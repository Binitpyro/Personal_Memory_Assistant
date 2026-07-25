import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import keyring
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Extra

from app.config import settings
from app.providers import (
    PROVIDER_IDS,
    PROVIDER_REGISTRY,
    create_provider,
    get_configured_provider_ids,
)
from app.providers.base import ValidationResult
from app.providers.cache import validation_cache

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task[Any]] = set()

providers_router = APIRouter(prefix="/providers", tags=["providers"])

SETTINGS_PATH = Path("data/settings.json")


def read_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_settings(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def migrate_settings_if_needed(data: dict) -> dict:
    if "llm" not in data:
        data["llm"] = {}
    llm = data["llm"]

    if "per_provider" not in llm:
        llm["per_provider"] = {}

    per_provider = llm["per_provider"]

    # Migrate old keys
    if "gemini_model" in llm and "gemini" not in per_provider:
        per_provider["gemini"] = {"base_url": None, "default_model": llm["gemini_model"]}
    if "ollama_model" in llm and "ollama" not in per_provider:
        per_provider["ollama"] = {"base_url": None, "default_model": llm["ollama_model"]}
    if "lm_studio_model" in llm and "lm_studio" not in per_provider:
        per_provider["lm_studio"] = {"base_url": None, "default_model": llm["lm_studio_model"]}

    for pid in PROVIDER_IDS:
        if pid not in per_provider:
            per_provider[pid] = {"base_url": None, "default_model": None}

    if "provider" not in llm:
        llm["provider"] = "auto"

    if "fallback_chain" not in llm:
        llm["fallback_chain"] = get_configured_provider_ids()

    return data


class ValidatePayload(BaseModel):
    api_key: str | None = None
    base_url: str | None = None

    class Config:
        extra = Extra.ignore


class SetKeyPayload(BaseModel):
    api_key: str | None = None
    base_url: str | None = None

    class Config:
        extra = Extra.ignore


class SetDefaultModelPayload(BaseModel):
    model: str


class LLMGeneralSettingsPayload(BaseModel):
    provider: str | None = None
    fallback_chain: list[str] | None = None

    class Config:
        extra = Extra.ignore


@providers_router.get("/settings")
async def get_llm_settings():
    data = await asyncio.to_thread(read_settings)
    data = migrate_settings_if_needed(data)
    llm = data.get("llm", {})
    return {
        "provider": llm.get("provider", "auto"),
        "fallback_chain": llm.get("fallback_chain") or get_configured_provider_ids(),
    }


@providers_router.put("/settings")
async def update_llm_settings(payload: LLMGeneralSettingsPayload):
    data = await asyncio.to_thread(read_settings)
    data = migrate_settings_if_needed(data)
    if payload.provider is not None:
        data["llm"]["provider"] = payload.provider
    if payload.fallback_chain is not None:
        for pid in payload.fallback_chain:
            if pid not in PROVIDER_IDS:
                raise HTTPException(
                    status_code=400, detail=f"Invalid provider ID in fallback chain: {pid}"
                )
        data["llm"]["fallback_chain"] = payload.fallback_chain
    await asyncio.to_thread(write_settings, data)
    return {"status": "success"}


@providers_router.get("")
async def list_providers():
    data = await asyncio.to_thread(read_settings)
    data = migrate_settings_if_needed(data)
    llm = data.get("llm", {})
    per_provider = llm.get("per_provider", {})

    results = []
    for pid in PROVIDER_IDS:
        spec = PROVIDER_REGISTRY[pid]

        is_set = False
        preview = None
        stored_in = None

        env_key_name = f"{pid}_api_key"
        env_key = getattr(settings, env_key_name, None)
        if env_key:
            is_set = True
            preview = env_key[:6] + "..." if len(env_key) > 6 else "****"
            stored_in = "env"
        else:
            try:
                key = await asyncio.to_thread(keyring.get_password, "pma_backend", pid)
                if key:
                    is_set = True
                    preview = key[:6] + "..." if len(key) > 6 else "****"
                    stored_in = "keyring"
            except Exception:  # nosec B110
                pass

        provider_settings = per_provider.get(pid, {})
        base_url = provider_settings.get("base_url") or spec.default_base_url
        default_model = provider_settings.get("default_model")

        api_key = env_key
        if not api_key:
            try:
                api_key = await asyncio.to_thread(keyring.get_password, "pma_backend", pid)
            except Exception:
                api_key = None

        last_validation = validation_cache.get(pid, base_url, api_key)

        # Trigger background validation if no validation result is cached yet for active providers
        if last_validation is None and (is_set or pid in ("ollama", "lm_studio")):
            try:
                p_obj = create_provider(
                    pid, api_key=api_key, base_url=base_url, default_model=default_model
                )
                task = asyncio.create_task(p_obj.validate())
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
            except Exception as e:
                logger.debug("Failed to spawn background validation for %s: %s", pid, e)

        results.append(
            {
                "spec": spec,
                "is_set": is_set,
                "preview": preview,
                "stored_in": stored_in,
                "base_url": base_url,
                "default_model": default_model,
                "last_validation": last_validation,
            }
        )

    return results


@providers_router.post("/{provider_id}/validate")
async def validate_provider(provider_id: str, payload: ValidatePayload) -> ValidationResult:
    if provider_id not in PROVIDER_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_id}")

    api_key = payload.api_key
    base_url = payload.base_url

    data = await asyncio.to_thread(read_settings)
    data = migrate_settings_if_needed(data)
    per_provider = data.get("llm", {}).get("per_provider", {})
    provider_settings = per_provider.get(provider_id, {})

    if base_url is None:
        base_url = provider_settings.get("base_url")

    if api_key is None:
        env_key_name = f"{provider_id}_api_key"
        api_key = getattr(settings, env_key_name, None)
        if not api_key:
            try:
                api_key = await asyncio.to_thread(keyring.get_password, "pma_backend", provider_id)
            except Exception:
                api_key = None

    provider = create_provider(
        provider_id,
        api_key=api_key,
        base_url=base_url,
        default_model=provider_settings.get("default_model"),
    )
    try:
        res = await provider.validate()
        return res
    finally:
        await provider.close()


