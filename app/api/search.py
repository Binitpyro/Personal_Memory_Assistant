import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.api.deps import ensure_rag, get_db, get_emb, get_lancedb, get_llm, get_planner
from app.api.limiter import limiter
from app.storage.db import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()

query_semaphore = asyncio.Semaphore(5)

# How long the stream may go quiet before we emit a keepalive frame. This is a
# liveness signal for the client, not a deadline on the model: a local 3 tok/s
# provider routinely spends longer than this on retrieval plus prompt
# processing before the first content token.
_KEEPALIVE_SECONDS = 15.0

_STREAM_END = object()


async def _next_chunk_or_end(agen):
    """Await one chunk from *agen*, mapping exhaustion onto a sentinel.

    Returning a sentinel rather than letting ``StopAsyncIteration`` escape keeps
    this safe to wrap in a Task, and lets the caller tell "generator finished"
    apart from "generator was torn down".
    """
    try:
        return await anext(agen)
    except StopAsyncIteration:
        return _STREAM_END


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    file_type: str | None = Field(None, max_length=50)
    folder_tag: str | None = Field(None, max_length=100)
    mode: str | None = Field(None, max_length=50)
    forced_chunk_ids: list[int] | None = Field(None, max_length=500)
    override_provider: str | None = Field(None, max_length=50)
    override_model: str | None = Field(None, max_length=150)
    # max_length is a context-budget guard, not a DoS fix: the request body is
    # fully parsed into memory before any validator runs. 50 items at the
    # 10,000-char ceiling below is already more history than a local model's
    # window can absorb.
    history: list[dict[str, str]] | None = Field(
        None, max_length=50
    )  # List of {"role": "user/assistant", "content": "..."}

    @field_validator("history")
    @classmethod
    def validate_history(cls, v):
        if v is None:
            return v
        if not isinstance(v, list):
            raise ValueError("history must be a list")
        for item in v:
            if not isinstance(item, dict):
                raise ValueError("history items must be dictionaries")
            # validate that keys are strings and only valid keys
            keys = set(item.keys())
            if not keys.issubset({"role", "content"}) or not keys:
                raise ValueError("history items must contain only 'role' and 'content' keys")
            # validate keys and values are strings
            for key, val in item.items():
                if not isinstance(key, str) or not isinstance(val, str):
                    raise ValueError("history item keys and values must be strings")
            # valid roles (user, assistant, system)
            role = item.get("role")
            if role not in ("user", "assistant", "system"):
                raise ValueError("history item role must be one of: 'user', 'assistant', 'system'")
            # content length limit (10000 characters)
            content = item.get("content", "")
            if len(content) > 10000:
                raise ValueError("history item content length must not exceed 10000 characters")
        return v

    @property
    def validated_question(self) -> str:
        return self.question.strip()


@router.post("")
@limiter.limit("30/minute")
async def query(
    request: Request,
    payload: QueryRequest,
    background_tasks: BackgroundTasks,
    db: DatabaseManager = Depends(get_db),
    emb=Depends(get_emb),
    lancedb_client=Depends(get_lancedb),
    llm=Depends(get_llm),
    planner=Depends(get_planner),
):
    q = payload.validated_question
    if not q:
        return JSONResponse(
            status_code=422, content={"error": "Question cannot be empty or whitespace only"}
        )
    history = payload.history or []
    full_rag = ensure_rag()

    try:
        async with query_semaphore:
            res = await full_rag(
                query=q,
                db=db,
                embedding_service=emb,
                lancedb_client=lancedb_client,
                llm_client=llm,
                planner=planner,
                file_type=payload.file_type,
                folder_tag=payload.folder_tag,
                mode=payload.mode,
                history=history,
            )

        async def _bg_save_query():
            query_id = await db.save_query(
                q,
                res.get("answer", ""),
                int(res.get("retrieved_count", 0)),
                float(res.get("latency_ms", 0.0)),
            )
            telemetry = res.get("_telemetry")
            if telemetry:
                await db.save_telemetry(
                    query_id=query_id,
                    time_to_first_token_ms=0.0,
                    mode_selected=payload.mode,
                    model_class=telemetry.get("model_class"),
                    context_tokens_budget=telemetry.get("context_tokens_budget"),
                    context_tokens_used=telemetry.get("context_tokens_used"),
                    chunks_included=telemetry.get("chunks_included"),
                    chunks_dropped=telemetry.get("chunks_dropped"),
                )

        background_tasks.add_task(_bg_save_query)

        return res
    except Exception as e:
        logger.exception("Query failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/stream")
