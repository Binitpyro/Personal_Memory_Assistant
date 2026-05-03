"""
Main FastAPI application module for Personal Memory Assistant.
Handles API routing, dependency injection, and lifespan events.
"""

import asyncio
import ctypes
import logging
import os
import platform as plat
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app import state
from app.api.auth import auth_router
from app.api.debug import router as debug_router
from app.api.deps import db_manager, get_db, get_emb
from app.api.indexing import router as indexing_router
from app.api.insights import router as insights_router
from app.api.limiter import limiter
from app.api.models import models_router
from app.api.search import router as search_router
from app.api.system import router as system_router
from app.api.telemetry import router as telemetry_router
from app.config import settings
from app.project_constants import APP_VERSION
from app.storage.db import DatabaseManager

_BASE_DIR = Path(__file__).parent.parent
_REACT_DIR = _BASE_DIR / "static" / "react"
INDEX_HTML = "index.html"
_REACT_INDEX = _REACT_DIR / INDEX_HTML

# ── Logging Setup ─────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


# ── Internal Helpers ──────────────────────────────────────────────────
def _missing_frontend_response() -> HTMLResponse:
    return HTMLResponse(
        content=(
            "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8' />"
            "<meta name='viewport' content='width=device-width, initial-scale=1' />"
            "<title>PMA Frontend Missing</title>"
            "<style>body{font-family:Arial,sans-serif;background:#0f172a;color:#e5e7eb;"
            "display:grid;place-items:center;min-height:100vh;margin:0;padding:24px}"
            "main{max-width:560px;background:#111827;border:1px solid #334155;"
            "border-radius:16px;padding:28px;line-height:1.6}"
            "code{background:#1e293b;padding:2px 6px;border-radius:6px}</style></head>"
            "<body><main><h1>Frontend bundle not found</h1>"
            "<p>PMA could not find <code>static/react/index.html</code>.</p>"
            "<p>Build the frontend with <code>cd frontend && npm run build</code> or "
            "run the desktop shell with <code>npm run tauri dev</code>.</p>"
            "</main></body></html>"
        ),
        status_code=503,
    )


def health(db: DatabaseManager):
    """Shared payload for /health and /api/health."""
    emb = get_emb()
    model_ready = emb.model is not None
    db_ok = db.conn is not None
    status = "ok" if model_ready and db_ok else "degraded"
    return {
        "version": APP_VERSION,
        "status": status,
        "db": "connected" if db_ok else "disconnected",
        "model_ready": model_ready,
        "indexing": "idle",
        "split_brain_sync_status": state.split_brain_sync_status,
    }


def _log_admin_status():
    """Helper to log Windows Administrator status for NTFS MFT scanning."""
    if plat.system() == "Windows":
        try:
            # type: ignore[attr-defined]
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            is_admin = False

        if is_admin:
            logger.info("Running with Administrator privileges - NTFS MFT fast scanning enabled.")
        else:
            logger.warning(
                "╔══════════════════════════════════════════════════════════════╗\n"
                "║  NOT running as Administrator.                              ║\n"
                "║  NTFS MFT fast scanning is DISABLED (using slower scandir). ║\n"
                "║  Restart with 'Run as Administrator' for best performance.  ║\n"
                "╚══════════════════════════════════════════════════════════════╝"
            )


def _log_startup_info():
    """Logs backend and environment information on startup."""
    logger.info("PMA Backend Starting (v%s)", APP_VERSION)
    logger.info("Database: %s", settings.db_path)
    logger.info("Embedding Model: %s", settings.embedding_model)
    logger.info("LanceDB Cache: %s (Mode: %s)", settings.lancedb_persist_dir, settings.lancedb_mode)


