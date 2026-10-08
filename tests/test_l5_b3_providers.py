"""Audit-fix lane L5 batch 3 (2026-10-08): provider defaults, timeouts, token refresh.

A4-14  one shared default Anthropic model, no longer the retired claude-3-5 id
A4-11  local providers' generation reads use query_stream_timeout_s; an empty
       exception message no longer reads "Last error: "
A4-15  an expiring Gemini OAuth token is reloaded, not cached for the process
A4-16  background validations close the provider they built
A4-18  SSE 'data:' without the optional space is parsed
A9-11  provider clients share one SSL context
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api import providers as providers_api
from app.config import settings
from app.providers import ANTHROPIC_DEFAULT_MODEL, create_provider
from app.providers.anthropic import AnthropicProvider
from app.providers.openai_compat import OpenAICompatibleProvider
from app.providers.registry import spec_of
from app.search.llm_client import LLMClient, _chain_failure_message
from app.settings_store import SettingsStore

# --- A4-14 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_default_model_is_the_shared_constant():
    assert ANTHROPIC_DEFAULT_MODEL == "claude-sonnet-5-5"
    assert AnthropicProvider(api_key="k").default_model == ANTHROPIC_DEFAULT_MODEL

    SettingsStore.save({"llm": {"cloud_privacy_consent": True}})
    c = LLMClient()
    c._token_loaded = True
    c.provider_keys["anthropic"] = "k"
    prov = await c._resolve_provider_by_id("anthropic")
    await prov.close()
    assert prov.default_model == ANTHROPIC_DEFAULT_MODEL  # type: ignore[attr-defined]


# --- A4-11 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_provider_read_timeout_is_the_stream_deadline_cloud_is_not(monkeypatch):
    monkeypatch.setattr(settings, "query_stream_timeout_s", 77)
    SettingsStore.save({"llm": {"cloud_privacy_consent": True}})
    c = LLMClient()
    c._token_loaded = True
    c._check_ollama_health = AsyncMock(return_value=True)  # type: ignore[method-assign]
    c.provider_keys["groq"] = "k"

    for pid in ("ollama", "groq"):
        prov = await c._resolve_provider_by_id(pid, timeout=30.0)
        await prov.close()
        if pid == "ollama":
            t = prov.timeout  # type: ignore[attr-defined]
            assert isinstance(t, httpx.Timeout)
            assert t.read == 77.0
            assert t.connect == 10.0
        else:
            assert prov.timeout == 30.0  # type: ignore[attr-defined]


def test_chain_message_names_the_exception_when_its_text_is_empty():
    tried = [("ollama", None, 30.0), ("groq", None, 10.0)]
    assert str(httpx.ReadTimeout("")) == ""
    assert "ReadTimeout" in _chain_failure_message(tried, httpx.ReadTimeout(""))
    assert "ReadTimeout" in _chain_failure_message(tried[:1], httpx.ReadTimeout(""))
    assert "boom" in _chain_failure_message(tried, RuntimeError("boom"))


# --- A4-15 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expiring_gemini_oauth_token_is_reloaded(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", None, raising=False)
    monkeypatch.setattr("app.search.llm_client.keyring.get_password", lambda *a: None)
    SettingsStore.save({"llm": {"cloud_privacy_consent": True}})
    c = LLMClient()
    c._token_loaded = True
    c.api_key = None  # type: ignore[assignment]
    c._oauth_token = "ya29.old"  # noqa: S105
    c._oauth_expiry = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)

    def fake_load():
        c._oauth_expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
        return "ya29.new"

    monkeypatch.setattr(c, "_load_oauth_token", fake_load)
    prov = await c._resolve_provider_by_id("gemini")
    await prov.close()
    assert prov.api_key == "ya29.new"  # type: ignore[attr-defined]

    # A token with time left is not reloaded.
    c._oauth_token = "ya29.fresh"  # noqa: S105
    prov = await c._resolve_provider_by_id("gemini")
    await prov.close()
    assert prov.api_key == "ya29.fresh"  # type: ignore[attr-defined]


# --- A4-16 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_background_validation_closes_the_provider_it_built():
    built = []

    def fake_create(pid, **kw):
        p = MagicMock()
        p.validate = AsyncMock(return_value={})
        p.close = AsyncMock()
        built.append(p)
        return p

    with (
        patch.object(providers_api, "create_provider", fake_create),
        patch.object(providers_api.validation_cache, "get", return_value=None),
    ):
        await providers_api.list_providers()
        await asyncio.gather(*providers_api._background_tasks)

    assert built, "a local provider should have been validated in the background"
    for p in built:
        p.validate.assert_awaited_once()
        p.close.assert_awaited_once()


# --- A4-18 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_data_line_without_a_space_is_parsed():
    body = (
        'data:{"choices":[{"delta":{"content":"hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        "data:[DONE]\n\n"
    )
    prov = OpenAICompatibleProvider(
        spec_of("openai_compatible"), api_key="k", base_url="http://x/v1", default_model="m"
    )
    prov._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=body))
    )
    out = [c async for c in prov.stream([{"role": "user", "content": "hi"}])]
    await prov.close()
    assert "".join(out) == "hello"


@pytest.mark.asyncio
async def test_anthropic_sse_data_line_without_a_space_is_parsed():
    body = 'data:{"type":"content_block_delta","delta":{"text":"hey"}}\n\n'
    prov = AnthropicProvider(api_key="k")
    prov._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=body))
    )
    out = [c async for c in prov.stream([{"role": "user", "content": "hi"}])]
    await prov.close()
    assert out == ["hey"]


# --- A9-11 ------------------------------------------------------------------


def test_providers_share_one_ssl_context():
    ctxs = []
    real = httpx.create_ssl_context

    def counting(*a, **kw):
        ctxs.append(1)
        return real(*a, **kw)

    from app.providers import base

    base._ssl_context.cache_clear()
    with patch("app.providers.base.httpx.create_ssl_context", counting):
        clients = [create_provider(pid)._get_client() for pid in ("ollama", "groq", "gemini")]  # type: ignore[attr-defined]
        clients += [create_provider("lm_studio")._get_client()]  # type: ignore[attr-defined]
    assert len(ctxs) == 1
    assert len(clients) == 4


@pytest.mark.asyncio
async def test_failed_oauth_refresh_keeps_the_token_so_the_next_call_retries(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", None, raising=False)
    monkeypatch.setattr("app.search.llm_client.keyring.get_password", lambda *a: None)
    SettingsStore.save({"llm": {"cloud_privacy_consent": True}})
    c = LLMClient()
    c._token_loaded = True
    c.api_key = None  # type: ignore[assignment]
    c._oauth_token = "ya29.old"  # noqa: S105
    c._oauth_expiry = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    calls = []

    def load():
        calls.append(1)
        if len(calls) == 1:
            return None  # offline: refresh failed
        c._oauth_expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
        return "ya29.new"

    monkeypatch.setattr(c, "_load_oauth_token", load)
    prov = await c._resolve_provider_by_id("gemini")
    await prov.close()
    assert c._oauth_token == "ya29.old"  # noqa: S105
    prov = await c._resolve_provider_by_id("gemini")
    await prov.close()
    assert len(calls) == 2
    assert prov.api_key == "ya29.new"  # type: ignore[attr-defined]
