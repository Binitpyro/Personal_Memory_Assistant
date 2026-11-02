import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.api.deps import ensure_insights, get_db
from app.storage.db import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()

try:
    from app.state import CACHE_TTL as _CACHE_TTL
    from app.state import file_tree_cache as _file_tree_cache
    from app.state import insights_cache as _insights_cache
except ImportError:
    _file_tree_cache = {"data": None, "ts": 0.0}
    _insights_cache = {"data": None, "ts": 0.0}
    _CACHE_TTL = 10


@router.get("/insights")
async def get_insights(db: DatabaseManager = Depends(get_db)):
    from app.state import CACHE_TTL as _CACHE_TTL
    from app.state import insights_cache as _insights_cache

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


@router.get("/insights/portrait")
async def get_insights_portrait():
    from app.insights.portrait import generate_portrait

    try:
        data = await generate_portrait()
        return data
    except Exception as e:
        logger.error(f"Error generating portrait: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


def _common_parent_prefix(paths: list[str]) -> str:
    """Longest directory prefix shared by every path in *paths*.

    Compared over the *parent* of each path, never the path itself: with a
    single file the whole-path prefix would swallow the filename and the caller
    would strip a file name off as though it were a folder.

    Separator-preserving. `files.path` is written as `str(Path.absolute())`, so
    it carries the host separator, and the result has to stay a literal prefix
    of it for `delete_files_by_folder_prefix` to match.
    """
    if not paths:
        return ""
    split: list[list[str]] = []
    for p in paths:
        norm = p.replace("\\", "/")
        parts = norm.split("/")[:-1]  # drop the file name
        split.append(parts)
    first, shortest = split[0], min(len(x) for x in split)
    depth = 0
    while depth < shortest and all(x[depth] == first[depth] for x in split):
        depth += 1
    if depth == 0:
        return ""
    prefix = "/".join(first[:depth])
    # Re-spell using the separator the original paths actually used.
    sep = "\\" if "\\" in paths[0] else "/"
    return prefix.replace("/", sep)


@router.get("/files/tree")
async def get_files_tree(db: DatabaseManager = Depends(get_db)):
    from app.state import CACHE_TTL as _CACHE_TTL
    from app.state import file_tree_cache as _file_tree_cache

    now = time.time()
    if _file_tree_cache["data"] and (now - _file_tree_cache["ts"] < _CACHE_TTL):
        return _file_tree_cache["data"]
    try:
        files = await db.get_all_files()
        # Keyed by the indexed folder's full path. The Explorer strips this key
        # off each file path to build its tree and passes it to the folder
        # removal endpoint, so a basename will not do.
        folders: dict[str, list[dict[str, Any]]] = {}
        # Rows written before the files_root_path migration carry an empty
        # root_path. Park them under their folder_tag, then derive a root by
        # common prefix once the group is complete, so an existing index gets the
        # corrected tree without a re-index. That fallback cannot separate two
        # folders sharing a basename -- only a re-index can.
        legacy: dict[str, list[dict[str, Any]]] = {}
        total_size = 0
        for f in files:
            # Row object does not have .get()
            entry = {
                "id": f["id"],
                "path": f["path"],
                "size": f["size"] or 0,
                "type": f["type"],
                "usage_count": f["usage_count"] or 0,
            }
            root = f["root_path"] or ""
            if root:
                folders.setdefault(root, []).append(entry)
            else:
                tag = f["folder_tag"] or "Unknown"
                legacy.setdefault(tag, []).append(entry)
            total_size += f["size"] or 0

        for tag, entries in legacy.items():
            root = _common_parent_prefix([e["path"] for e in entries]) or tag
            folders.setdefault(root, []).extend(entries)

        data = {"folders": folders, "total_files": len(files), "total_size": total_size}
        _file_tree_cache["data"] = data
        _file_tree_cache["ts"] = now
        return data
    except Exception as e:
        logger.error(f"Error building file tree: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/visualizer/stream")
async def stream_visualizer_binary(
    extension: str | None = None, db: DatabaseManager = Depends(get_db)
):
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


@router.get("/visualizer/meta")
async def get_visualizer_meta(extension: str | None = None, db: DatabaseManager = Depends(get_db)):
    """type_hash → {name, path, size, usage_count} sidecar for the 3D visualizer."""
    from app.insights.visualizer import get_visualizer_meta_impl

    try:
        return await get_visualizer_meta_impl(extension, db)
    except Exception as e:
        logger.error(f"Visualizer meta failed: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to build visualizer meta"})
