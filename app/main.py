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
import tkinter as tk
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from tkinter import filedialog
from typing import Any, List, Optional, Tuple

from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.insights.unreal_import import parse_unreal_metadata
from app.storage.db import DatabaseManager

_indexing_service_cls: Any = None
_progress_obj: Any = None
_full_rag_func: Any = None
_insights_service_cls: Any = None
_static_asset_version_cache: dict[str, tuple[int, str]] = {}
_file_tree_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_insights_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_CACHE_TTL = 10  # seconds

try:
    APP_VERSION = importlib.metadata.version("personal-memory-assistant")
except importlib.metadata.PackageNotFoundError:
    APP_VERSION = "1.2.0"


def _versioned_static_url(asset_name: str) -> str:
    asset_rel = Path("static") / asset_name
    try:
        mtime_ns = asset_rel.stat().st_mtime_ns
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

@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    loop = asyncio.get_running_loop()

    logger.info("Initializing database...")
    await db_manager.init_db(schema_path=settings.schema_path)

    emb = _get_embedding_service()
    logger.info("Starting background model load...")
    emb.load_model_background()

    chroma = _get_chroma_client()
    logger.info("Initializing Chroma...")
    await loop.run_in_executor(None, chroma.connect)

    # Preload reranker model to eliminate cold-start latency on first query
    try:
        from app.search.reranker import preload_reranker
        preload_reranker()
        logger.info("Reranker preload started in background.")
    except Exception as e:
        logger.debug("Reranker preload skipped: %s", e)

    logger.info("Server ready  (model loading in background)")
    yield
    logger.info("Shutting down...")
    await db_manager.close()

app = FastAPI(title="Personal Memory Assistant", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - t0) * 1000
    response.headers["X-Request-ID"] = request_id
    if request.url.path.startswith("/static/"):
        if request.query_params.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=3600"
    if request.url.path not in ("/health", "/index/progress-stream"):
        logger.info(
            "[%s] %s %s → %d (%.0fms)",
            request_id, request.method, request.url.path,
            response.status_code, elapsed,
        )
    return response

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    errors = exc.errors()
    messages = []
    for err in errors:
        loc = " → ".join(str(l) for l in err.get("loc", []))
        messages.append(f"{loc}: {err.get('msg', 'invalid')}")
    return JSONResponse(
        status_code=422,
        content={"error": "Validation error", "details": messages},
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "An internal server error occurred. Please try again later."},
    )

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def get_db():
    return db_manager

def get_emb():
    return _get_embedding_service()

def get_chroma():
    return _get_chroma_client()

def get_llm():
    return _get_llm_client()

class IndexRequest(BaseModel):
    folders: List[str] = Field(..., max_length=50)

    @property
    def validated_folders(self) -> List[str]:
        """Returns cleaned, non-empty and validated folder paths."""
        cleaned = []
        for f in self.folders:
            p = f.strip().strip('"').strip("'")
            if not p:
                continue
            resolved = os.path.realpath(os.path.normpath(p))
            if os.path.isdir(resolved):
                cleaned.append(resolved)
            else:
                logger.warning("Skipping invalid/non-existent folder: %s", p)
        return cleaned

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    file_type: Optional[str] = Field(None, description="Filter by file extension, e.g. '.py'")
    folder_tag: Optional[str] = Field(None, description="Filter by folder tag")

    @property
    def validated_question(self) -> str:
        return self.question.strip()


class UnrealImportRequest(BaseModel):
    json_path: str = Field(..., min_length=3, description="Path to Unreal metadata JSON export")
    folder_tag: Optional[str] = Field(None, description="Optional folder/project tag override")

    @property
    def validated_json_path(self) -> str:
        return os.path.realpath(os.path.normpath(self.json_path.strip().strip('"').strip("'")))

@app.get("/health")
async def health(db: DatabaseManager = Depends(get_db)):
    db_ok = await db.is_healthy()
    emb = _get_embedding_service()
    _, progress = _ensure_indexing()
    return {
        "version": APP_VERSION,
        "status": "ok" if db_ok else "degraded",
        "db": "connected" if db_ok else "error",
        "model_ready": emb.is_ready,
        "indexing": progress.status,
    }

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_version": APP_VERSION,
            "pma_css_url": _versioned_static_url("pma.css"),
            "pma_js_url": _versioned_static_url("pma.js"),
        },
    )

