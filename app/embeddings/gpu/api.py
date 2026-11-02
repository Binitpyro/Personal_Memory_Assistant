"""HTTP surface for opt-in GPU embedding. Mirrors app/ocr/api.py's install flow."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import state as app_state
from app.api.deps import get_emb
from app.api.limiter import limiter
from app.embeddings.gpu import registry

router = APIRouter(prefix="/embedding/gpu", tags=["embedding"])


@router.get("")
async def gpu_status() -> dict[str, Any]:
    return registry.status(get_emb().gpu_status())


@router.post("/install")
@limiter.limit("3/minute")
async def install(request: Request) -> dict[str, Any]:
    """Start provisioning; returns at once, poll GET for progress. Never enables."""
    if registry.begin_install():
        task = asyncio.create_task(registry.install())
        # Registered so shutdown awaits it rather than orphaning a uv subprocess.
        app_state.bg_tasks.add(task)
        task.add_done_callback(app_state.bg_tasks.discard)
    return registry.status(get_emb().gpu_status())


@router.post("/install/cancel")
async def cancel_install() -> dict[str, bool]:
    registry.cancel_install()
    return {"ok": True}


class EnablePayload(BaseModel):
    on: bool


@router.post("/enable")
async def enable(payload: EnablePayload) -> dict[str, Any]:
    """Save the device for the next start. Either direction needs a re-embed."""
    if payload.on and (registry.unavailable_reason() or not registry.is_installed()):
        return {"ok": False, "error_code": "NOT_INSTALLED"}
    registry.persist_device("gpu" if payload.on else "cpu")
    return {"ok": True, **registry.status(get_emb().gpu_status())}
