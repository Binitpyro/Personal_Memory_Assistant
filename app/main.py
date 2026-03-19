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

APP_VERSION = "0.0.41"

def _versioned_static_url(asset_name: str) -> str:
    # Look in the base static directory for legacy assets
    """
    Return a cacheable URL for a static asset that includes a version token derived from the asset's modification time.
    
    Parameters:
        asset_name (str): File name of the static asset (relative to the application's `static` directory).
    
    Returns:
        str: A path under `/static/` for the asset. If the file exists, the URL includes a `?v=<hex>` query token computed from the file's modification time; if the file cannot be stat-ed, returns `/static/<asset_name>` without a token.
    
    Notes:
        The function caches the computed (mtime_ns, url) pair to avoid repeated filesystem stat calls.
    """
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
    """
    Lifespan context manager that initializes application services on startup and performs cleanup on shutdown.
    
    On startup this connects and initializes the database schema, logs a Windows administrative privilege check (used to determine NTFS MFT fast-scan advisory), begins background model loading for embeddings, connects the Chroma vector store, and schedules a background reranker preload; it then yields to allow the app to serve. On shutdown it closes the database connection.
    """
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
    logger.info("Server ready (v%s)", APP_VERSION)
    yield
    logger.info("Shutting down...")
    await db_manager.close()

app = FastAPI(title="Personal Memory Assistant", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    if request.url.path not in ("/health", "/index/progress-stream"):
        logger.info("[%s] %s %s → %d (%.0fms)", request_id, request.method, request.url.path, response.status_code, elapsed)
    return response

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "Validation error", "detail": exc.errors()}
    )

class IndexRequest(BaseModel):
    folders: List[str] = Field(..., max_length=50)
    @property
    def validated_folders(self) -> List[str]:
        cleaned = []
        for f in self.folders:
            p = f.strip().strip('"').strip("'")
            if not p: continue
            resolved = os.path.realpath(os.path.normpath(p))
            if os.path.isdir(resolved): cleaned.append(resolved)
        return cleaned

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    file_type: Optional[str] = Field(None)
    folder_tag: Optional[str] = Field(None)
    history: Optional[List[Dict[str, str]]] = Field(None) # List of {"role": "user/assistant", "content": "..."}
    @property
    def validated_question(self) -> str: return self.question.strip()

class UnrealImportRequest(BaseModel):
    json_path: str = Field(..., min_length=1)
    folder_tag: Optional[str] = Field(None)
    @property
    def validated_json_path(self) -> str:
        return os.path.realpath(self.json_path.strip().strip('"').strip("'"))

api_router = APIRouter()

@api_router.get("/health")
async def health(db: DatabaseManager = Depends(get_db)):
    db_ok = await db.is_healthy()
    emb = _get_embedding_service()
    _, progress = _ensure_indexing()
    return {"version": APP_VERSION, "status": "ok" if db_ok else "degraded", "db": "connected" if db_ok else "error", "model_ready": emb.is_ready, "indexing": progress.status}

@api_router.post("/index/start")
async def index_start(request: IndexRequest, background_tasks: BackgroundTasks, db: DatabaseManager = Depends(get_db), emb=Depends(get_emb), chroma=Depends(get_chroma)):
    """
    Start background indexing for the request's validated folders and schedule a database compaction after indexing.
    
    Clears in-memory file-tree and insights caches, enqueues a background task that runs the indexing service over the validated folders and then attempts to run `db.vacuum()` (logs a warning on failure).
    
    Parameters:
        request (IndexRequest): Request model whose `validated_folders` property provides the folder paths to index.
        background_tasks (BackgroundTasks): FastAPI background task registry used to run indexing and compaction asynchronously.
    
    Returns:
        dict: `{"message": "Indexing started"}` on success; returns HTTP 400 JSON `{"error": "No valid folder paths provided."}` if no folders are valid.
    """
    folders = request.validated_folders
    if not folders: return JSONResponse(status_code=400, content={"error": "No valid folder paths provided."})
    indexing_service_cls, _ = _ensure_indexing()
    service = indexing_service_cls(db, emb, chroma)
    _file_tree_cache["data"] = None
    _insights_cache["data"] = None
    async def _index_then_compact():
        """
        Run the indexing process for the configured folders and attempt to compact the database afterwards.
        
        Attempts to run a database VACUUM after indexing and logs whether compaction succeeded or failed.
        """
        await service.index_folders(folders)
        try:
            await db.vacuum()
            logger.info("Auto-compact completed after indexing.")
        except Exception as e:
            logger.warning("Auto-compact after indexing failed: %s", e)
    background_tasks.add_task(_index_then_compact)
    return {"message": "Indexing started"}

