"""HTTP surface for the OCR subsystem."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import get_db, get_ocr
from app.api.limiter import limiter
from app.config import settings
from app.ocr import cache as ocr_cache
from app.ocr import queue as ocr_queue
from app.ocr import registry
from app.ocr.settings import persist_enabled
from app.storage.db import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ocr", tags=["ocr"])


class ForceOcrPayload(BaseModel):
    file_path: str = Field(..., max_length=4096)


# Note: the DB comes from Depends(get_db), not from the manager's own handle,
# so these handlers honour dependency overrides the way the rest of the API
# layer does.


@router.get("/status")
async def ocr_status(db: DatabaseManager = Depends(get_db)):
    manager = await get_ocr()
    counts = await ocr_queue.counts(db)
    cache_bytes = await ocr_cache.total_bytes(db)
    return {
        **registry.tier_status(),
        **manager.runtime_state(),
        "queue": counts,
        "pages_pending": counts.get("pages_pending", 0),
        "cache_mb": round(cache_bytes / (1024 * 1024), 2),
        "cache_max_mb": settings.ocr_cache_max_mb,
    }


@router.get("/install/status")
async def install_status():
    return registry.get_install_state()


@router.post("/install")
@limiter.limit("3/minute")
async def install(request: Request):
    """Provision the CPU tier. Rate-limited because it spawns processes."""
    result = await registry.install_tier1()
    if result.get("status") == "ok":
        # The tier only becomes selectable once the install actually succeeded.
        settings.ocr_tier = "cpu"
        settings.ocr_enabled = True
        persist_enabled(True)
        manager = await get_ocr()
        manager.clear_fatal()
        await manager.kick()
    return result


@router.post("/install/cancel")
async def cancel_install():
    return {"ok": await registry.cancel_install()}


@router.post("/uninstall")
@limiter.limit("3/minute")
async def uninstall(request: Request):
    settings.ocr_tier = "none"
    settings.ocr_enabled = False
    persist_enabled(False)
    return await registry.uninstall_tier1()


class EnablePayload(BaseModel):
    enabled: bool


@router.post("/enable")
async def set_enabled(payload: EnablePayload):
    """Toggle OCR without uninstalling it.

    Refuses to enable when nothing is installed - `normalize_ocr` would
    silently flip it back and the UI would look broken.
    """
    from app.ocr.settings import is_tier_installed

    if payload.enabled and not is_tier_installed():
        return {"ok": False, "error_code": "TIER_NOT_INSTALLED", "enabled": False}

    settings.ocr_enabled = bool(payload.enabled)
    if payload.enabled:
        settings.ocr_tier = "cpu"
        await (await get_ocr()).kick()
    persist_enabled(settings.ocr_enabled)
    return {"ok": True, "enabled": settings.ocr_enabled}


@router.get("/queue")
async def list_queue(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: DatabaseManager = Depends(get_db),
):
    rows = await ocr_queue.list_queue(db, limit=limit, offset=offset, status=status)
    return {
        "items": [
            {
                "file_path": r.file_path,
                "file_name": Path(r.file_path).name,
                "page_count": r.page_count,
                "pages_done": r.pages_done,
                "pages_pending": max(r.page_count - r.pages_done, 0),
                "status": r.status.value,
                "attempts": r.attempts,
                "last_error": r.last_error,
                "updated_at": r.updated_at,
            }
            for r in rows
        ],
        "counts": await ocr_queue.counts(db),
    }


@router.post("/retry")
async def retry(payload: ForceOcrPayload, db: DatabaseManager = Depends(get_db)):
    """Re-arm a failed row and wake the drain loop."""
    row = await ocr_queue.get_row(db, payload.file_path)
    if row is None:
        return {"ok": False, "error_code": "NOT_QUEUED"}
    await ocr_queue.requeue(db, payload.file_path)
    manager = await get_ocr()
    manager.clear_fatal()
    await manager.kick()
    return {"ok": True}


@router.post("/force")
async def force_ocr(payload: ForceOcrPayload, db: DatabaseManager = Depends(get_db)):
    """Queue every page of a file, bypassing the detection gate.

    The escape hatch for the gate's known blind spot: a page whose text layer
    extracts successfully but wrongly (scrambled multi-column, bad CID map)
    looks NATIVE and is never queued automatically.
    """
    path = Path(payload.file_path)
    row = await db.get_file_by_path(str(path.absolute()))
    if row is None:
        return {"ok": False, "error_code": "NOT_INDEXED"}

    page_count = await _count_pdf_pages(path)
    if page_count <= 0:
        return {"ok": False, "error_code": "NOT_A_PDF"}

    await ocr_queue.enqueue_document(
        db,
        str(path.absolute()),
        list(range(page_count)),
        page_count,
        force=True,
        tier=settings.ocr_tier if settings.ocr_tier != "none" else "cpu",
    )
    await (await get_ocr()).kick()
    return {"ok": True, "pages_queued": page_count}


async def _count_pdf_pages(path: Path) -> int:
    import asyncio

    def _count() -> int:
        try:
            from pypdf import PdfReader

            return len(PdfReader(str(path), strict=False).pages)
        except Exception as exc:
            logger.debug("Could not count pages of %s: %s", path, exc)
            return 0

    return await asyncio.to_thread(_count)


@router.post("/queue/clear")
async def clear_queue(db: DatabaseManager = Depends(get_db)):
    return {"removed": await ocr_queue.clear_queue(db)}


@router.delete("/cache")
async def clear_cache(db: DatabaseManager = Depends(get_db)):
    """Wipe cached page text. Never done implicitly - `clear_all()` preserves it."""
    return {"removed": await ocr_cache.clear_cache(db)}