async def query_stream(
    request: QueryRequest,
    http_request: Request,
    db: DatabaseManager = Depends(get_db),
    emb=Depends(get_emb),
    lancedb_client=Depends(get_lancedb),
    llm=Depends(get_llm),
    planner=Depends(get_planner),
):
    q = request.validated_question
    if not q:
        return JSONResponse(
            status_code=422, content={"error": "Question cannot be empty or whitespace only"}
        )
    history = request.history or []
    from app.search.retrieval import stream_rag

    async def stream_results():
        agen = None
        pending = None
        try:
            async with query_semaphore:
                agen = stream_rag(
                    query=q,
                    db=db,
                    embedding_service=emb,
                    lancedb_client=lancedb_client,
                    llm_client=llm,
                    planner=planner,
                    file_type=request.file_type,
                    folder_tag=request.folder_tag,
                    mode=request.mode,
                    forced_chunk_ids=request.forced_chunk_ids,
                    history=history,
                    override_provider=request.override_provider,
                    override_model=request.override_model,
                )

                while True:
                    if pending is None:
                        pending = asyncio.ensure_future(_next_chunk_or_end(agen))

                    # asyncio.wait leaves an unfinished task running. wait_for
                    # does not: it cancels what it is waiting on, which threw
                    # CancelledError into this generator at its suspension
                    # point and left it unusable, so the next anext() raised
                    # StopAsyncIteration and the answer was silently truncated
                    # at the first 15s gap.
                    done, _ = await asyncio.wait({pending}, timeout=_KEEPALIVE_SECONDS)

                    if not done:
                        if await http_request.is_disconnected():
                            logger.info("Client disconnected; abandoning generation.")
                            return
                        yield json.dumps({"type": "ping"}) + "\n"
                        continue

                    chunk = pending.result()
                    pending = None
                    if chunk is _STREAM_END:
                        break
                    yield json.dumps(chunk) + "\n"

            yield json.dumps({"type": "done"}) + "\n"
        except Exception as e:
            logger.exception("Stream errored: %s", e)
            yield json.dumps({"type": "error", "text": str(e)}) + "\n"
        finally:
            # Let the cancelled task finish unwinding before closing the
            # generator: aclose() on a generator with an in-flight anext()
            # raises "already running".
            if pending is not None:
                pending.cancel()
                with contextlib.suppress(BaseException):
                    await pending
            if agen is not None:
                with contextlib.suppress(Exception):
                    await agen.aclose()

    return StreamingResponse(stream_results(), media_type="application/x-ndjson")


@router.get("/history")
async def query_history(limit: int = 20, db: DatabaseManager = Depends(get_db)):
    try:
        rows = await db.get_query_history(limit)
        return {
            "history": [
                {"id": h["id"], "question": h["question"], "timestamp": h.get("created_at")}
                for h in rows
            ]
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/history/clear")
async def clear_query_history(
    db: DatabaseManager = Depends(get_db), lancedb_client=Depends(get_lancedb)
):
    try:
        await db.clear_query_history()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    # The LanceDB semantic cache holds verbatim question *and* answer text and
    # used to survive this entirely, so clearing history left every question on
    # disk. Reported rather than swallowed: a user who clears their history
    # needs to know if any of it is still there.
    try:
        await lancedb_client.clear_query_cache()
    except Exception as e:
        logger.warning("Semantic query cache not cleared: %s", e)
        return {
            "message": "Query history cleared",
            "semantic_cache_cleared": False,
            "warning": str(e),
        }
    return {"message": "Query history cleared", "semantic_cache_cleared": True}
