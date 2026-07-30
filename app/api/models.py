import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app.api.deps import get_llm
from app.search.llm_client import ProviderNotConfiguredError

logger = logging.getLogger(__name__)

models_router = APIRouter(prefix="/llm", tags=["llm"])

SETTINGS_PATH = Path("data/settings.json")


PRIVACY_NOTICE = (
    "Free-tier cloud dispatches (such as Google Gemini free tier) may use data inputs for model "
    "training/improvement per provider terms and are restricted for EEA, Switzerland, and UK users. "
    "Explicit user consent (cloud_privacy_consent=true) is required prior to enabling cloud dispatches."
)


class LLMPreferences(BaseModel):
    provider: str = "auto"  # auto | gemini | ollama | lm_studio
    gemini_model: str | None = None
    ollama_model: str | None = None
    lm_studio_model: str | None = None
    cloud_privacy_consent: bool = False


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
async def get_preferences(response: Response = None) -> dict[str, Any]:  # type: ignore[assignment]
    if response:
        response.headers["X-Deprecated"] = "true"
    data = await asyncio.to_thread(_read_settings)
    llm_prefs = data.get("llm", {})
    return {
        "provider": llm_prefs.get("provider", "auto"),
        "gemini_model": llm_prefs.get("gemini_model"),
        "ollama_model": llm_prefs.get("ollama_model"),
        "lm_studio_model": llm_prefs.get("lm_studio_model"),
        "cloud_privacy_consent": llm_prefs.get("cloud_privacy_consent", False),
        "cloud_privacy_notice": PRIVACY_NOTICE,
    }


@models_router.post("/preferences")
async def set_preferences(payload: LLMPreferences, response: Response = None) -> dict[str, Any]:  # type: ignore[assignment]
    if response:
        response.headers["X-Deprecated"] = "true"
    normalized_provider = (payload.provider or "auto").lower()
    if normalized_provider not in {"auto", "gemini", "ollama", "lm_studio"}:
        normalized_provider = "auto"

    if normalized_provider in {"gemini"} and not payload.cloud_privacy_consent:
        raise HTTPException(
            status_code=400,
            detail=(
                "Explicit consent (cloud_privacy_consent=true) is required to select cloud providers. "
                "Free-tier cloud dispatches may use inputs for model training and are restricted in EEA/CH/UK."
            ),
        )

    data = await asyncio.to_thread(_read_settings)
    data["llm"] = {
        "provider": normalized_provider,
        "gemini_model": payload.gemini_model,
        "ollama_model": payload.ollama_model,
        "lm_studio_model": payload.lm_studio_model,
        "cloud_privacy_consent": payload.cloud_privacy_consent,
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
async def detect_local_models(response: Response = None) -> dict[str, Any]:  # type: ignore[assignment]
    """
    Rapidly probes standard local ports to detect active LLM providers.
    Returns the list of available models for each provider.
    """
    if response:
        response.headers["X-Deprecated"] = "true"
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


class ChatMessage(BaseModel):
    role: str
    content: str


class LLMChatRequest(BaseModel):
    messages: list[ChatMessage]
    provider: str
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096


@models_router.post("/chat")
async def chat_passthrough(payload: LLMChatRequest) -> dict[str, Any]:
    """
    Generic LLM passthrough endpoint for sidecars (e.g. Creative Module).
    Requires an explicit provider ID (rejects 'auto').
    """
    provider_id = (payload.provider or "").strip().lower()
    if not provider_id or provider_id == "auto":
        raise HTTPException(
            status_code=400,
            detail="Explicit provider is required. 'auto' is not allowed for sidecar passthrough dispatches.",
        )

    llm = get_llm()
    try:
        provider_instance = await llm._resolve_provider_by_id(
            provider_id, model_override=payload.model
        )
    except ProviderNotConfiguredError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to resolve provider '%s': %s", provider_id, e)
        raise HTTPException(status_code=502, detail=f"Provider '{provider_id}' resolution failed: {e}")

    try:
        messages_dicts = [m.model_dump() for m in payload.messages]
        content = await provider_instance.chat(
            messages=messages_dicts,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )
        resolved_model = payload.model or getattr(provider_instance, "model_name", None) or "default"
        return {
            "provider": provider_id,
            "model": resolved_model,
            "content": content,
        }
    except Exception as e:
        logger.error("Error during LLM passthrough chat on provider '%s': %s", provider_id, e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Provider '{provider_id}' error: {e}")
    finally:
        if hasattr(provider_instance, "close"):
            try:
                await provider_instance.close()
            except Exception:
                pass

