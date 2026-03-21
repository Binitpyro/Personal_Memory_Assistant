import json
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import (
    get_db, get_emb, get_chroma, get_llm, ensure_rag
)
from app.storage.db import DatabaseManager

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
async def query(
    request: QueryRequest, 
    background_tasks: BackgroundTasks, 
    db: DatabaseManager = Depends(get_db), 
    emb=Depends(get_emb), 
    chroma=Depends(get_chroma), 
    llm=Depends(get_llm)
):
    q = request.validated_question
    history = request.history or []
    full_rag = ensure_rag()
    
    try:
        from app.utils.metrics import metrics_tracker
        res = await full_rag(q, db, chroma, emb, llm, request.file_type, request.folder_tag, history)
        
        async def _bg_increment():
            await metrics_tracker.increment_search()
            if res.get("mode") == "fast_path":
                await metrics_tracker.increment_fast_path()
            else:
                await metrics_tracker.increment_rag()
        background_tasks.add_task(_bg_increment)
        
        async def _bg_save_query():
            await db.insert_query_history(q)
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
            async for chunk in stream_rag(q, db, chroma, emb, llm, request.file_type, request.folder_tag, history):
                yield json.dumps(chunk) + "\n"
        except Exception as e:
            logger.exception("Stream errored: %s", e)
            yield json.dumps({"type": "error", "text": str(e)}) + "\n"
            
    return StreamingResponse(stream_results(), media_type="application/x-ndjson")

@router.get("/history")
async def query_history(limit: int = 20, db: DatabaseManager = Depends(get_db)):
    try:
        history = await db.get_query_history(limit)
        return {"history": [{"id": row[0], "question": row[1], "timestamp": row[2]} for row in history]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/history/clear")
async def clear_query_history(db: DatabaseManager = Depends(get_db)):
    try:
        await db.clear_query_history()
        return {"message": "Query history cleared"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
