"""
Shared FastAPI dependencies for Personal Memory Assistant.
"""

from typing import Any

from app.config import settings
from app.storage.db import DatabaseManager

_db_manager: DatabaseManager | None = None
embedding_service = None
lancedb_client = None
llm_client = None

_indexing_service_cls: Any = None
_progress_obj: Any = None
_full_rag_func: Any = None
_insights_service_cls: Any = None


async def get_db() -> DatabaseManager:
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager(db_path=settings.db_path)
    if not _db_manager.conn:
        await _db_manager.connect()
    return _db_manager


def get_emb():
    global embedding_service
    if embedding_service is None:
        from app.embeddings.service import EmbeddingService

        embedding_service = EmbeddingService()
    return embedding_service


def get_lancedb():
    global lancedb_client
    if lancedb_client is None:
        from app.vector_store.lancedb_client import LanceDBClient  # type: ignore

        lancedb_client = LanceDBClient(persist_directory=settings.lancedb_persist_dir)
    return lancedb_client


def get_llm():
    global llm_client
    if llm_client is None:
        from app.search.llm_client import LLMClient

        llm_client = LLMClient()
    return llm_client


def ensure_indexing() -> tuple[Any, Any]:
    from app import state

    if state.indexing_service_cls is None:
        from app.indexing.service import IndexingService, progress

        state.indexing_service_cls = IndexingService
        state.progress_obj = progress
    return state.indexing_service_cls, state.progress_obj


def ensure_rag():
    from app import state

    if state.full_rag_func is None:
        from app.search.retrieval import full_rag

        state.full_rag_func = full_rag
    return state.full_rag_func


def ensure_insights():
    from app import state

    if state.insights_service_cls is None:
        from app.insights.service import InsightsService

        state.insights_service_cls = InsightsService
    return state.insights_service_cls