# ── Lifespan & Background Tasks ───────────────────────────────────────


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    _log_startup_info()
    loop = asyncio.get_running_loop()

    # 1. Initialize core infrastructure
    logger.info("Initializing database...")
    await db_manager.connect()
    await db_manager.init_db(schema_path=settings.schema_path)

    _log_admin_status()

    # 2. Centralize Service Resolution (Modular Setup)
    from app.embeddings.service import EmbeddingService
    from app.indexing.service import IndexingService, progress
    from app.insights.service import InsightsService
    from app.search.retrieval import full_rag
    from app.vector_store.lancedb_client import LanceDBClient  # type: ignore

    state.indexing_service_cls = IndexingService
    state.progress_obj = progress
    state.full_rag_func = full_rag
    state.insights_service_cls = InsightsService

    # 3. Model & Cache readiness
    emb = EmbeddingService()
    logger.info("Starting background model load...")
    emb.load_model_background()

    lancedb_client = LanceDBClient(persist_directory=settings.lancedb_persist_dir)
    logger.info("Initializing LanceDB...")
    await loop.run_in_executor(None, lancedb_client.connect)

    async def _bg_preload_reranker_task():
        try:
            from app.search.reranker import preload_reranker

            await loop.run_in_executor(None, preload_reranker)
            logger.info("Reranker model loaded successfully.")
        except Exception as e:
            logger.debug("Reranker preload skipped or failed: %s", e)

    rerank_task = asyncio.create_task(_bg_preload_reranker_task())
    state.bg_tasks.add(rerank_task)
    rerank_task.add_done_callback(state.bg_tasks.discard)

    # 4. Background Maintenance Tasks
    sync_task = asyncio.create_task(_split_brain_sync(db_manager, lancedb_client, emb))
    state.bg_tasks.add(sync_task)
    sync_task.add_done_callback(state.bg_tasks.discard)

    cleanup_task = asyncio.create_task(_bg_startup_cleanup(db_manager, sync_task))
    state.bg_tasks.add(cleanup_task)
    cleanup_task.add_done_callback(state.bg_tasks.discard)

    vac_task = asyncio.create_task(_bg_auto_vacuum(db_manager))
    state.bg_tasks.add(vac_task)
    vac_task.add_done_callback(state.bg_tasks.discard)

    logger.info("Server ready (v%s)", APP_VERSION)
    yield

    # 5. Graceful Shutdown
    logger.info("Shutting down: cleaning up %d background tasks...", len(state.bg_tasks))
    for t in list(state.bg_tasks):
        t.cancel()

    if state.bg_tasks:
        await asyncio.gather(*state.bg_tasks, return_exceptions=True)

    await db_manager.close()
    logger.info("Shutdown complete.")


async def _split_brain_sync(db_manager, lancedb_client, emb_svc):
    if settings.lancedb_mode != "split_brain":
        state.split_brain_sync_status = "idle"
        return

    state.split_brain_sync_status = "syncing"
    logger.info("Split-brain Mode: Starting vector sync into LanceDB host cache…")
    loop = asyncio.get_running_loop()

    try:
        import numpy as np

        # Phase A: Back-fill
        conn = db_manager._get_conn()
        async with conn.execute("SELECT COUNT(*) FROM chunk_embeddings") as cur:
            emb_count = (await cur.fetchone())[0]
        async with conn.execute("SELECT COUNT(*) FROM chunks") as cur:
            chunk_count = (await cur.fetchone())[0]

        if emb_count == 0 and chunk_count > 0:
            logger.warning("Split-brain: Running one-time back-fill migration…")
            if emb_svc.model is None:
                for _ in range(60):
                    await asyncio.sleep(0.5)
                    if emb_svc.model is not None:
                        break
                if emb_svc.model is None:
                    raise RuntimeError("Embedding model not ready for back-fill.")

            backfill_batch = 5000
            bf_offset = 0
            bf_total = 0
            while True:
                async with conn.execute(
                    "SELECT c.id, zlib_decompress(c.text_preview) "
                    "FROM chunks c "
                    "LEFT JOIN chunk_embeddings ce ON ce.chunk_id = c.id "
                    "WHERE ce.chunk_id IS NULL "
                    "LIMIT ? OFFSET ?",
                    (backfill_batch, bf_offset),
                ) as cur:
                    rows = await cur.fetchall()
                if not rows:
                    break
                ids_batch = [r[0] for r in rows]
                texts_batch = [r[1] or "" for r in rows]

                def _embed_task(t=texts_batch):
                    return emb_svc.embed_texts_sync(t)

                embeddings = await loop.run_in_executor(None, _embed_task)
                blob_data = [
                    (chunk_id, np.array(emb, dtype=np.float16).tobytes())
                    for chunk_id, emb in zip(ids_batch, embeddings, strict=False)
                ]
                await db_manager.insert_chunk_embeddings_bulk(blob_data)
                bf_total += len(blob_data)
                bf_offset += backfill_batch
                if len(rows) < backfill_batch:
                    break
            logger.info("Split-brain back-fill complete.")

        # Phase B: O(1) Memory Differential Sync
        max_ldb_id = await loop.run_in_executor(None, lancedb_client.get_max_id, "pma_chunks")
        logger.info("Split-brain: Syncing missing chunks after ID %d...", max_ldb_id)

        batch_size = 5000
        last_id = max_ldb_id
        total_synced = 0

        while True:
            sqlite_data = await db_manager.get_all_chunk_data_for_sync(
                limit=batch_size, last_id=last_id
            )
            if not sqlite_data:
                break

            m_ids = [row["chunk_id"] for row in sqlite_data]
            m_embs = [np.frombuffer(row["embedding"], dtype=np.float16) for row in sqlite_data]
            m_metas = [
                {
                    "chunk_id": row["chunk_id"],
                    "file_path": row["file_path"],
                    "folder_tag": row["folder_tag"],
                }
                for row in sqlite_data
            ]
            await lancedb_client.add_documents(m_ids, m_embs, m_metas)
            total_synced += len(sqlite_data)
            last_id = max(int(row["chunk_id"]) for row in sqlite_data)

            if len(sqlite_data) < batch_size:
                break

        state.split_brain_sync_status = "done"
        logger.info("Split-brain sync complete. %d new vectors cached.", total_synced)
    except Exception as e:
        state.split_brain_sync_status = "error"
        logger.error("Split-brain sync failed: %s", e)


