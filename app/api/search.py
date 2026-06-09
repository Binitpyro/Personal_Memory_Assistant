import json
import logging
import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import ensure_rag, get_db, get_emb, get_lancedb, get_llm, get_planner
from app.api.limiter import limiter
from app.storage.db import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()

query_semaphore = asyncio.Semaphore(5)


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    file_type: str | None = Field(None)
    folder_tag: str | None = Field(None)
    mode: str | None = Field(None)
    forced_chunk_ids: list[int] | None = Field(None)
    history: list[dict[str, str]] | None = Field(
        None
    )  # List of {"role": "user/assistant", "content": "..."}

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
    history = payload.history or []
    full_rag = ensure_rag()

    try:
        async with query_semaphore:
            res = await full_rag(
                q, db, emb, lancedb_client, llm, planner, payload.file_type, payload.folder_tag, payload.mode, history
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
    db: DatabaseManager = Depends(get_db),
    emb=Depends(get_emb),
    lancedb_client=Depends(get_lancedb),
    llm=Depends(get_llm),
    planner=Depends(get_planner),
):
    q = request.validated_question
    history = request.history or []
    from app.search.retrieval import stream_rag

    async def stream_results():
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
                )
                while True:
                    try:
                        chunk = await asyncio.wait_for(anext(agen), timeout=15.0)
                        yield json.dumps(chunk) + "\n"
                    except asyncio.TimeoutError:
                        yield json.dumps({"type": "ping"}) + "\n"
        except StopAsyncIteration as e:
            payload = {"type": "done"}
            if e.value:
                payload.update(e.value)
            yield json.dumps(payload) + "\n"
        except Exception as e:
            logger.exception("Stream errored: %s", e)
            yield json.dumps({"type": "error", "text": str(e)}) + "\n"

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
async def clear_query_history(db: DatabaseManager = Depends(get_db)):
    try:
        await db.clear_query_history()
        return {"message": "Query history cleared"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