@app.post("/index/start")
async def index_start(
    request: IndexRequest,
    background_tasks: BackgroundTasks,
    db: DatabaseManager = Depends(get_db),
    emb=Depends(get_emb),
    chroma=Depends(get_chroma)
):
    folders = request.validated_folders
    if not folders:
        return JSONResponse(status_code=400, content={"error": "No valid folder paths provided."})
    indexing_service_cls, _ = _ensure_indexing()
    service = indexing_service_cls(db, emb, chroma)
    _file_tree_cache["data"] = None
    _insights_cache["data"] = None
    background_tasks.add_task(service.index_folders, folders)
    return JSONResponse(status_code=202, content={"message": "Indexing started"})

@app.get("/index/status")
async def index_status(db: DatabaseManager = Depends(get_db)):
    _, progress = _ensure_indexing()
    file_count = 0
    chunk_count = 0
    try:
        file_count, chunk_count = await db.get_counts()
    except Exception as e:
        logger.warning("Could not fetch index counts: %s", e)

    percentage = 0
    if progress.total_files > 0:
        percentage = int((progress.processed_files / progress.total_files) * 100)

    return {
        "status": progress.status,
        "files_indexed": file_count,
        "chunks_indexed": chunk_count,
        "progress_percent": percentage,
        "scan_method": progress.scan_method,
        "scan_duration_ms": round(progress.scan_duration_ms, 1),
        "skipped_files": progress.skipped_files,
        "new_files": progress.new_files,
        "changed_files": progress.changed_files,
    }

@app.post("/query")
async def query(
    request: QueryRequest,
    db: DatabaseManager = Depends(get_db),
    emb=Depends(get_emb),
    chroma=Depends(get_chroma),
    llm=Depends(get_llm)
):
    question = request.validated_question
    if not question:
        return JSONResponse(status_code=400, content={"error": "Question cannot be empty."})

    try:
        full_rag = _ensure_rag()
        results = await full_rag(
            query=question,
            db=db,
            embedding_service=emb,
            chroma_client=chroma,
            llm_client=llm,
            file_type=request.file_type,
            folder_tag=request.folder_tag,
        )
    except Exception as e:
        logger.error("Query failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "An error occurred while processing your query."})

    source_paths = [res["file_path"] for res in results.get("sources", [])]
    if source_paths:
        try:
            await db.batch_increment_usage(source_paths)
        except Exception as e:
            logger.warning("Failed to increment usage counts: %s", e)

    try:
        await db.save_query(
            question=question,
            answer=results.get("answer", ""),
            source_count=results.get("retrieved_count", 0),
            latency_ms=results.get("latency_ms", 0),
        )
    except Exception as e:
        logger.warning("Failed to save query history: %s", e)

    return results




@app.post("/query/stream")
async def query_stream(
    request: QueryRequest,
    db: DatabaseManager = Depends(get_db),
    emb=Depends(get_emb),
    chroma=Depends(get_chroma),
    llm=Depends(get_llm)
):
    question = request.validated_question
    if not question:
        return JSONResponse(status_code=400, content={"error": "Question cannot be empty."})

    from app.search.retrieval import full_rag_stream
    
    return StreamingResponse(
        full_rag_stream(
            query=question,
            db=db,
            embedding_service=emb,
            chroma_client=chroma,
            llm_client=llm,
            file_type=request.file_type,
            folder_tag=request.folder_tag,
        ),
        media_type="text/event-stream"
    )

@app.post("/unreal/import")
async def import_unreal_metadata(
    request: UnrealImportRequest,
    db: DatabaseManager = Depends(get_db),
    emb=Depends(get_emb),
    chroma=Depends(get_chroma),
):
    """Import Unreal metadata JSON and enrich project-level understanding.

    Expected input is a JSON export produced from Unreal tooling or scripts
    containing project + asset metadata.
    """
    json_path = request.validated_json_path
    if not os.path.isfile(json_path):
        return JSONResponse(status_code=400, content={"error": "Metadata JSON file does not exist."})

    try:
        facts = parse_unreal_metadata(json_path, folder_tag=request.folder_tag or "")

        await db.upsert_unreal_project_facts(facts)

        profile = {
            "folder_path": facts["folder_path"],
            "folder_tag": facts["folder_tag"],
            "profile_text": facts["profile_text"],
            "project_type": "Unreal Engine",
            "file_count": max(1, int(facts.get("total_assets", 0))),
            "total_size_bytes": 0,
            "top_extensions": ".uasset, .umap",
            "key_files": ".uproject",
        }
        await db.upsert_folder_profile(profile)

        try:
            emb_vec = await emb.embed_texts([facts["profile_text"]])
            await chroma.add_summary(
                doc_id=f"folder_profile_{facts['folder_tag']}",
                embedding=emb_vec[0],
                metadata={
                    "file_path": facts["folder_path"],
                    "folder_tag": facts["folder_tag"],
                    "project_type": "Unreal Engine",
                    "is_folder_profile": "true",
                    "is_unreal_import": "true",
                },
            )
        except Exception as emb_err:
            logger.warning("Unreal profile embedding failed (non-fatal): %s", emb_err)

        return {
            "message": "Unreal metadata imported successfully.",
            "project": {
                "name": facts["project_name"],
                "engine_version": facts["engine_version"],
                "folder_tag": facts["folder_tag"],
                "folder_path": facts["folder_path"],
            },
            "stats": {
                "total_assets": facts["total_assets"],
                "map_count": facts["map_count"],
                "environment_assets": facts["environment_assets"],
                "character_blueprints": facts["character_blueprints"],
                "pawn_blueprints": facts["pawn_blueprints"],
                "skeletal_meshes": facts["skeletal_meshes"],
                "material_count": facts["material_count"],
                "niagara_systems": facts["niagara_systems"],
            },
        }
    except Exception as e:
        logger.error("Unreal metadata import failed: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Failed to import Unreal metadata."})