async def _bg_startup_cleanup(db_manager, sync_task):
    """Fire-and-forget: remove stale file records from the DB."""
    try:
        await sync_task
        await asyncio.sleep(5)
        removed = await db_manager.cleanup_stale_files()
        if removed:
            logger.info("Background startup: cleared %d deleted file(s) from index.", len(removed))
    except Exception as e:
        logger.warning("Background startup cleanup error: %s", e)


async def _bg_auto_vacuum(db_manager):
    """Auto-vacuum if DB hasn't been vacuumed in 7 days and is > 500 MB."""
    try:
        await asyncio.sleep(10)
        db_size = os.path.getsize(settings.db_path)
        if db_size < 500 * 1024 * 1024:
            return
        marker = Path("data/.last_vacuum")
        if marker.exists() and (time.time() - marker.stat().st_mtime) / 86400 < 7:
            return
        logger.info("Auto-vacuum: running background VACUUM…")
        await db_manager.vacuum()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        logger.info("Auto-vacuum complete.")
    except Exception as e:
        logger.warning("Auto-vacuum error: %s", e)


# ── FastAPI App Instance ──────────────────────────────────────────────

app = FastAPI(title="Personal Memory Assistant", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:*",
        "https://localhost:*",
        "tauri://localhost",
        "https://tauri.localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_and_telemetry_middleware(request: Request, call_next):
    # 1. Enforce Local Access Token if running in desktop mode
    expected_token = os.environ.get("X_LOCAL_ACCESS_TOKEN")
    if expected_token and request.url.path.startswith("/api/") and request.method != "OPTIONS":
        provided_token = request.headers.get("X-Local-Access-Token")
        if not provided_token:
            provided_token = request.query_params.get("token")

        if not provided_token or not secrets.compare_digest(provided_token, expected_token):
            return JSONResponse(status_code=401, content={"error": "Unauthorized local access."})

    # 2. Telemetry and Security Headers
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id

    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # P10-5: Defense-in-depth headers
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    if request.url.path not in ("/api/health", "/api/index/progress-stream"):
        logger.info(
            "[%s] %s %s → %d (%.0fms)",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422, content={"error": "Validation error", "detail": exc.errors()}
    )


api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(models_router)
api_router.include_router(indexing_router, prefix="/index", tags=["indexing"])
api_router.include_router(search_router, prefix="/query", tags=["search"])
api_router.include_router(insights_router, tags=["insights"])
api_router.include_router(system_router, tags=["system"])
api_router.include_router(telemetry_router)
api_router.include_router(debug_router)


@api_router.get("/health")
def api_health(db: DatabaseManager = Depends(get_db)):
    return health(db)


app.include_router(api_router, prefix="/api")


@app.get("/health")
def health_root(db: DatabaseManager = Depends(get_db)):
    return health(db)


@app.get("/")
async def root(request: Request):
    if _REACT_INDEX.exists():
        return FileResponse(_REACT_INDEX)
    return _missing_frontend_response()


@app.get("/{full_path:path}")
async def spa_catch_all(request: Request, full_path: str):
    candidate = _REACT_DIR / full_path
    if candidate.exists() and candidate.is_file():
        return FileResponse(candidate)

    if "text/html" in request.headers.get("accept", ""):
        if _REACT_INDEX.exists():
            return FileResponse(_REACT_INDEX)
        return _missing_frontend_response()
    return JSONResponse(status_code=404, content={"error": "Not found"})


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", settings.port))
    logger.info(f"Starting server on port {port}...")
    uvicorn.run("app.main:app", host=settings.host, port=port, log_level="info")