@api_router.get("/index/status")
async def index_status(db: DatabaseManager = Depends(get_db)):
    _, progress = _ensure_indexing()
    file_count, chunk_count = await db.get_counts()
    percentage = int((progress.processed_files / progress.total_files) * 100) if progress.total_files > 0 else 0
    return {"status": progress.status, "files_indexed": file_count, "chunks_indexed": chunk_count, "progress_percent": percentage, "scan_method": progress.scan_method, "processed_files": progress.processed_files, "total_files": progress.total_files}

@api_router.get("/index/progress-stream")
async def progress_stream():
    """
    Stream indexing progress as server-sent events.
    
    Each SSE event is named "progress" and carries a JSON object with the following keys:
    `status`, `total_files`, `processed_files`, `total_chunks`, `skipped_files`, `new_files`,
    `changed_files`, `current_file`, `scan_method`, `scan_duration_ms`, and `progress_percent`.
    
    Returns:
        EventSourceResponse: An SSE response that yields periodic "progress" events until indexing stops.
    """
    _, progress = _ensure_indexing()
    async def event_generator():
        """
        Stream indexing progress events for server-sent events (SSE).
        
        Yields periodic progress events containing the current indexing status and metrics until indexing is no longer running.
        
        Returns:
        	A generator that yields dictionaries with keys:
        		- `event` (str): Always `"progress"`.
        		- `data` (str): JSON-encoded object with the following fields:
        			- `status` (str): Current progress status (e.g., `"running"`, `"completed"`).
        			- `total_files` (int): Total number of files discovered for indexing.
        			- `processed_files` (int): Number of files processed so far.
        			- `total_chunks` (int): Current chunk count from the database.
        			- `skipped_files` (int): Number of files skipped during indexing.
        			- `new_files` (int): Number of newly discovered files.
        			- `changed_files` (int): Number of files detected as changed.
        			- `current_file` (str | None): Path of the file currently being processed, if any.
        			- `scan_method` (str): Method used to scan files (e.g., `"scandir"`, `"ntfs_mft"`).
        			- `scan_duration_ms` (int): Duration of the ongoing scan in milliseconds.
        			- `progress_percent` (int): Integer percent complete (0–100).
        """
        while True:
            _, chunk_count = await db_manager.get_counts()
            pct = int((progress.processed_files / progress.total_files) * 100) if progress.total_files > 0 else 0
            data = {
                "status": progress.status,
                "total_files": progress.total_files,
                "processed_files": progress.processed_files,
                "total_chunks": chunk_count,
                "skipped_files": progress.skipped_files,
                "new_files": progress.new_files,
                "changed_files": progress.changed_files,
                "current_file": progress.current_file,
                "scan_method": progress.scan_method,
                "scan_duration_ms": progress.scan_duration_ms,
                "progress_percent": pct
            }
            yield {"event": "progress", "data": json.dumps(data)}
            if progress.status != "running": break
            await asyncio.sleep(0.5)
    return EventSourceResponse(event_generator())