@app.get("/insights")
async def get_insights(db: DatabaseManager = Depends(get_db)):
    now = time.time()
    if _insights_cache["data"] and (now - _insights_cache["ts"]) < _CACHE_TTL:
        return _insights_cache["data"]
    insights_service_cls = _ensure_insights()
    insights_svc = insights_service_cls(db)
    stats = await insights_svc.get_stats()
    _insights_cache["data"] = stats
    _insights_cache["ts"] = now
    return stats

@app.get("/insights/by-type")
async def get_insights_by_type(
    type_filter: str = "",
    db: DatabaseManager = Depends(get_db),
):
    """Returns top and cold files filtered by file extension."""
    if not type_filter:
        return {"top_files": [], "cold_files": []}
    insights_service_cls = _ensure_insights()
    insights_svc = insights_service_cls(db)
    return await insights_svc.get_filtered_files(type_filter)

@app.get("/files/tree")
async def get_files_tree(db: DatabaseManager = Depends(get_db)):
    """Returns indexed files grouped by folder tag for tree display."""
    now = time.time()
    if _file_tree_cache["data"] and (now - _file_tree_cache["ts"]) < _CACHE_TTL:
        return _file_tree_cache["data"]
    try:
        files = await db.get_all_files()
    except Exception as e:
        logger.warning("Could not fetch file tree: %s", e)
        files = []
    folders: dict = {}
    total_size = 0
    for f in files:
        tag = f["folder_tag"] or "Unknown"
        if tag not in folders:
            folders[tag] = []
        folders[tag].append({
            "path": f["path"],
            "size": f["size"],
            "type": f["type"],
            "usage_count": f["usage_count"],
        })
        total_size += f["size"]
    result = {"folders": folders, "total_files": len(files), "total_size": total_size}
    _file_tree_cache["data"] = result
    _file_tree_cache["ts"] = now
    return result

