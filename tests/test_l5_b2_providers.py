"""Audit-fix lane L5 batch 2 (2026-10-07).

A4-09  local health checks probe the saved base_url, not the .env one
A4-10  an outage verdict is not cached (provider started after a failed probe)
A4-12  an outage is not cached as a <claim> capability verdict
A10-04 Knowledge Portrait calls an LLMClient method that exists
"""

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.insights import portrait
from app.providers.cache import ValidationCache, validation_cache
from app.search.capability_detector import CapabilityDetector
from app.search.llm_client import LLMClient
from app.settings_store import SettingsStore

_SAVED = "http://127.0.0.1:11500"


def _save_ollama_url():
    SettingsStore.save({"llm": {"per_provider": {"ollama": {"base_url": _SAVED}}}})


# --- A4-09 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_probes_the_saved_base_url():
    _save_ollama_url()
    validation_cache.clear()
    seen: list[str] = []

    async def fake_get(self, url, *a, **kw):
        seen.append(str(url))
        raise OSError("down")

    c = LLMClient()
    with patch("httpx.AsyncClient.get", fake_get):
        assert await c._check_ollama_health() is False
    assert seen and all(u.startswith(_SAVED) for u in seen), seen


def test_configured_provider_ids_probes_the_saved_base_url():
    from app.providers import manifest

    _save_ollama_url()
    probed: list[str] = []

    def fake_reachable(url, *a, **kw):
        probed.append(url)
        return True

    with (
        patch.object(manifest, "is_local_endpoint_reachable", fake_reachable),
        patch.object(manifest.keyring, "get_password", return_value=None),
    ):
        ids = manifest.get_configured_provider_ids()
    assert "ollama" in ids
    assert _SAVED in probed


# --- A4-10 ------------------------------------------------------------------


def test_outage_result_is_not_cached_but_auth_failure_is():
    cache = ValidationCache(ttl_seconds=60)
    cache._persistent_heap = {}
    outage = {"ok": False, "error_code": "network", "models": []}
    cache.set("ollama", "http://x", None, outage)
    assert cache.get("ollama", "http://x", None) is None

    auth = {"ok": False, "error_code": "auth_failed", "models": []}
    cache.set("openai", "http://y", "k", auth)
    assert cache.get("openai", "http://y", "k") == auth


def test_expired_verdict_is_kept_for_allow_stale():
    cache = ValidationCache(ttl_seconds=60)
    cache._persistent_heap = {}
    verdict = {"ok": True, "models": []}
    cache._cache[("ollama", "http://x", "")] = (time.time() - 120, verdict)
    assert cache.get("ollama", "http://x", None) is None
    # The plain read above must not have evicted it.
    assert cache.get("ollama", "http://x", None, allow_stale=True) == verdict


@pytest.mark.asyncio
async def test_ollama_validate_sees_the_provider_come_up():
    from app.providers import create_provider

    validation_cache.clear()
    p = create_provider("ollama", base_url="http://127.0.0.1:1")
    try:
        with patch("httpx.AsyncClient.get", AsyncMock(side_effect=OSError("refused"))):
            assert (await p.validate())["ok"] is False
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = {"models": []}
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=resp)):
            assert (await p.validate())["ok"] is True
    finally:
        await p.close()
        validation_cache.clear()


# --- A4-12 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capability_probe_does_not_cache_an_outage():
    d = CapabilityDetector()
    d.reset_cache()
    c = MagicMock()
    c.get_model_class.return_value = "7b_local"
    c.provider_preference = "ollama"
    c.model = c.ollama_model = c.lm_studio_model = "m"

    c.generate_raw = AsyncMock(return_value="LLM unavailable: Ollama is not running.")
    assert await d.detect_capabilities(c) is False

    c.generate_raw = AsyncMock(return_value='<claim sources="[1]">x</claim>')
    assert await d.detect_capabilities(c) is True
    d.reset_cache()


def _client_resolving_to(model: str):
    prov = MagicMock()
    prov.spec.id = "openai_compatible"
    prov.default_model = model
    prov.close = AsyncMock()
    c = MagicMock()
    c.get_model_class.return_value = "7b_local"
    c._resolve = AsyncMock(return_value=prov)
    return c


@pytest.mark.asyncio
async def test_capability_verdict_is_per_dispatched_model():
    # A4-12: the key ignored per_provider.default_model, so switching models within
    # one provider reused the first model's verdict.
    d = CapabilityDetector()
    d.reset_cache()
    a = _client_resolving_to("model-a")
    a.generate_raw = AsyncMock(return_value="no tags here")
    assert await d.detect_capabilities(a) is False
    b = _client_resolving_to("model-b")
    b.generate_raw = AsyncMock(return_value='<claim sources="[1]">x</claim>')
    assert await d.detect_capabilities(b) is True
    b.generate_raw.assert_awaited_once()
    d.reset_cache()


@pytest.mark.asyncio
async def test_capability_probe_bypasses_the_rag_template():
    # A4-12: generate_answer wraps the probe in the RAG prompt with an empty
    # context, inviting a refusal that disabled <claim> tags.
    d = CapabilityDetector()
    d.reset_cache()
    c = _client_resolving_to("m")
    c.generate_raw = AsyncMock(return_value='<claim sources="[1]">x</claim>')
    c.generate_answer = AsyncMock(return_value="not enough information")
    assert await d.detect_capabilities(c) is True
    c.generate_answer.assert_not_awaited()
    d.reset_cache()


# --- A10-04 -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_portrait_returns_themes_from_a_stub_provider(monkeypatch):
    reply = json.dumps({"themes": [{"name": "Python", "description": "code", "weight": 8}]})
    stub = SimpleNamespace(
        spec=SimpleNamespace(id="ollama"),
        chat=AsyncMock(return_value=reply),
        close=AsyncMock(),
    )
    c = LLMClient()
    c._token_loaded = True
    monkeypatch.setattr(c, "_resolve", AsyncMock(return_value=stub))
    monkeypatch.setattr(c, "_resolve_provider_by_id", AsyncMock(return_value=stub))
    db = SimpleNamespace(get_folder_profiles_text=AsyncMock(return_value="## docs"))
    monkeypatch.setattr(portrait, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(portrait, "get_llm", lambda: c)

    out = await portrait.generate_portrait()
    assert [t["name"] for t in out["themes"]] == ["Python"]
