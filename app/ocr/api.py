"""HTTP surface for the OCR subsystem."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app import state as app_state
from app.api.deps import get_db, get_ocr
from app.api.limiter import limiter
from app.config import settings
from app.ocr import cache as ocr_cache
from app.ocr import queue as ocr_queue
from app.ocr import registry
from app.ocr.settings import persist_enabled
from app.providers import env_base_url
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


class InstallPayload(BaseModel):
    tier: str = "cpu"


@router.get("/tiers")
async def list_tiers():
    """Tiers this build knows about and whether each can run on this machine.

    Mirrors the `uv_available` precedent: the UI must be able to say *before* a
    several-hundred-megabyte download whether a tier is even installable here,
    rather than finding out afterwards.
    """
    from app.ocr.settings import VLM_TIER, is_tier_installed, vlm_selection

    tiers = [
        {
            "id": tier,
            "unavailable_reason": registry.unavailable_reason(tier),
            "installed": is_tier_installed(tier),
            "active": tier == settings.ocr_tier and bool(settings.ocr_enabled),
            # Engine tiers are provisioned; the VLM tier is chosen. The UI needs
            # to know which, because "Install" is the wrong control for a model
            # PMA does not download.
            "needs_install": True,
        }
        for tier in sorted(registry.TIER_DEPS)
    ]
    tiers.append(
        {
            "id": VLM_TIER,
            "unavailable_reason": "",
            "installed": bool(vlm_selection()),
            "active": settings.ocr_tier == VLM_TIER and bool(settings.ocr_enabled),
            "needs_install": False,
        }
    )
    return {"installed": settings.ocr_tier, "tiers": tiers}


@router.get("/vlm/models")
async def list_vlm_models():
    """Vision models the user already has, per local provider.

    PMA does not download these - the user pulls them in Ollama or LM Studio
    themselves - so the picker's job is to show what is actually present and,
    when nothing is, name models that are worth pulling. Reaching the provider
    only lists model names; no document content leaves the machine here.
    """
    from app.providers import create_provider, is_loopback_url
    from app.providers.registry import PROVIDER_REGISTRY
    from app.providers.vision import (
        SUGGESTED_VISION_MODELS,
        looks_like_vision_model,
        supports_vision_messages,
    )

    results = []
    any_vision = False

    for pid in ("ollama", "lm_studio"):
        spec = PROVIDER_REGISTRY.get(pid)
        if spec is None or not supports_vision_messages(pid):
            continue

        base_url = env_base_url(pid) or spec.default_base_url
        entry: dict[str, Any] = {
            "provider": pid,
            "display_name": spec.display_name,
            "base_url": base_url,
            # Surfaced so the UI can warn before page images are sent off-box;
            # the dispatch path refuses without consent regardless.
            "is_local": is_loopback_url(base_url),
            "reachable": False,
            "models": [],
            "error": None,
        }
        try:
            # Use 2.0s connect/read timeout for local probes to keep the UI snappy
            provider = create_provider(pid, api_key=None, base_url=base_url, default_model=None, timeout=2.0)
            try:
                models = await provider.list_models()
            finally:
                await provider.close()
            entry["reachable"] = True
            entry["models"] = [
                {"id": m["id"], "vision": looks_like_vision_model(str(m["id"]))} for m in models
            ]
            any_vision = any_vision or any(m["vision"] for m in entry["models"])
        except Exception as exc:
            # Not running is the normal case, not an error worth a 500.
            entry["error"] = type(exc).__name__
        results.append(entry)

    return {
        "providers": results,
        "has_vision_model": any_vision,
        # Only meaningful when nothing was found; harmless to always include.
        "suggestions": list(SUGGESTED_VISION_MODELS),
    }


class VlmSelectionPayload(BaseModel):
    provider: str = Field(..., max_length=64)
    model: str = Field(..., max_length=256)


@router.post("/vlm/select")
async def select_vlm(payload: VlmSelectionPayload):
    """Choose the vision model for Tier 3, and switch to it.

    Refuses a provider whose message format we cannot build - sending a
    text-only message would make the model describe nothing, and that would be
    cached and indexed as the page's text.
    """
    from app.ocr.settings import VLM_TIER, persist_active_tier, persist_vlm_selection
    from app.providers.vision import supports_vision_messages

    if not supports_vision_messages(payload.provider):
        return {"ok": False, "error_code": "PROVIDER_CANNOT_SEND_IMAGES"}

    persist_vlm_selection(payload.provider, payload.model)
    persist_active_tier(VLM_TIER)
    settings.ocr_tier = VLM_TIER
    settings.ocr_enabled = True
    persist_enabled(True)

    manager = await get_ocr()
    manager.clear_fatal()
    # A different model transcribes differently, so it must not read the
    # previous one's cached pages.
    manager.reset_engine_identity()
    await manager.kick()
    return {"ok": True, "provider": payload.provider, "model": payload.model}


@router.get("/vlm/selection")
async def get_vlm_selection():
    from app.ocr.settings import vlm_selection

    return {"selection": vlm_selection() or None}


class SelectTierPayload(BaseModel):
    tier: str = Field(..., max_length=32)


@router.post("/select")
async def select_tier(payload: SelectTierPayload):
    """Switch active OCR engine to an already installed tier (or configured VLM)."""
    from app.ocr.settings import VLM_TIER, is_tier_installed, persist_active_tier, vlm_selection

    tier = payload.tier.strip().lower()
    if tier == VLM_TIER:
        if not vlm_selection():
            return {"ok": False, "error_code": "NO_VLM_MODEL_SELECTED"}
    elif tier in registry.TIER_DEPS:
        if not is_tier_installed(tier):
            return {"ok": False, "error_code": "TIER_NOT_INSTALLED"}
    else:
        return {"ok": False, "error_code": "UNKNOWN_TIER"}

    settings.ocr_tier = tier
    settings.ocr_enabled = True
    persist_enabled(True)
    persist_active_tier(tier)

    manager = await get_ocr()
    manager.clear_fatal()
    manager.reset_engine_identity()
    await manager.kick()
    return {"ok": True, "tier": tier}


@router.post("/install")
@limiter.limit("3/minute")
async def install(request: Request, payload: InstallPayload | None = None):
    """Start provisioning the CPU tier. Returns immediately.

    Rate-limited because it spawns processes.

    This used to await the whole provision - minutes, with a 1800s dependency
    timeout - so the client's promise did not resolve until it was over. The UI
    derives "installing" from a status of "running", which it can only learn by
    polling, and it only polls while "installing": the progress bar and the
    cancel button were therefore unreachable, and a failure left the button
    disabled forever. Returning straight away is what makes both usable.
    """
    tier = (payload.tier if payload else "cpu") or "cpu"
    if not registry.begin_install():
        return registry.get_install_state()

    async def _provision() -> None:
        from app.ocr.settings import persist_active_tier

        result = await registry.install_tier(tier, _armed=True)
        if result.get("status") != "ok":
            return
        # The tier only becomes selectable once the install actually succeeded.
        settings.ocr_tier = tier
        settings.ocr_enabled = True
        persist_enabled(True)
        persist_active_tier(tier)
        manager = await get_ocr()
        manager.clear_fatal()
        # The engine just changed on disk; a remembered identity from the
        # previous install would key the cache wrongly until the next restart.
        manager.reset_engine_identity()
        await manager.kick()

    task = asyncio.create_task(_provision())
    # Registered so shutdown awaits it rather than orphaning a uv subprocess.
    app_state.bg_tasks.add(task)
    task.add_done_callback(app_state.bg_tasks.discard)
    return registry.get_install_state()


@router.post("/install/cancel")
async def cancel_install():
    return {"ok": await registry.cancel_install()}


class UninstallPayload(BaseModel):
    tier: str | None = None


@router.post("/uninstall")
@limiter.limit("3/minute")
async def uninstall(request: Request, payload: UninstallPayload | None = None):
    from app.ocr.settings import detect_installed_tier, persist_active_tier

    target_tier = (payload.tier if payload and payload.tier else settings.ocr_tier) or "cpu"
    res = await registry.uninstall_tier(target_tier)

    # If the uninstalled tier was active, fall back to another installed tier or disable
    if settings.ocr_tier == target_tier or target_tier == "all":
        remaining = detect_installed_tier()
        if remaining:
            settings.ocr_tier = remaining
            persist_active_tier(remaining)
        else:
            settings.ocr_tier = "none"
            settings.ocr_enabled = False
            persist_enabled(False)
            persist_active_tier("none")

    (await get_ocr()).reset_engine_identity()
    return res


class EnablePayload(BaseModel):
    enabled: bool


@router.post("/enable")
async def set_enabled(payload: EnablePayload):
    """Toggle OCR without uninstalling it.

    Refuses to enable when nothing is installed - `normalize_ocr` would
    silently flip it back and the UI would look broken.
    """
    from app.ocr.settings import detect_installed_tier

    installed = detect_installed_tier()
    if payload.enabled and not installed:
        return {"ok": False, "error_code": "TIER_NOT_INSTALLED", "enabled": False}

    settings.ocr_enabled = bool(payload.enabled)
    if payload.enabled:
        # The installed tier names itself. Forcing "cpu" here made a toggle
        # silently relabel any other tier, and that label reaches the cache key.
        settings.ocr_tier = installed
        await (await get_ocr()).kick()
    persist_enabled(settings.ocr_enabled)
    return {"ok": True, "enabled": settings.ocr_enabled}


@router.post("/resume")
async def resume():
    """Clear a fatal stop and wake the drain loop.

    `_fatal` halts every document, not just the one that tripped it, and the
    only other way to clear it was the per-file Retry button - which renders
    only when there are failed rows. A fatal raised during the worker handshake
    produces no failed rows at all, so that state had no reachable exit and the
    user's only documented option was to reinstall the tier.
    """
    manager = await get_ocr()
    manager.clear_fatal()
    await manager.kick()
    return {"ok": True}


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