@providers_router.post("/{provider_id}/self_test")
async def self_test_provider(provider_id: str):
    if provider_id not in PROVIDER_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_id}")

    data = await asyncio.to_thread(read_settings)
    data = migrate_settings_if_needed(data)
    per_provider = data.get("llm", {}).get("per_provider", {})
    provider_settings = per_provider.get(provider_id, {})
    base_url = provider_settings.get("base_url")
    default_model = provider_settings.get("default_model")

    env_key_name = f"{provider_id}_api_key"
    api_key = getattr(settings, env_key_name, None)
    if not api_key:
        try:
            api_key = await asyncio.to_thread(keyring.get_password, "pma_backend", provider_id)
        except Exception:
            api_key = None

    provider = create_provider(
        provider_id, api_key=api_key, base_url=base_url, default_model=default_model
    )

    try:
        test_model = default_model
        if not test_model:
            models = await provider.list_models()
            if models:
                test_model = models[0]["id"]
            else:
                raise HTTPException(status_code=400, detail="No models available to test.")

        messages = [{"role": "user", "content": "Reply with the single word: ok"}]
        start_time = time.time()

        res = await provider.chat(messages, model=test_model, temperature=0.0, max_tokens=5)
        latency = int((time.time() - start_time) * 1000)

        ok = "ok" in res.lower()
        return {"ok": ok, "latency_ms": latency, "response": res, "model_used": test_model}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        await provider.close()


@providers_router.put("/{provider_id}/key")
@providers_router.post("/{provider_id}/key")
async def set_provider_key(provider_id: str, payload: SetKeyPayload):
    if provider_id not in PROVIDER_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_id}")

    env_key_name = f"{provider_id}_api_key"
    if getattr(settings, env_key_name, None):
        raise HTTPException(
            status_code=400,
            detail=f"Provider {provider_id} is managed by .env and cannot be changed via API.",
        )

    if payload.api_key is not None:
        try:
            await asyncio.to_thread(
                keyring.set_password, "pma_backend", provider_id, payload.api_key
            )
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to write to OS keyring: {e!s}"
            ) from e

    if payload.base_url is not None:
        spec = PROVIDER_REGISTRY[provider_id]
        if not spec.base_url_editable:
            raise HTTPException(
                status_code=400, detail=f"Base URL is not editable for provider {provider_id}."
            )

        data = await asyncio.to_thread(read_settings)
        data = migrate_settings_if_needed(data)
        data["llm"]["per_provider"][provider_id]["base_url"] = payload.base_url
        await asyncio.to_thread(write_settings, data)

    validation_cache.clear()
    return {"status": "success"}


@providers_router.delete("/{provider_id}/key")
async def delete_provider_key(provider_id: str):
    if provider_id not in PROVIDER_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_id}")

    env_key_name = f"{provider_id}_api_key"
    if getattr(settings, env_key_name, None):
        raise HTTPException(
            status_code=400,
            detail=f"Provider {provider_id} is managed by .env and cannot be deleted via API.",
        )

    try:
        await asyncio.to_thread(keyring.delete_password, "pma_backend", provider_id)
    except Exception as e:
        logger.debug("Failed to delete key for %s from keyring: %s", provider_id, e)

    validation_cache.clear()
    return {"status": "success"}


@providers_router.put("/{provider_id}/default_model")
async def set_default_model(provider_id: str, payload: SetDefaultModelPayload):
    if provider_id not in PROVIDER_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_id}")

    data = await asyncio.to_thread(read_settings)
    data = migrate_settings_if_needed(data)
    data["llm"]["per_provider"][provider_id]["default_model"] = payload.model
    await asyncio.to_thread(write_settings, data)
    return {"status": "success"}


@providers_router.get("/current")
async def get_current_provider():
    data = await asyncio.to_thread(read_settings)
    data = migrate_settings_if_needed(data)
    llm = data.get("llm", {})
    provider_preference = llm.get("provider", "auto")

    fallback_chain = llm.get("fallback_chain") or get_configured_provider_ids()

    resolved_id = None
    source = "unset"

    async def is_active(pid: str) -> tuple[bool, str]:
        env_key_name = f"{pid}_api_key"
        if getattr(settings, env_key_name, None):
            return True, "env"
        if pid in ("ollama", "lm_studio"):
            return True, "default"
        try:
            key = await asyncio.to_thread(keyring.get_password, "pma_backend", pid)
            if key:
                return True, "keyring"
        except Exception:  # nosec B110
            pass
        return False, "unset"

    if provider_preference != "auto":
        active, src = await is_active(provider_preference)
        if active:
            resolved_id = provider_preference
            source = src

    if not resolved_id:
        for pid in fallback_chain:
            active, src = await is_active(pid)
            if active:
                resolved_id = pid
                source = src
                break

    if not resolved_id:
        resolved_id = "gemini"
        source = "unset"

    per_provider = llm.get("per_provider", {})
    provider_settings = per_provider.get(resolved_id, {})
    model = provider_settings.get("default_model")

    if not model:
        if resolved_id == "gemini":
            model = settings.gemini_model
        elif resolved_id == "ollama":
            model = settings.ollama_model
        elif resolved_id == "openai":
            model = "gpt-4o-mini"
        elif resolved_id == "anthropic":
            model = "claude-3-5-sonnet-20241022"

    return {"provider": resolved_id, "model": model, "source": source}
