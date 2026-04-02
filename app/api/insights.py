import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import (
    get_db, ensure_insights
)
from app.storage.db import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()

try:
    from app.main import _file_tree_cache, _insights_cache, _CACHE_TTL
except ImportError:
    _file_tree_cache = {"data": None, "ts": 0.0}
    _insights_cache = {"data": None, "ts": 0.0}
    _CACHE_TTL = 10

@router.get("/insights")
async def get_insights(db: DatabaseManager = Depends(get_db)):
    from app.main import _insights_cache, _CACHE_TTL
    now = time.time()
    if _insights_cache["data"] and (now - _insights_cache["ts"] < _CACHE_TTL):
        return _insights_cache["data"]

    insights_service_cls = ensure_insights()
    service = insights_service_cls(db)
    try:
        data = await service.get_dashboard_insights()
        _insights_cache["data"] = data
        _insights_cache["ts"] = now
        return data
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.get("/insights/by-type")
async def get_insights_by_type(extension: str = Query(...), db: DatabaseManager = Depends(get_db)):
    insights_service_cls = ensure_insights()
    service = insights_service_cls(db)
    try:
        return await service.get_insights_for_extension(extension)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.get("/files/tree")
async def get_files_tree(db: DatabaseManager = Depends(get_db)):
    from app.main import _file_tree_cache, _CACHE_TTL
    now = time.time()
    if _file_tree_cache["data"] and (now - _file_tree_cache["ts"] < _CACHE_TTL):
        return _file_tree_cache["data"]
    try:
        files = await db.get_all_files()
        folders = {}
        total_size = 0
        for f in files:
            tag = f.get("folder_tag", "Unknown")
            if tag not in folders:
                folders[tag] = []
            folders[tag].append({
                "id": f.get("id"),
                "path": f["path"],
                "size": f["size"],
                "type": f["type"],
                "usage_count": f.get("usage_count", 0)
            })
            total_size += f["size"]
            
        data = {
            "folders": folders,
            "total_files": len(files),
            "total_size": total_size
        }
        _file_tree_cache["data"] = data
        _file_tree_cache["ts"] = now
        return data
    except Exception as e:
        logger.error(f"Error building file tree: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

def importlib_os_sep():
    import os
    return os.sep

@router.get("/visualizer/stream")
async def stream_visualizer_binary(extension: Optional[str] = None, db: DatabaseManager = Depends(get_db)):
    """
    Streams files and folders as a raw 32-byte struct binary format for WebGPU.
    Optionally filters by file extension.
    """
    from app.insights.visualizer import _stream_visualizer_binary_impl
    try:
        return await _stream_visualizer_binary_impl(extension, db)
    except Exception as e:
        logger.error(f"Visualizer stream failed: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to stream visualizer data"})