@api_router.post("/index/cleanup")
async def cleanup_stale(db: DatabaseManager = Depends(get_db)):
    try:
        cleaned = await db.cleanup_stale_files()
        _file_tree_cache["data"] = _insights_cache["data"] = None
        return {"message": f"Cleaned {len(cleaned)} stale file(s).", "cleaned_paths": cleaned}
    except Exception as e:
        logger.error("Cleanup failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "Cleanup failed."})

@api_router.post("/index/clear")
async def clear_index(db: DatabaseManager = Depends(get_db), chroma=Depends(get_chroma)):
    """
    Delete all indexed records from the database and remove all documents from the vector store; also clears the in-memory file tree and insights caches.
    
    Returns:
        The value returned by `db.clear_all()`.
    """
    res = await db.clear_all()
    await chroma.clear_all()
    _file_tree_cache["data"] = _insights_cache["data"] = None
    return res

@api_router.get("/index/export")
async def export_index(db: DatabaseManager = Depends(get_db)):
    try:
        file_count, chunk_count = await db.get_counts()
        files = await db.get_all_files()
        return {"file_count": file_count, "chunk_count": chunk_count, "files": [dict(f) for f in files]}
    except Exception as e:
        logger.error("Export failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "Export failed."})

@api_router.post("/query")
async def query(request: QueryRequest, background_tasks: BackgroundTasks, db: DatabaseManager = Depends(get_db), emb=Depends(get_emb), chroma=Depends(get_chroma), llm=Depends(get_llm)):
    question = request.validated_question
    if not question: return JSONResponse(status_code=400, content={"error": "Question cannot be empty."})
    full_rag = _ensure_rag()
    results = await full_rag(query=question, db=db, embedding_service=emb, chroma_client=chroma, llm_client=llm, file_type=request.file_type, folder_tag=request.folder_tag)
    
    if results.get("mode") != "fast_path":
        source_paths = [res["file_path"] for res in results.get("sources", []) if res.get("file_path")]
        if source_paths:
            async def _bg_increment():
                try:
                    # Create a fresh connection if needed or use db_manager safely
                    await db.batch_increment_usage(source_paths)
                except Exception as e:
                    logger.warning("Failed to increment usage counts: %s", e)
            background_tasks.add_task(_bg_increment)

    if not results.get("cache_hit"):
        async def _bg_save_query():
            try: await db.save_query(question=question, answer=results.get("answer", ""), source_count=results.get("retrieved_count", 0), latency_ms=results.get("latency_ms", 0))
            except Exception as e: logger.warning("Failed to save query history: %s", e)
        t = asyncio.create_task(_bg_save_query())
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
    return results

@api_router.post("/query/stream")
async def query_stream(request: QueryRequest, db: DatabaseManager = Depends(get_db), emb=Depends(get_emb), chroma=Depends(get_chroma), llm=Depends(get_llm)):
    question = request.validated_question
    full_rag = _ensure_rag()
    async def stream_results():
        results = await full_rag(query=question, db=db, embedding_service=emb, chroma_client=chroma, llm_client=llm, file_type=request.file_type, folder_tag=request.folder_tag)
        yield json.dumps({"type": "sources", "sources": results.get("sources", []), "latency_retrieval_ms": results.get("latency_ms", 0)}) + "\n"
        yield json.dumps({"type": "content", "text": results.get("answer", "")}) + "\n"
    return StreamingResponse(stream_results(), media_type="text/event-stream")

@api_router.get("/query/history")
async def query_history(limit: int = 20, db: DatabaseManager = Depends(get_db)):
    try:
        history = await db.get_query_history(limit=limit)
        return {"history": history}
    except Exception:
        return {"history": []}

@api_router.post("/query/history/clear")
async def clear_query_history(db: DatabaseManager = Depends(get_db)):
    try:
        return await db.clear_query_history()
    except Exception as e:
        logger.error("Failed to clear query history: %s", e)
        return JSONResponse(status_code=500, content={"error": "Failed to clear history."})

@api_router.get("/insights")
async def get_insights(db: DatabaseManager = Depends(get_db)):
    now = time.time()
    if _insights_cache["data"] and (now - _insights_cache["ts"]) < _CACHE_TTL: return _insights_cache["data"]
    insights_service_cls = _ensure_insights()
    stats = await insights_service_cls(db).get_stats()
    _insights_cache["data"], _insights_cache["ts"] = stats, now
    return stats

@api_router.get("/insights/by-type")
async def get_insights_by_type(extension: str, db: DatabaseManager = Depends(get_db)):
    insights_service_cls = _ensure_insights()
    return await insights_service_cls(db).get_filtered_files(extension)

@api_router.get("/files/tree")
async def get_files_tree(db: DatabaseManager = Depends(get_db)):
    """
    Return a cached file-tree grouping of indexed files by folder tag.
    
    Uses a short in-memory TTL cache to avoid repeated database queries. On success returns a mapping of folder tags to lists of file entries and aggregate counts; on error returns empty defaults.
    
    Returns:
        dict: {
            "folders": dict mapping folder_tag (str) to list of entries, where each entry is
                {"path": str, "size": int, "type": str, "usage_count": int},
            "total_files": int,
            "total_size": int
        } — returns {"folders": {}, "total_files": 0, "total_size": 0} if an error occurs.
    """
    try:
        now = time.time()
        if _file_tree_cache["data"] and (now - _file_tree_cache["ts"]) < _CACHE_TTL: return _file_tree_cache["data"]
        files = await db.get_all_files()
        folders: dict = {}
        total_size = 0
        for f in files:
            tag = f["folder_tag"] or "Unknown"
            if tag not in folders: folders[tag] = []
            folders[tag].append({"path": f["path"], "size": f["size"], "type": f["type"], "usage_count": f["usage_count"]})
            total_size += f["size"]
        result = {"folders": folders, "total_files": len(files), "total_size": total_size}
        _file_tree_cache["data"], _file_tree_cache["ts"] = result, now
        return result
    except Exception:
        return {"folders": {}, "total_files": 0, "total_size": 0}

@api_router.get("/visualizer/stream")
async def stream_visualizer_binary(db: DatabaseManager = Depends(get_db)):
    """
    Stream a binary payload representing all files and folders for consumption by the WebGPU visualizer.
    
    The response begins with a 4-byte little-endian unsigned integer containing the total number of nodes (files + folders). Following the header, each node is encoded as 20 bytes: four little-endian 32-bit floats (x, y, z, size) followed by one little-endian 32-bit unsigned integer (typeHash). Folder entries use a negative `size` value to distinguish them from files.
    """
    import struct
    import math

    async def binary_generator():
        # First, we need the total count to send as a header.
        # This prevents WebGPU from allocating incorrectly.
        """
        Generate a binary stream of node records for WebGPU visualization.
        
        Yields a 4-byte little-endian unsigned integer header containing the total node count (file nodes + folder profiles), followed by one or more binary chunks. Each chunk is a sequence of 20-byte records packed as little-endian: four 32-bit floats (x, y, z, norm_size) followed by one 32-bit unsigned integer (typeHash). Coordinates and norm_size are deterministic values derived from the node index and size; folders use a negative `norm_size` to distinguish them and files use a positive `norm_size`. The `typeHash` is a deterministic 32-bit hash computed from the node's filename. Chunks are yielded incrementally (buffered to roughly 1 MB) to limit memory usage.
         
        Returns:
            Async iterator yielding `bytes`: the first yielded value is the 4-byte header, subsequent yields are binary payload chunks containing packed node records.
        """
        file_count, _ = await db.get_counts()
        folder_count = len(await db.get_all_folder_profiles())
        total_nodes = file_count + folder_count

        yield struct.pack("<I", total_nodes)

        buffer = bytearray()
        i = 0

        async for node in db.stream_all_nodes():
            path = node["path"]
            size = float(node["size"])
            is_folder = node["is_folder"]

            # Deterministic layout logic mimicking frontend
            angle = i * 0.1
            radius = 10.0 + math.sqrt(i) * 2.0
            x = math.cos(angle) * radius
            y = math.sin(angle) * radius
            z = (i % 100.0) - 50.0

            if is_folder:
                # Folders are drawn larger and distinguished by a negative size value
                norm_size = -max(2.0, math.log10(size + 1.0) * 1.5)
            else:
                norm_size = max(0.5, math.log10(size + 1.0) * 0.8)

            # Simple string hash matching the frontend logic
            hash_val = 0
            name = path.split("\\")[-1].split("/")[-1]
            for char in name:
                hash_val = ((hash_val << 5) - hash_val) + ord(char)
                hash_val &= 0xFFFFFFFF

            # Pack: 4 floats + 1 unsigned int = 20 bytes
            buffer.extend(struct.pack("<ffffI", x, y, z, norm_size, hash_val))
            i += 1

            # Yield every 50,000 nodes (1MB) to keep memory footprint low
            if len(buffer) >= 1000000:
                yield bytes(buffer)
                buffer.clear()

        # Yield remainder
        if buffer:
            yield bytes(buffer)

    return StreamingResponse(binary_generator(), media_type="application/octet-stream")
@api_router.post("/index/folder/remove")
async def remove_folder_index(request: IndexRequest, db: DatabaseManager = Depends(get_db), chroma=Depends(get_chroma)):
    """
    Remove indexed files under the provided folder paths and delete their associated vector and summary documents.
    
    Given an IndexRequest, finds files whose stored path matches each validated folder (handling both Unix and Windows path variants), deletes associated Chroma chunk documents and per-file summaries, removes the file records from the database, and clears in-memory file-tree and insights caches.
    
    Parameters:
        request (IndexRequest): Request containing folder paths to remove; uses `request.validated_folders`.
    
    Returns:
        dict: A message of the form `{"message": "Successfully removed N files."}` indicating how many files were removed.
        JSONResponse (status 400): If no valid folder paths are provided, returns `{"error": "No valid folder paths provided."}`.
    """
    folders = request.validated_folders
    if not folders: return JSONResponse(status_code=400, content={"error": "No valid folder paths provided."})
    removed_total = 0
    for folder in folders:
        try:
            f_norm = folder.replace("\\", "/")
            if not f_norm.endswith("/"): f_norm += "/"
            p1, p2 = f_norm + "%", f_norm.replace("/", "\\") + "%"
            p3, p4 = f_norm[:-1], f_norm[:-1].replace("/", "\\")
            rows = await db.execute_query("SELECT id FROM files WHERE path LIKE ? OR path LIKE ? OR path = ? OR path = ?", (p1, p2, p3, p4))
            if not rows: continue
            file_ids = [r[0] for r in rows]
            chunk_rows = await db.execute_query(f"SELECT id FROM chunks WHERE file_id IN ({','.join('?' for _ in file_ids)})", tuple(file_ids))
            chunk_ids = [str(r[0]) for r in chunk_rows]
            if chunk_ids: await chroma.delete_documents(chunk_ids)
            for f_id in file_ids: await chroma.delete_summary(f_id=f"file_{f_id}")
            await db.execute_write(f"DELETE FROM files WHERE id IN ({','.join('?' for _ in file_ids)})", tuple(file_ids))
            removed_total += len(file_ids)
        except Exception as e: logger.error("Failed to remove folder index for %s: %s", folder, e)
    _file_tree_cache["data"] = _insights_cache["data"] = None
    return {"message": f"Successfully removed {removed_total} files."}

@api_router.post("/unreal/import")
async def unreal_import(request: UnrealImportRequest, db: DatabaseManager = Depends(get_db), emb=Depends(get_emb), chroma=Depends(get_chroma)):
    """
    Import Unreal project metadata from the provided JSON file, persist extracted facts to the database, and store an embedded profile summary in the vector store when available.
    
    Parameters:
        request (UnrealImportRequest): Request containing `validated_json_path` (path to the metadata JSON) and optional `folder_tag` used to tag the imported facts.
    
    Returns:
        dict: Parsed `facts` extracted from the metadata on success.
        JSONResponse: On failure, a JSONResponse with an `error` message and appropriate HTTP status code.
    """
    try:
        path = request.validated_json_path
        if not os.path.exists(path): return JSONResponse(status_code=400, content={"error": "Metadata file not found."})
        
        # Run sync parser in executor
        loop = asyncio.get_running_loop()
        facts = await loop.run_in_executor(None, parse_unreal_metadata, path, request.folder_tag)
        
        # Persist facts to DB
        await db.upsert_unreal_project_facts(facts)
        
        # Embed profile text and store in summary collection
        if facts.get("profile_text"):
            embeddings = await emb.embed_texts([facts["profile_text"]])
            await chroma.add_summaries_batch([{
                "doc_id": f"unreal_import_{facts['folder_tag']}",
                "embedding": embeddings[0],
                "metadata": {
                    "file_path": facts["folder_path"],
                    "folder_tag": facts["folder_tag"],
                    "project_type": "Unreal Engine",
                    "is_unreal_import": "true"
                }
            }])
            
        return facts
    except Exception as e:
        logger.error("Unreal import failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})

@api_router.get("/system/info")
async def get_system_info():
    volumes = []
    if plat.system() == "Windows":
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive = f"{letter}:\\"
                try:
                    total, used, free = shutil.disk_usage(drive)
                    volumes.append({"letter": letter, "total_gb": total // (1024**3), "used_gb": used // (1024**3), "free_gb": free // (1024**3)})
                except OSError: pass
            bitmask >>= 1
    return {"os": plat.system(), "is_admin": ctypes.windll.shell32.IsUserAnAdmin() if plat.system() == "Windows" else False, "scan_method": "ntfs_mft" if plat.system() == "Windows" else "scandir", "volumes": volumes}

@api_router.get("/system/metrics")
async def get_metrics(): return metrics_tracker.get_stats()

@api_router.post("/system/compact-db")
async def compact_db(db: DatabaseManager = Depends(get_db)):
    """
    Start a background database compaction (VACUUM) task.
    
    Schedules a vacuum operation to run in the background and tracks the created task.
    
    Returns:
        dict: A single-key mapping `{"message": "Compaction started in background."}` confirming the background task was scheduled.
    """
    async def _do_vacuum():
        try: await db.vacuum()
        except Exception as e: logger.error("Vacuum failed: %s", e)
    t = asyncio.create_task(_do_vacuum())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return {"message": "Compaction started in background."}

@api_router.get("/system/compact-db/status")
async def compact_status(): return {"is_running": False, "last_run": None, "error": None} # Minimal mock

@api_router.post("/system/clear-cache")
async def clear_cache():
    _file_tree_cache["data"] = _insights_cache["data"] = None
    from app.search.retrieval import clear_retrieval_cache
    clear_retrieval_cache()
    return {"message": "Caches cleared."}

@api_router.post("/demo/seed")
async def demo_seed(background_tasks: BackgroundTasks, db: DatabaseManager = Depends(get_db), emb=Depends(get_emb), chroma=Depends(get_chroma)):
    """
    Start background indexing of the bundled demo_data folder and schedule a database compaction after indexing.
    
    Clears in-memory file-tree and insights caches, enqueues a background task that runs the indexing service on the demo_data folder and attempts a DB vacuum when indexing completes.
    
    Parameters:
    	background_tasks (BackgroundTasks): FastAPI BackgroundTasks instance used to schedule the indexing-and-compact task.
    
    Returns:
    	On success, a dict with keys `message` and `folder` describing the started job. If the demo_data folder is not found, a JSONResponse with status code 400 and an `error` message is returned.
    """
    demo_folder = str(_BASE_DIR / "demo_data")
    if not os.path.isdir(demo_folder):
        return JSONResponse(status_code=400, content={"error": "demo_data folder not found."})
    indexing_service_cls, _ = _ensure_indexing()
    service = indexing_service_cls(db, emb, chroma)
    _file_tree_cache["data"] = _insights_cache["data"] = None
    async def _demo_index_then_compact():
        """
        Index the demo folder and attempt to compact the database afterward.
        
        Runs the demo indexing process and then tries to run a database compaction (vacuum). Logs success or failure of the compaction.
        """
        await service.index_folders([demo_folder])
        try:
            await db.vacuum()
            logger.info("Auto-compact completed after demo indexing.")
        except Exception as e:
            logger.warning("Auto-compact after demo indexing failed: %s", e)
    background_tasks.add_task(_demo_index_then_compact)
    return {"message": "Demo indexing started for demo_data folder.", "folder": demo_folder}

@api_router.get("/pick/folder")
async def pick_folder():
    """
    Open a native folder-selection dialog and return the chosen directory path.
    
    Returns:
        result (dict): A dictionary with key `"path"` whose value is the selected directory path as a string, or an empty string if the user cancelled the dialog.
    """
    def _dialog():
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        return filedialog.askdirectory(parent=root, title="Select Folder") or ""
    path = await asyncio.get_running_loop().run_in_executor(None, _dialog)
    return {"path": path}

app.include_router(api_router, prefix="/api")

@app.get("/health")
async def health_root(db: DatabaseManager = Depends(get_db)):
    return await health(db)

@app.get("/")
async def root(request: Request):
    """
    Serve the single-page application entry point, preferring the built React index file when available.
    
    Parameters:
        request (Request): The incoming FastAPI request used when rendering the template.
    
    Returns:
        FileResponse: If the React-built index file exists.
        TemplateResponse: Otherwise, renders the configured SPA template with `app_version`, `pma_css_url`, and `pma_js_url` context.
    """
    if _REACT_INDEX.exists():
        return FileResponse(_REACT_INDEX)
    return templates.TemplateResponse(
        request, 
        INDEX_HTML, 
        {"app_version": APP_VERSION, "pma_css_url": _versioned_static_url("pma.css"), "pma_js_url": _versioned_static_url("pma.js")}
    )

@app.get("/{full_path:path}")
async def spa_catch_all(request: Request, full_path: str):
    """
    Serve a requested file from the React static directory or return the SPA index HTML when the client accepts HTML; otherwise respond with a 404 JSON error.
    
    If the requested path matches an existing file under the React build directory, that file is returned. If the client accepts "text/html", the React index file is returned (from disk if present, otherwise rendered from the TEMPLATE). For other requests that do not match a static file and do not accept HTML, a 404 JSON response is returned.
    
    Parameters:
    	full_path (str): The requested path relative to the React static directory.
    
    Returns:
    	FileResponse or TemplateResponse or JSONResponse: The matched static file response, the SPA index HTML response, or a JSON 404 error response.
    """
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
