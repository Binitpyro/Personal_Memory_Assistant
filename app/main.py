"""
Main FastAPI application module for Personal Memory Assistant.
Handles API routing, dependency injection, and lifespan events.
"""

import asyncio
import ctypes
import importlib.metadata
import json
import logging
import os
import platform as plat
import shutil
import string
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Optional, Tuple, Dict

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.insights.unreal_import import parse_unreal_metadata
from app.storage.db import DatabaseManager
from app.utils.metrics import metrics_tracker

_BASE_DIR = Path(__file__).parent.parent
_REACT_DIR = _BASE_DIR / "static" / "react"
INDEX_HTML = "index.html"
_REACT_INDEX = _REACT_DIR / INDEX_HTML
templates = Jinja2Templates(directory="templates")

_indexing_service_cls: Any = None
_progress_obj: Any = None
_full_rag_func: Any = None
_insights_service_cls: Any = None

_static_asset_version_cache: dict[str, tuple[int, str]] = {}
_file_tree_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_insights_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_CACHE_TTL = 10  # seconds
_bg_tasks: set[asyncio.Task] = set()

APP_VERSION = "0.0.55"

def _versioned_static_url(asset_name: str) -> str:
    # Look in the base static directory for legacy assets
    asset_path = _BASE_DIR / "static" / asset_name
    try:
        mtime_ns = asset_path.stat().st_mtime_ns
    except OSError:
        return f"/static/{asset_name}"

    cached = _static_asset_version_cache.get(asset_name)
    if cached and cached[0] == mtime_ns:
        return cached[1]

    version = format(mtime_ns, "x")
    url = f"/static/{asset_name}?v={version}"
    _static_asset_version_cache[asset_name] = (mtime_ns, url)
    return url

def _ensure_indexing() -> Tuple[Any, Any]:
    global _indexing_service_cls, _progress_obj
    if _indexing_service_cls is None:
        from app.indexing.service import IndexingService as _IS
        from app.indexing.service import progress as _p
        _indexing_service_cls = _IS
        _progress_obj = _p
    return _indexing_service_cls, _progress_obj

def _ensure_rag():
    global _full_rag_func
    if _full_rag_func is None:
        from app.search.retrieval import full_rag as _fr
        _full_rag_func = _fr
    return _full_rag_func

def _ensure_insights():
    global _insights_service_cls
    if _insights_service_cls is None:
        from app.insights.service import InsightsService as _IS
        _insights_service_cls = _IS
    return _insights_service_cls

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

db_manager = DatabaseManager(db_path=settings.db_path)

_embedding_service = None
_chroma_client = None
_llm_client = None

def _get_embedding_service():
    global _embedding_service
    if _embedding_service is None:
        from app.embeddings.service import EmbeddingService
        _embedding_service = EmbeddingService()
    return _embedding_service

def _get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        from app.vector_store.chroma_client import ChromaClient
        _chroma_client = ChromaClient(persist_directory=settings.chroma_persist_dir)
    return _chroma_client

def _get_llm_client():
    global _llm_client
    if _llm_client is None:
        from app.search.llm_client import LLMClient
        _llm_client = LLMClient()
    return _llm_client

async def get_db():
    if not db_manager.conn:
        await db_manager.connect()
    return db_manager

def get_emb(): return _get_embedding_service()
def get_chroma(): return _get_chroma_client()
def get_llm(): return _get_llm_client()

