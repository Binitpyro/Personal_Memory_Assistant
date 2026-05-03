import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import get_llm

logger = logging.getLogger(__name__)

models_router = APIRouter(prefix="/llm", tags=["llm"])

SETTINGS_PATH = Path("data/settings.json")


class LLMPreferences(BaseModel):
    provider: str = "auto"  # auto | gemini | ollama | lm_studio
    gemini_model: str | None = None
    ollama_model: str | None = None
    lm_studio_model: str | None = None


def _read_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_settings(data: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


@models_router.get("/preferences")
async def get_preferences() -> dict[str, Any]:
    data = await asyncio.to_thread(_read_settings)
    llm_prefs = data.get("llm", {})
    return {
        "provider": llm_prefs.get("provider", "auto"),
        "gemini_model": llm_prefs.get("gemini_model"),
        "ollama_model": llm_prefs.get("ollama_model"),
        "lm_studio_model": llm_prefs.get("lm_studio_model"),
    }


@models_router.post("/preferences")
async def set_preferences(payload: LLMPreferences) -> dict[str, Any]:
    normalized_provider = (payload.provider or "auto").lower()
    if normalized_provider not in {"auto", "gemini", "ollama", "lm_studio"}:
        normalized_provider = "auto"

    data = await asyncio.to_thread(_read_settings)
    data["llm"] = {
        "provider": normalized_provider,
        "gemini_model": payload.gemini_model,
        "ollama_model": payload.ollama_model,
        "lm_studio_model": payload.lm_studio_model,
    }
    await asyncio.to_thread(_write_settings, data)

    # Apply at runtime to current singleton LLM client
    llm = get_llm()
    llm.apply_preferences(
        provider=normalized_provider,
        gemini_model=payload.gemini_model,
        ollama_model=payload.ollama_model,
        lm_studio_model=payload.lm_studio_model,
    )

    return {"message": "LLM preferences saved.", "llm": data["llm"]}


@models_router.get("/detect")
async def detect_local_models() -> dict[str, Any]:
    """
    Rapidly probes standard local ports to detect active LLM providers.
    Returns the list of available models for each provider.
    """
    results = {
        "ollama": {"detected": False, "models": []},
        "lm_studio": {"detected": False, "models": []},
    }

    # Use a very short timeout so the UI doesn't hang if they are offline
    async with httpx.AsyncClient(timeout=1.5) as client:
        # Probe Ollama
        try:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("name") for m in data.get("models", [])]
                results["ollama"] = {"detected": True, "models": models}
        except Exception as e:
            logger.debug("Ollama detection skipped: %s", e)

        # Probe LM Studio (OpenAI compatible endpoint)
        try:
            resp = await client.get("http://localhost:1234/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id") for m in data.get("data", [])]
                results["lm_studio"] = {"detected": True, "models": models}
        except Exception as e:
            logger.debug("LM Studio detection skipped: %s", e)

    return results
