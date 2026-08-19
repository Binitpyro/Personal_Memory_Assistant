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
_planner = None
_ocr_manager = None

_indexing_service_cls: Any = None
_progress_obj: Any = None
_full_rag_func: Any = None
_insights_service_cls: Any = None


def get_planner():
    global _planner
    if _planner is None:
        from app.search.planner import QueryPlanner

        _planner = QueryPlanner()
    return _planner


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


async def get_ocr():
    """The OCR manager singleton.

    Async because it needs a *connected* DatabaseManager. Normally built once
    during lifespan; constructing it here covers tests that touch an OCR
    endpoint without going through startup.
    """
    global _ocr_manager
    if _ocr_manager is None:
        from app.ocr.manager import OcrManager

        _ocr_manager = OcrManager(await get_db(), get_emb(), get_lancedb())
    return _ocr_manager


def get_ocr_if_ready():
    """Return the OCR manager only if something already built it. Never builds one.

    `get_ocr()` constructs on demand, and constructing pulls in `get_db()` -
    the module-global `DatabaseManager`. Calling that from inside the indexing
    pipeline is wrong twice over: it is a service reaching forward into the API
    layer, and in any non-FastAPI entry point (the eval harness, `scripts/`)
    it opens a **second** DatabaseManager on the same file that nobody closes.
    aiosqlite's `Connection` worker threads are not daemons, so one unclosed
    connection blocks `threading._shutdown` forever - the process finishes all
    its work, commits, and then sits at zero CPU until it is killed.

    The app builds this during lifespan startup (`app/main.py`), so the in-app
    path is unchanged. Everywhere else there is no OCR worker to kick anyway,
    and the durable `ocr_queue` row has already been written by the caller.
    """
    return _ocr_manager


async def close_all() -> None:
    """Release the module-global DatabaseManager. Idempotent.

    For entry points that are not the FastAPI app: the lifespan closes this
    object itself (`app/main.py`), but a script or the eval harness builds its
    own manager and would otherwise leave this one open. Deliberately does not
    touch `_ocr_manager` - stopping it means shutting a worker subprocess down,
    which is the lifespan's job, not a teardown helper's.
    """
    global _db_manager

    if _db_manager is not None:
        try:
            await _db_manager.close()
        finally:
            _db_manager = None


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
