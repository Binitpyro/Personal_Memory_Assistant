"""
Shared FastAPI dependencies for Personal Memory Assistant.
"""
from typing import Any, Tuple

from app.storage.db import DatabaseManager
from app.config import settings

db_manager = DatabaseManager(db_path=settings.db_path)
embedding_service = None
chroma_client = None
llm_client = None

_indexing_service_cls: Any = None
_progress_obj: Any = None
_full_rag_func: Any = None
_insights_service_cls: Any = None

async def get_db() -> DatabaseManager:
    if not db_manager.conn:
        await db_manager.connect()
    return db_manager

def get_emb():
    global embedding_service
    if embedding_service is None:
        from app.embeddings.service import EmbeddingService
        embedding_service = EmbeddingService()
    return embedding_service

def get_chroma():
    global chroma_client
    if chroma_client is None:
        from app.vector_store.chroma_client import ChromaClient
        chroma_client = ChromaClient(persist_directory=settings.chroma_persist_dir)
    return chroma_client

def get_llm():
    global llm_client
    if llm_client is None:
        from app.search.llm_client import LLMClient
        llm_client = LLMClient()
    return llm_client

def ensure_indexing() -> Tuple[Any, Any]:
    global _indexing_service_cls, _progress_obj
    if _indexing_service_cls is None:
        from app.indexing.service import IndexingService as _IS
        from app.indexing.service import progress as _p
        _indexing_service_cls = _IS
        _progress_obj = _p
    return _indexing_service_cls, _progress_obj

def ensure_rag():
    global _full_rag_func
    if _full_rag_func is None:
        from app.search.retrieval import full_rag as _fr
        _full_rag_func = _fr
    return _full_rag_func

def ensure_insights():
    global _insights_service_cls
    if _insights_service_cls is None:
        from app.insights.service import InsightsService as _IS
        _insights_service_cls = _IS
    return _insights_service_cls
