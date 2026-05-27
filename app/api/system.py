import asyncio
import ctypes
import logging
import os
import platform as plat
import shutil
import string
from datetime import UTC
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse

from app.api.deps import ensure_indexing, get_db, get_emb, get_lancedb, get_llm
from app.config import settings
from app.project_constants import APP_VERSION
from app.storage.db import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()

# P0-4: Real vacuum state tracking (replaces hardcoded mock)
_vacuum_lock: asyncio.Lock | None = None
_vacuum_last_run: str | None = None
_vacuum_last_error: str | None = None

def get_vacuum_lock() -> asyncio.Lock:
    global _vacuum_lock
    if _vacuum_lock is None:
        _vacuum_lock = asyncio.Lock()
    return _vacuum_lock


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


def get_drive_fs_type(drive_letter: str) -> str:
    """Returns the precise file system (e.g. 'NTFS', 'exFAT', 'FAT32') using kernel32"""
    if plat.system() != "Windows":
        return "Unknown"
    try:
        volume_name_buffer = ctypes.create_unicode_buffer(1024)
        file_system_name_buffer = ctypes.create_unicode_buffer(1024)
        result = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(drive_letter + "\\"),
            volume_name_buffer,
            len(volume_name_buffer),
            None,
            None,
            None,
            file_system_name_buffer,
            len(file_system_name_buffer),
        )
        if result:
            return file_system_name_buffer.value
    except Exception:
        logger.debug("Failed to detect Windows file system name.", exc_info=True)
        pass
    return "Unknown"


@router.get("/system/drive_info")
async def get_drive_info():
    """Detects the filesystem of the current drive hosting the app/SQLite."""
    fs_type = "Unknown"
    drive = ""
    is_portable_fs = False

    if plat.system() == "Windows":
        drive = os.path.splitdrive(os.getcwd())[0]
        if not drive:
            drive = "C:"
        # get_drive_fs_type uses blocking ctypes kernel32 call — offload it.
        fs_type = await asyncio.to_thread(get_drive_fs_type, drive)
        is_portable_fs = fs_type.lower() in ("exfat", "fat32")

    return {
        "drive": drive,
        "fs_type": fs_type,
        "is_portable_fs": is_portable_fs,
        "lancedb_mode": settings.lancedb_mode,
    }


@router.get("/system/metrics")
async def get_metrics():
    from app.utils.metrics import metrics_tracker

    return metrics_tracker.get_stats()


def _get_windows_version() -> str:
    """Pretty formats Windows version and build."""
    release = plat.release()
    try:
        parts = plat.version().split(".")
        build = int(parts[2])
        if release == "10" and build >= 22000:
            release = "11"
        return f"Windows {release} (Build {build})"
    except Exception:
        return f"Windows {release}"


def _get_os_string() -> str:
    """Helper to format a pretty OS name and version."""
    os_name = plat.system()
    if os_name == "Windows":
        return _get_windows_version()
    if os_name == "Darwin":
        return f"macOS {plat.mac_ver()[0]}"
    return f"{os_name} {plat.release()}"


def _get_volumes() -> list[dict[str, Any]]:
    """Helper to enumerate disk volumes and their usage."""
    gb_unit = 1024**3
    if plat.system() != "Windows":
        try:
            total, used, free = shutil.disk_usage("/")
            return [
                {
                    "letter": "/",
                    "total_gb": round(total / gb_unit, 1),
                    "used_gb": round(used / gb_unit, 1),
                    "free_gb": round(free / gb_unit, 1),
                }
            ]
        except Exception:
            return []

    volumes = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            try:
                total, used, free = shutil.disk_usage(drive)
                volumes.append(
                    {
                        "letter": f"{letter}:",
                        "total_gb": round(total / gb_unit, 1),
                        "used_gb": round(used / gb_unit, 1),
                        "free_gb": round(free / gb_unit, 1),
                    }
                )
            except (PermissionError, OSError):
                continue
    return volumes


@router.get("/system/info")
async def get_system_info():
    """Return OS details, admin status, scan method, and disk volume info."""
    is_admin = False
    if plat.system() == "Windows":
        try:
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            is_admin = False

    scan_method = "MFT (fast)" if (plat.system() == "Windows" and is_admin) else "scandir"
    # _get_volumes() uses shutil.disk_usage — blocking I/O, offload it.
    volumes = await asyncio.to_thread(_get_volumes)

    return {
        "os": _get_os_string(),
        "is_admin": is_admin,
        "scan_method": scan_method,
        "volumes": volumes,
    }


@router.post("/system/enable-split-brain")
async def enable_split_brain():
    """Edits the .env file to enable split_brain mode."""
    env_file = ".env"
    try:
        lines = []
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
        
        found = False
        for i, line in enumerate(lines):
            if line.startswith("PMA_LANCEDB_MODE="):
                lines[i] = "PMA_LANCEDB_MODE=split_brain\n"
                found = True
                break
        
        if not found:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append("PMA_LANCEDB_MODE=split_brain\n")
            
        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(lines)
            
        return {"message": "Split-brain mode enabled in .env"}
    except (IOError, PermissionError) as e:
        from fastapi import HTTPException
        logger.error("Failed to write to .env file: %s", e)
        raise HTTPException(status_code=403, detail="Failed to write to .env file. Please edit it manually.")


