import json
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import (
    get_db, get_emb, get_chroma, get_llm, ensure_rag
)
from app.storage.db import DatabaseManager
from app.api.limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter()

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    file_type: Optional[str] = Field(None)
    folder_tag: Optional[str] = Field(None)
    history: Optional[List[Dict[str, str]]] = Field(None) # List of {"role": "user/assistant", "content": "..."}
    @property
    def validated_question(self) -> str: return self.question.strip()

@router.post("")
@limiter.limit("30/minute")
async def query(
    request: Request,
    payload: QueryRequest, 
    background_tasks: BackgroundTasks, 
    db: DatabaseManager = Depends(get_db), 
    emb=Depends(get_emb), 
    chroma=Depends(get_chroma), 
    llm=Depends(get_llm)
):
    q = payload.validated_question
    history = payload.history or []
    full_rag = ensure_rag()
    
    try:
        res = await full_rag(q, db, emb, chroma, llm, request.file_type, request.folder_tag, history)

        async def _bg_save_query():
            await db.save_query(
                q,
                res.get("answer", ""),
                int(res.get("retrieved_count", 0)),
                float(res.get("latency_ms", 0.0)),
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
    chroma=Depends(get_chroma), 
    llm=Depends(get_llm)
):
    q = request.validated_question
    history = request.history or []
    from app.search.retrieval import stream_rag
    
    async def stream_results():
        try:
            async for chunk in stream_rag(q, db, emb, chroma, llm, request.file_type, request.folder_tag, history):
                yield json.dumps(chunk) + "\n"
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

@router.post("/debug/query-plan")
async def debug_query_plan(payload: QueryRequest):
    from app.config import settings
    if not settings.dev_mode:
        return JSONResponse(status_code=403, content={"error": "Dev mode is disabled."})
    
    from app.search.planner import QueryPlanner
    planner = QueryPlanner()
    plan = planner.plan(payload.validated_question)
    return {
        "question": payload.validated_question,
        "intents": plan.intents,
        "keywords": plan.keywords,
        "mode": plan.mode.value
    }
