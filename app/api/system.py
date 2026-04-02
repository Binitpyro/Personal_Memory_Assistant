import asyncio
import ctypes
import os
import platform as plat
import shutil
import string
import logging

from fastapi import APIRouter, Depends, BackgroundTasks
from fastapi.responses import JSONResponse

from app.api.deps import (
    get_db, get_emb, get_chroma, get_llm, ensure_indexing
)
from app.storage.db import DatabaseManager
from app.config import settings
from app.project_constants import APP_VERSION

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/system/config")
async def get_app_config():
    """Public app metadata for reactive UI (model name, version, embeddings)."""
    llm = get_llm()
    return {
        "app_version": APP_VERSION,
        "embedding_model": settings.embedding_model,
        "gemini_model": getattr(llm, "model", settings.gemini_model),
        "gemini_max_output_tokens": settings.gemini_max_output_tokens,
        "dev_mode": settings.dev_mode,
        "prompt_version": "inline-v1",
    }


@router.get("/system/metrics")
async def get_metrics(): 
    from app.utils.metrics import metrics_tracker
    return metrics_tracker.get_stats()

@router.get("/system/info")
async def get_system_info():
    """Return OS details, admin status, scan method, and disk volume info."""
    import ctypes
    import platform as plat
    # Determine admin status on Windows
    is_admin = False
    if plat.system() == "Windows":
        try:
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            is_admin = False

    # Determine scan method used by indexer (MFT fast-path requires admin on Windows)
    if plat.system() == "Windows" and is_admin:
        scan_method = "MFT (fast)"
    else:
        scan_method = "scandir"

    # Enumerate disk volumes
    volumes = []
    if plat.system() == "Windows":
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                try:
                    total, used, free = shutil.disk_usage(drive)
                    GB = 1024 ** 3
                    volumes.append({
                        "letter": f"{letter}:",
                        "total_gb": round(total / GB, 1),
                        "used_gb": round(used / GB, 1),
                        "free_gb": round(free / GB, 1),
                    })
                except PermissionError:
                    pass
    else:
        # Non-Windows: report the root filesystem
        try:
            total, used, free = shutil.disk_usage("/")
            GB = 1024 ** 3
            volumes.append({
                "letter": "/",
                "total_gb": round(total / GB, 1),
                "used_gb": round(used / GB, 1),
                "free_gb": round(free / GB, 1),
            })
        except Exception:
            pass

    # Format OS string properly (Windows 11 reports as 10 natively)
    os_name = plat.system()
    if os_name == "Windows":
        release = plat.release()
        build_str = ""
        try:
            parts = plat.version().split('.')
            build = int(parts[2])
            build_str = f" (Build {build})"
            if release == "10" and build >= 22000:
                release = "11"
        except Exception:
            pass
        os_str = f"Windows {release}{build_str}"
    elif os_name == "Darwin":
        os_str = f"macOS {plat.mac_ver()[0]}"
    else:
        os_str = f"{os_name} {plat.release()}"

    return {
        "os": os_str,
        "is_admin": is_admin,
        "scan_method": scan_method,
        "volumes": volumes,
    }

@router.post("/system/compact-db")
async def compact_db(db: DatabaseManager = Depends(get_db)):
    async def _do_vacuum():
        try: await db.vacuum()
        except Exception as e: logger.error("Vacuum failed: %s", e)
    from app.main import _bg_tasks
    t = asyncio.create_task(_do_vacuum())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return {"message": "Compaction started in background."}

@router.get("/system/compact-db/status")
async def compact_status(): 
    return {"is_running": False, "last_run": None, "error": None} # Minimal mock

@router.post("/system/clear-cache")
async def clear_cache():
    try:
        from app.main import _file_tree_cache, _insights_cache
        _file_tree_cache["data"] = _insights_cache["data"] = None
    except ImportError:
        pass
        
    from app.search.retrieval import clear_retrieval_cache
    clear_retrieval_cache()
    return {"message": "Caches cleared."}

@router.post("/demo/seed")
async def demo_seed(background_tasks: BackgroundTasks, db: DatabaseManager = Depends(get_db), emb=Depends(get_emb), chroma=Depends(get_chroma)):
    from pathlib import Path
    _BASE_DIR = Path(__file__).parent.parent.parent
    demo_folder = str(_BASE_DIR / "demo_data")
    if not os.path.isdir(demo_folder):
        return JSONResponse(status_code=400, content={"error": "demo_data folder not found."})
        
    indexing_service_cls, _ = ensure_indexing()
    service = indexing_service_cls(db, emb, chroma)
    
    try:
        from app.main import _file_tree_cache, _insights_cache
        _file_tree_cache["data"] = _insights_cache["data"] = None
    except ImportError:
        pass
        
    async def _demo_index_then_compact():
        await service.index_folders([demo_folder])
        try:
            await db.vacuum()
            logger.info("Auto-compact completed after demo indexing.")
        except Exception as e:
            logger.warning("Auto-compact after demo indexing failed: %s", e)
            
    background_tasks.add_task(_demo_index_then_compact)
    return {"message": "Demo indexing started for demo_data folder.", "folder": demo_folder}

@router.get("/pick/folder")
async def pick_folder():
    def _dialog():
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        return filedialog.askdirectory(parent=root, title="Select Folder") or ""
    path = await asyncio.get_running_loop().run_in_executor(None, _dialog)
    return {"path": path}