@router.post("/system/purge-host-cache")
async def purge_host_cache():
    """Deletes the local LanceDB split-brain cache directory.
    Only valid when lancedb_mode == 'split_brain'. The next app restart
    will trigger a full re-sync from the SQLite embedding BLOBs.
    """
    if settings.lancedb_mode != "split_brain":
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400, detail="purge-host-cache is only available in split_brain mode."
        )

    import shutil

    cache_dir = settings.lancedb_persist_dir
    if os.path.isdir(cache_dir):
        shutil.rmtree(cache_dir, ignore_errors=True)
        return {"message": f"Host cache purged: {cache_dir}. Restart the backend to rebuild."}
    return {"message": "No host cache directory found — nothing to purge."}


@router.post("/system/compact-db")
async def compact_db(db: DatabaseManager = Depends(get_db)):
    if get_vacuum_lock().locked():
        return {"message": "Compaction already in progress."}

    # H-08: Multi-worker safety: Only allow worker 0 to run maintenance tasks.
    if int(os.environ.get("UVICORN_WORKER_ID", "0")) != 0:
        return {"message": "Compaction can only be triggered by the primary worker."}

    async def _do_vacuum():
        global _vacuum_last_run, _vacuum_last_error
        async with get_vacuum_lock():
            _vacuum_last_error = None
            try:
                # FTS optimize via aiosqlite (non-blocking in event loop terms)
                await db.fts_optimize()
                # VACUUM cannot run inside an active transaction and is CPU-bound.
                # Open a separate sync connection to avoid blocking the event loop
                # and to sidestep aiosqlite's implicit transaction wrapping.
                import sqlite3

                def _vacuum_sync():
                    con = sqlite3.connect(db.db_path, timeout=60)
                    con.isolation_level = None  # autocommit mode
                    con.execute("PRAGMA incremental_vacuum(100)")
                    con.close()

                await asyncio.to_thread(_vacuum_sync)
                from datetime import datetime

                _vacuum_last_run = datetime.now(UTC).isoformat()
                logger.info("DB vacuum completed.")
            except Exception as e:
                _vacuum_last_error = str(e)
                logger.error("Vacuum failed: %s", e)

    from app.state import bg_tasks as _bg_tasks

    t = asyncio.create_task(_do_vacuum())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return {"message": "Compaction started in background."}


@router.get("/system/compact-db/status")
async def compact_status():
    return {
        "is_running": get_vacuum_lock().locked(),
        "last_run": _vacuum_last_run,
        "error": _vacuum_last_error,
    }


@router.post("/system/clear-cache")
async def clear_cache():
    try:
        from app.state import file_tree_cache as _file_tree_cache
        from app.state import insights_cache as _insights_cache

        _file_tree_cache["data"] = _insights_cache["data"] = None
    except ImportError:
        pass

    from app.search.retrieval import clear_retrieval_cache

    clear_retrieval_cache()
    return {"message": "Caches cleared."}


@router.post("/demo/seed")
async def demo_seed(
    background_tasks: BackgroundTasks,
    db: DatabaseManager = Depends(get_db),
    emb=Depends(get_emb),
    lancedb_client=Depends(get_lancedb),
):
    from pathlib import Path

    base_dir_root = Path(__file__).parent.parent.parent
    demo_folder = str(base_dir_root / "demo_data")
    if not os.path.isdir(demo_folder):
        return JSONResponse(status_code=400, content={"error": "demo_data folder not found."})

    indexing_service_cls, _ = ensure_indexing()
    service = indexing_service_cls(db, emb, lancedb_client)

    try:
        from app.state import file_tree_cache as _file_tree_cache
        from app.state import insights_cache as _insights_cache

        _file_tree_cache["data"] = _insights_cache["data"] = None
    except ImportError:
        pass

    async def _demo_index_then_compact():
        await service.index_folders([demo_folder])
        try:
            await db.fts_optimize()
            import sqlite3

            def _vacuum_sync():
                con = sqlite3.connect(db.db_path, timeout=60)
                con.isolation_level = None
                con.execute("PRAGMA incremental_vacuum(100)")
                con.close()

            await asyncio.to_thread(_vacuum_sync)
            logger.info("Auto-compact completed after demo indexing.")
        except Exception as e:
            logger.warning("Auto-compact after demo indexing failed: %s", e)

    background_tasks.add_task(_demo_index_then_compact)
    return {"message": "Demo indexing started for demo_data folder.", "folder": demo_folder}


@router.get("/pick/folder")
async def pick_folder():
    # P2-2: This endpoint is deprecated in Tauri context.
    # Folder picking should be done via the native Tauri dialog plugin in the frontend.
    # Keeping for browser-mode dev compatibility only.
    def _dialog():
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        return filedialog.askdirectory(parent=root, title="Select Folder") or ""

    try:
        path = await asyncio.get_running_loop().run_in_executor(None, _dialog)
    except Exception as e:
        logger.warning("tkinter folder picker failed (expected in Tauri mode): %s", e)
        return {"path": "", "error": "Use the native Tauri dialog instead."}
    return {"path": path}