@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    loop = asyncio.get_running_loop()
    logger.info("Initializing database...")
    await db_manager.connect()
    await db_manager.init_db(schema_path=settings.schema_path)
    # ── Admin privilege check for NTFS fast scanning ──
    if plat.system() == "Windows":
        try:
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            is_admin = False
        if is_admin:
            logger.info("Running with Administrator privileges — NTFS MFT fast scanning enabled.")
        else:
            logger.warning(
                "╔══════════════════════════════════════════════════════════════╗\n"
                "║  NOT running as Administrator.                              ║\n"
                "║  NTFS MFT fast scanning is DISABLED (using slower scandir). ║\n"
                "║  Restart with 'Run as Administrator' for best performance.  ║\n"
                "╚══════════════════════════════════════════════════════════════╝"
            )
    emb = _get_embedding_service()
    logger.info("Starting background model load...")
    emb.load_model_background()
    chroma = _get_chroma_client()
    logger.info("Initializing Chroma...")
    await loop.run_in_executor(None, chroma.connect)
    def _bg_preload_reranker():
        try:
            from app.search.reranker import preload_reranker
            preload_reranker()
            logger.info("Reranker model loaded successfully.")
        except Exception as e:
            logger.debug("Reranker preload skipped: %s", e)
    loop.run_in_executor(None, _bg_preload_reranker)

    async def _bg_startup_cleanup():
        """Fire-and-forget: remove stale file records from the DB."""
        try:
            await asyncio.sleep(5)  # let the server fully boot first
            removed = await db_manager.cleanup_stale_files()
            if removed:
                logger.info("Background startup: cleared %d deleted file(s) from index.", removed)
        except Exception as e:
            logger.warning("Background startup cleanup error: %s", e)

    task = asyncio.create_task(_bg_startup_cleanup())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

    logger.info("Server ready (v%s)", APP_VERSION)
    yield
    logger.info("Shutting down...")
    await db_manager.close()

app = FastAPI(title="Personal Memory Assistant", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    # Enforce Local Access Token if running in desktop mode with a token
    expected_token = os.environ.get("PMA_LOCAL_TOKEN")
    if expected_token and request.url.path.startswith("/api/"):
        # Allow OPTIONS for CORS preflight
        if request.method != "OPTIONS":
            provided_token = request.headers.get("X-Local-Access-Token")
            # Fallback to query param for things like EventSource
            if not provided_token:
                provided_token = request.query_params.get("token")
            
            if provided_token != expected_token:
                return JSONResponse(status_code=401, content={"error": "Unauthorized local access."})

    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - t0) * 1000
    response.headers["X-Request-ID"] = request_id
    if request.url.path not in ("/api/health", "/api/index/progress-stream"):
        logger.info("[%s] %s %s → %d (%.0fms)", request_id, request.method, request.url.path, response.status_code, elapsed)
    return response

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "Validation error", "detail": exc.errors()}
    )

api_router = APIRouter()

from app.api.auth import auth_router
from app.api.models import models_router
from app.api.indexing import router as indexing_router
from app.api.search import router as search_router
from app.api.insights import router as insights_router
from app.api.system import router as system_router

api_router.include_router(auth_router)
api_router.include_router(models_router)
api_router.include_router(indexing_router, prefix="/index", tags=["indexing"])
api_router.include_router(search_router, prefix="/query", tags=["search"])
api_router.include_router(insights_router, tags=["insights"])
api_router.include_router(system_router, tags=["system"])

app.include_router(api_router, prefix="/api")

@app.get("/health")
async def health_root(db: DatabaseManager = Depends(get_db)):
    return await health(db)

@app.get("/")
async def root(request: Request):
    if _REACT_INDEX.exists():
        return FileResponse(_REACT_INDEX)
    return templates.TemplateResponse(
        request, 
        INDEX_HTML, 
        {"app_version": APP_VERSION, "pma_css_url": _versioned_static_url("pma.css"), "pma_js_url": _versioned_static_url("pma.js")}
    )

@app.get("/{full_path:path}")
async def spa_catch_all(request: Request, full_path: str):
    candidate = _REACT_DIR / full_path
    if candidate.exists() and candidate.is_file():
        return FileResponse(candidate)
    
    if "text/html" in request.headers.get("accept", ""):
        if _REACT_INDEX.exists():
            return FileResponse(_REACT_INDEX)
        return templates.TemplateResponse(
            request, 
            INDEX_HTML, 
            {"app_version": APP_VERSION, "pma_css_url": _versioned_static_url("pma.css"), "pma_js_url": _versioned_static_url("pma.js")}
        )
    return JSONResponse(status_code=404, content={"error": "Not found"})