@app.get("/pick/folder")
async def pick_folder():
    """Opens a native OS folder picker dialog and returns the selected path."""
    def _dialog():
        root = tk.Tk()
        try:
            root.withdraw()
            root.wm_attributes('-topmost', 1)
            root.focus_force()
            folder = filedialog.askdirectory(parent=root, title="Select Folder to Index")
            return folder or ""
        finally:
            root.destroy()
    try:
        path = await asyncio.get_running_loop().run_in_executor(None, _dialog)
        return {"path": path}
    except Exception as e:
        logger.warning("Folder picker failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "Could not open folder picker."})

@app.get("/pick/file")
async def pick_file():
    """Opens a native OS file picker dialog (multi-select) and returns paths."""
    def _dialog():
        root = tk.Tk()
        try:
            root.withdraw()
            root.wm_attributes('-topmost', 1)
            root.focus_force()
            files = filedialog.askopenfilenames(parent=root, title="Select Files to Index")
            return list(files) if files else []
        finally:
            root.destroy()
    try:
        paths = await asyncio.get_running_loop().run_in_executor(None, _dialog)
        return {"paths": paths}
    except Exception as e:
        logger.warning("File picker failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "Could not open file picker."})

@app.get("/system/info")
async def get_system_info():
    """Returns OS, admin status, available volumes, and scan method."""
    info = {
        "os": plat.system(),
        "is_admin": False,
        "scan_method": "scandir",
        "volumes": [],
    }
    if plat.system() == "Windows":
        try:
            info["is_admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
            if info["is_admin"]:
                info["scan_method"] = "ntfs_mft"
        except Exception:  # noqa: S110 – non-critical admin check
            logger.debug("Could not determine admin status")
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                try:
                    total, used, free = shutil.disk_usage(drive)
                    info["volumes"].append({
                        "letter": f"{letter}:",
                        "total_gb": round(total / (1024 ** 3), 1),
                        "free_gb": round(free / (1024 ** 3), 1),
                        "used_gb": round(used / (1024 ** 3), 1),
                    })
                except OSError as exc:
                    logger.debug("Could not read disk usage for %s: %s", drive, exc)
    return info

@app.get("/system/metrics")
async def get_metrics():
    """Returns detailed stage-level latency metrics."""
    from app.utils.metrics import metrics_tracker
    return metrics_tracker.get_stats()

@app.post("/demo/seed")
async def seed_demo(
    background_tasks: BackgroundTasks,
    db: DatabaseManager = Depends(get_db),
    emb=Depends(get_emb),
    chroma=Depends(get_chroma)
):
    """Creates some demo files and kicks off indexing.

    Only available when PMA_DEV_MODE=1 or when no files are indexed yet.
    """
    dev_mode = settings.dev_mode
    file_count, _ = await db.get_counts()
    if not dev_mode and file_count > 0:
        return JSONResponse(
            status_code=403,
            content={"error": "Demo seeding is disabled in production mode."},
        )
    demo_dir = Path("demo_data")
    demo_dir.mkdir(exist_ok=True)

    (demo_dir / "OS_project_feedback.txt").write_text(
        "TA Feedback on OS Project: The process scheduler implementation is correct, but your error handling in the fork system call needs improvement. 0.92/1.0",
        encoding="utf-8"
    )
    (demo_dir / "internship_offer.md").write_text(
        "# Internship Offer\n\nDear Student, we are pleased to offer you a Software Engineering Internship at Future Corp. Starting date: June 1st, 2026. Salary: $8000/mo.",
        encoding="utf-8"
    )
    (demo_dir / "code_tips.md").write_text(
        "## Python Performance\nUse lists comprehensions instead of maps for small datasets. Always close DB connections.",
        encoding="utf-8"
    )

    indexing_service_cls, _ = _ensure_indexing()
    service = indexing_service_cls(db, emb, chroma)
    background_tasks.add_task(service.index_folders, [str(demo_dir.absolute())])

    return {"message": "Demo files created and indexing started.", "folder": str(demo_dir.absolute())}

@app.get("/index/progress-stream")
async def progress_stream():
    """Server-Sent Events endpoint for real-time indexing progress.

    The frontend connects to this with EventSource and receives
    JSON progress updates every 500ms while indexing is active.
    """
    _, progress = _ensure_indexing()
    async def event_generator():
        while True:
            data = {
                "status": progress.status,
                "total_files": progress.total_files,
                "processed_files": progress.processed_files,
                "total_chunks": progress.total_chunks,
                "skipped_files": progress.skipped_files,
                "new_files": progress.new_files,
                "changed_files": progress.changed_files,
                "current_file": progress.current_file,
                "scan_method": progress.scan_method,
                "scan_duration_ms": round(progress.scan_duration_ms, 1),
                "progress_percent": (
                    int((progress.processed_files / progress.total_files) * 100)
                    if progress.total_files > 0 else 0
                ),
            }
            yield {"event": "progress", "data": json.dumps(data)}

            if progress.status != "running":
                data["status"] = "idle"
                yield {"event": "progress", "data": json.dumps(data)}
                break

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())

@app.get("/query/history")
async def query_history(
    limit: int = 20,
    db: DatabaseManager = Depends(get_db),
):
    """Returns recent query history."""
    try:
        history = await db.get_query_history(limit=min(limit, 100))
        return {"history": history}
    except Exception as e:
        logger.warning("Failed to load query history: %s", e)
        return {"history": []}

@app.post("/index/clear")
async def clear_database(
    db: DatabaseManager = Depends(get_db),
    chroma=Depends(get_chroma),
):
    """Permanently delete ALL indexed data (files, chunks, embeddings, history)."""
    try:
        from app.search.retrieval import clear_retrieval_cache
        counts = await db.clear_all()
        await chroma.clear_all()
        clear_retrieval_cache()
        _, progress = _ensure_indexing()
        progress.reset(0)
        progress.status = "idle"
        _file_tree_cache["data"] = None
        _insights_cache["data"] = None
        logger.info("Database fully cleared by user.")
        return counts
    except Exception as e:
        logger.error("Clear database failed: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Failed to clear database. Please check server logs."})

@app.post("/index/cleanup")
async def cleanup_stale(db: DatabaseManager = Depends(get_db)):
    """Removes index entries for files that no longer exist on disk."""
    try:
        cleaned = await db.cleanup_stale_files()
        _file_tree_cache["data"] = None
        _insights_cache["data"] = None
        return {
            "message": f"Cleaned {len(cleaned)} stale file(s).",
            "cleaned_paths": cleaned,
        }
    except Exception as e:
        logger.error("Cleanup failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "Cleanup failed."})

@app.post("/index/reindex")
async def force_reindex(
    request: IndexRequest,
    background_tasks: BackgroundTasks,
    db: DatabaseManager = Depends(get_db),
    emb=Depends(get_emb),
    chroma=Depends(get_chroma),
):
    """Force re-index folders, ignoring change detection.

    Deletes existing entries for the specified folders first.
    """
    folders = request.validated_folders
    if not folders:
        return JSONResponse(status_code=400, content={"error": "No valid folder paths provided."})

    for folder in folders:
        try:
            await db.delete_files_by_folder_prefix(folder)
        except Exception as e:
            logger.warning("Failed to clean folder %s before re-index: %s", folder, e)

    indexing_service_cls, _ = _ensure_indexing()
    service = indexing_service_cls(db, emb, chroma)
    background_tasks.add_task(service.index_folders, folders)
    return JSONResponse(status_code=202, content={"message": "Re-indexing started (change detection bypassed)"})

@app.get("/index/export")
async def export_index(db: DatabaseManager = Depends(get_db)):
    """Export all indexed file metadata as JSON for backup/migration."""
    try:
        files = await db.get_all_files()
        file_count, chunk_count = await db.get_counts()
        export_data = {
            "file_count": file_count,
            "chunk_count": chunk_count,
            "files": [
                {
                    "path": f["path"],
                    "size": f["size"],
                    "type": f["type"],
                    "folder_tag": f["folder_tag"],
                    "usage_count": f["usage_count"],
                }
                for f in files
            ],
        }
        return export_data
    except Exception as e:
        logger.error("Export failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "Export failed."})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.dev_mode,
        log_level=settings.log_level.lower(),
    )
