import logging
import math
import struct
import zlib

from fastapi import Response

from app.config import settings
from app.storage.db import DatabaseManager

logger = logging.getLogger(__name__)

# Appended verbatim to BOTH visualizer queries. `id` is the stable insertion
# order and is the primary key, so the ordering is total and index-backed. Both
# endpoints must use this exact clause: the meta sidecar joins to the binary
# buffer by hash_tree_path(path), and a LIMIT without a deterministic ORDER BY
# lets SQLite hand the two calls different subsets, which silently drops nodes
# from the join rather than failing.
_ORDER_AND_LIMIT = " ORDER BY id LIMIT ?"


def _warn_if_truncated(row_count: int, which: str) -> None:
    """Say so in the log when the cap bound the result.

    Deliberately a log line rather than a field in the response: the meta body
    is a hash-keyed map the frontend reads by lookup, and adding a sibling key
    of a different shape to it would change a typed contract for a condition
    that needs a corpus of settings.visualizer_max_nodes files to reach. The
    operator needs to know; the renderer does not have anything useful to do
    about it.
    """
    if row_count >= settings.visualizer_max_nodes:
        logger.warning(
            "Visualizer %s hit the %d-node cap (settings.visualizer_max_nodes); "
            "the view is showing a bounded prefix of the corpus, not all of it.",
            which,
            settings.visualizer_max_nodes,
        )


try:
    import rust_core  # type: ignore

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False
    logger.warning("rust_core not available — visualizer will use Python fallback layout")


def _python_fallback_binary(rows: list) -> bytes:
    """
    Fallback: Fibonacci sphere layout with a fake root folder.
    Used only when rust_core is not importable.
    Buffer layout matches the Rust Node struct (32 bytes per node).
    """
    parts = []

    # Insert a fake root folder at index 0
    parts.append(
        struct.pack(
            "<ffffIIII",
            0.0,
            0.0,
            0.0,  # x, y, z
            100.0,  # radius large enough to enclose
            0xFFFFFFFF,  # root / no parent
            1,  # flags (1 = folder)
            0,  # type_hash
            0,  # pad
        )
    )

    n = len(rows)
    if n == 0:
        return b"".join(parts)

    phi = math.pi * (3.0 - math.sqrt(5.0))
    for i, row in enumerate(rows):
        y = 1 - (i / float(n - 1)) * 2 if n > 1 else 0
        r = math.sqrt(1 - y * y)
        theta = phi * i
        x = math.cos(theta) * r
        z = math.sin(theta) * r

        scale = 50.0  # Spacing
        x *= scale
        y *= scale
        z *= scale

        ext = (row["type"] or ".bin").lower()
        type_hash = zlib.crc32(ext.encode("utf-8")) & 0xFFFFFFFF

        parts.append(
            struct.pack(
                "<ffffIIII",
                x,
                y,
                z,
                2.0,  # arbitrary radius for bubble
                0,  # parent_idx (0 = the fake root folder)
                0,  # flags (0 = file)
                type_hash,
                0,  # pad
            )
        )
    return b"".join(parts)


async def _stream_visualizer_binary_impl(extension: str | None, db: DatabaseManager):
    """
    Implementation of the binary stream for the WebGPU visualizer.
    """
    query = "SELECT id, path, type, size FROM files"
    params: list = []
    if extension:
        clean_ext = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        query += " WHERE type = ?"
        params.append(clean_ext)
    # Same ORDER BY and same limit as get_visualizer_meta_impl - see
    # settings.visualizer_max_nodes. The sidecar joins to this buffer by
    # hash_tree_path(path), so the two calls must agree on which rows exist.
    query += _ORDER_AND_LIMIT
    params.append(settings.visualizer_max_nodes)

    try:
        rows = await db.execute_query(query, tuple(params))
        _warn_if_truncated(len(rows), "binary stream")

        if _RUST_AVAILABLE and hasattr(rust_core, "get_spatial_binary"):
            file_tuples = [
                (row["path"] or "", float(row["size"] or 0), row["type"] or ".bin") for row in rows
            ]
            raw_buf = rust_core.get_spatial_binary(file_tuples)
            buf = bytes(raw_buf) if isinstance(raw_buf, list) else raw_buf
        else:
            if _RUST_AVAILABLE:
                logger.warning(
                    "rust_core is loaded but missing get_spatial_binary (likely an outdated DLL is locked). Falling back to Python layout."
                )
            buf = _python_fallback_binary(rows)

        return Response(content=buf, media_type="application/octet-stream")
    except Exception as e:
        logger.error(f"Error in visualizer binary stream: {e}")
        raise


async def get_visualizer_meta_impl(extension: str | None, db: DatabaseManager) -> dict:
    """
    Sidecar metadata for the binary visualizer stream, keyed by the same
    type_hash rust_core writes into each Node. Folders aggregate size,
    usage and file count from their descendants.
    """
    if not (_RUST_AVAILABLE and hasattr(rust_core, "hash_tree_path")):
        # Python fallback layout hashes extensions, not paths — no join possible.
        return {}

    query = "SELECT path, type, size, usage_count FROM files"
    params: list = []
    if extension:
        clean_ext = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        query += " WHERE type = ?"
        params.append(clean_ext)
    # Must match _stream_visualizer_binary_impl exactly - the join below is by
    # hash_tree_path(path), so a different row set here silently drops nodes.
    query += _ORDER_AND_LIMIT
    params.append(settings.visualizer_max_nodes)

    rows = await db.execute_query(query, tuple(params))
    _warn_if_truncated(len(rows), "meta")
    meta: dict[int, dict] = {}
    folders: dict[str, dict] = {}

    for row in rows:
        path = (row["path"] or "").replace("\\", "/")
        if not path:
            continue
        size = int(row["size"] or 0)
        usage = int(row["usage_count"] or 0)

        h = rust_core.hash_tree_path(path)
        meta[h] = {
            "name": path.rsplit("/", 1)[-1],
            "path": path,
            "size": size,
            "usage_count": usage,
            "is_folder": False,
        }

        # Aggregate every ancestor folder (same cumulative components build_tree hashes).
        parts = [p for p in path.split("/") if p]
        current = ""
        for part in parts[:-1]:
            current = f"{current}/{part}" if current else part
            f = folders.setdefault(current, {"size": 0, "usage_count": 0, "file_count": 0})
            f["size"] += size
            f["usage_count"] += usage
            f["file_count"] += 1

    for folder_path, agg in folders.items():
        h = rust_core.hash_tree_path(folder_path)
        meta[h] = {
            "name": folder_path.rsplit("/", 1)[-1],
            "path": folder_path,
            "size": agg["size"],
            "usage_count": agg["usage_count"],
            "file_count": agg["file_count"],
            "is_folder": True,
        }

    return meta
