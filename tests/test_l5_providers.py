"""Audit-fix lane L5 (2026-10-07): providers and consent regressions.

A4-02  openai_compatible at a remote host is consent-gated (dispatch + PUT gate)
A4-03  a saved fallback chain is never replaced by the cloud-bearing default
A4-04  a provider switch reaches the running LLMClient
A4-05  a failure after content was emitted does not splice in another answer
A4-06  Anthropic / Gemini in-stream errors are no longer dropped
A4-13  /validate never sends the stored key to a caller-supplied different host
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers import create_provider
from app.providers.base import ProviderStreamError
from app.search.llm_client import (
    LLMClient,
    ProviderNotConfiguredError,
    _get_effective_fallback_chain,
    provider_leaves_device,
)
from app.settings_store import CURRENT_SCHEMA_VERSION, SettingsStore

client = TestClient(app)
headers = {"X-Local-Access-Token": os.environ.get("X_LOCAL_ACCESS_TOKEN", "test-token")}

_MSG = [{"role": "user", "content": "hi"}]
_REMOTE = "https://api.together.xyz/v1"
_LOCAL = "http://localhost:8080/v1"


# --- A4-02 ------------------------------------------------------------------


def test_custom_provider_leaves_device_only_when_remote():
    assert provider_leaves_device("openai_compatible", _REMOTE) is True
    assert provider_leaves_device("openai_compatible", _LOCAL) is False
    assert provider_leaves_device("openai_compatible", None) is False


def _save_llm(**llm):
    SettingsStore.save({"llm": llm})


@pytest.mark.asyncio
async def test_dispatch_refuses_remote_custom_without_consent_and_allows_loopback():
    c = LLMClient()
    c._token_loaded = True
    c.provider_keys["openai_compatible"] = "k"

    _save_llm(per_provider={"openai_compatible": {"base_url": _REMOTE}})
    with pytest.raises(ProviderNotConfiguredError) as exc:
        await c._resolve_provider_by_id("openai_compatible")
    assert exc.value.code == "cloud_consent_required"

    _save_llm(per_provider={"openai_compatible": {"base_url": _LOCAL}})
    prov = await c._resolve_provider_by_id("openai_compatible")
    await prov.close()


@patch("app.api.providers.write_settings")
@patch("app.api.providers.read_settings")
def test_put_settings_gates_a_remote_custom_provider(mock_read, mock_write):
    mock_read.return_value = {"llm": {"per_provider": {"openai_compatible": {"base_url": _REMOTE}}}}
    resp = client.put(
        "/api/providers/settings", json={"provider": "openai_compatible"}, headers=headers
    )
    assert resp.status_code == 400
    mock_write.assert_not_called()

    mock_read.return_value = {"llm": {"per_provider": {"openai_compatible": {"base_url": _LOCAL}}}}
    with patch("app.api.providers.get_llm", return_value=LLMClient()):
        resp = client.put(
            "/api/providers/settings", json={"provider": "openai_compatible"}, headers=headers
        )
    assert resp.status_code == 200


# --- A4-03 ------------------------------------------------------------------


def test_saved_local_chain_is_not_replaced_by_a_keyed_cloud_provider(monkeypatch):
    SettingsStore.save(
        {"schema_version": CURRENT_SCHEMA_VERSION, "llm": {"fallback_chain": ["ollama"]}}
    )
    # Ollama is momentarily unreachable; an old Groq key is still in the keyring.
    monkeypatch.setattr("app.search.llm_client.get_configured_provider_ids", lambda: ["groq"])

    assert _get_effective_fallback_chain() == ["ollama"]


# --- A4-04 ------------------------------------------------------------------


@patch("app.api.providers.write_settings")
@patch("app.api.providers.read_settings")
def test_provider_switch_reaches_the_running_client(mock_read, mock_write):
    mock_read.return_value = {"llm": {"per_provider": {}}}
    llm = LLMClient()
    assert llm.provider_preference == "auto"

    with patch("app.api.providers.get_llm", return_value=llm):
        resp = client.put("/api/providers/settings", json={"provider": "ollama"}, headers=headers)

    assert resp.status_code == 200
    assert llm.provider_preference == "ollama"


@pytest.mark.asyncio
async def test_rotated_key_is_the_one_dispatched():
    c = LLMClient()
    c._token_loaded = True
    c.provider_keys["openai"] = "new-key"
    _save_llm(cloud_privacy_consent=True)

    with patch("app.search.llm_client.create_provider") as create:
        await c._resolve_provider_by_id("openai")

    assert create.call_args.kwargs["api_key"] == "new-key"


# --- A4-05 ------------------------------------------------------------------


class _FakeProvider:
    def __init__(self, chunks, fail_after=False):
        self._chunks = chunks
        self._fail = fail_after

    async def stream(self, messages):
        for c in self._chunks:
            yield c
        if self._fail:
            raise httpx.ReadError("connection reset")

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_failure_after_content_does_not_splice_the_fallback_answer():
    c = LLMClient()
    c._token_loaded = True
    providers = {
        "groq": _FakeProvider(["The meeting is on Tues"], fail_after=True),
        "openai": _FakeProvider(["The meeting is on Thursday."]),
    }

    async def _resolve_by_id(pid, model=None, timeout=30.0):
        return providers[pid]

    async def _chain():
        return ["groq", "openai"]

    primary = MagicMock()
    primary.spec.id = "groq"
    primary.close = AsyncMock()
    with (
        patch.object(c, "_resolve_provider_by_id", side_effect=_resolve_by_id),
        patch.object(c, "_resolve", AsyncMock(return_value=primary)),
        patch("app.search.llm_client._get_effective_fallback_chain_async", _chain),
        patch(
            "app.search.llm_client.capability_detector.detect_capabilities",
            AsyncMock(return_value=False),
        ),
    ):
        out = [x async for x in c.stream_answer("q", "ctx")]

    text = "".join(x for x in out if not x.startswith('{"control"'))
    controls = [json.loads(x) for x in out if x.startswith('{"control"')]
    assert text == "The meeting is on Tues"
    assert [x["control"] for x in controls] == ["provider_error", "usage"]


# --- A4-06 ------------------------------------------------------------------


def _patched_stream(provider, body: str):
    def handler(request):
        return httpx.Response(200, text=body)

    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


_ANTHROPIC_ERR = (
    'data: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}\n\n'
)
_ANTHROPIC_DELTA = 'data: {"type":"content_block_delta","delta":{"text":"Your tax refund is "}}\n\n'


@pytest.mark.asyncio
async def test_anthropic_error_event_before_content_raises():
    p = _patched_stream(
        create_provider("anthropic", api_key="k", default_model="m"), _ANTHROPIC_ERR
    )
    with pytest.raises(ProviderStreamError, match="Overloaded"):
        [c async for c in p.stream(_MSG)]
    await p.close()


@pytest.mark.asyncio
async def test_anthropic_error_event_after_content_raises():
    body = _ANTHROPIC_DELTA + _ANTHROPIC_ERR + _ANTHROPIC_DELTA
    p = _patched_stream(create_provider("anthropic", api_key="k", default_model="m"), body)
    got: list[str] = []
    with pytest.raises(ProviderStreamError, match="Overloaded"):
        async for c in p.stream(_MSG):
            got.append(c)
    assert got == ["Your tax refund is "]
    await p.close()


_GEMINI_ERR = '[{"error":{"code":503,"message":"The model is overloaded"}}]'
_GEMINI_TEXT = '[{"candidates":[{"content":{"parts":[{"text":"Your tax refund is "}]}}]}'


@pytest.mark.asyncio
async def test_gemini_error_object_before_content_raises():
    p = _patched_stream(create_provider("gemini", api_key="k", default_model="m"), _GEMINI_ERR)
    with pytest.raises(ProviderStreamError, match="overloaded"):
        [c async for c in p.stream(_MSG)]
    await p.close()


@pytest.mark.asyncio
async def test_gemini_error_object_after_content_raises():
    body = _GEMINI_TEXT + ',{"error":{"message":"boom"}}]'
    p = _patched_stream(create_provider("gemini", api_key="k", default_model="m"), body)
    got: list[str] = []
    with pytest.raises(ProviderStreamError, match="boom"):
        async for c in p.stream(_MSG):
            got.append(c)
    assert got == ["Your tax refund is "]
    await p.close()


# --- A4-13 / A5-06 ----------------------------------------------------------

_OK = {
    "ok": True,
    "latency_ms": 1,
    "models": [],
    "error": None,
    "error_code": None,
    "server_time": None,
}


def _validate(pid, body, stored_url):
    with (
        patch("app.api.providers.create_provider") as create,
        patch("app.api.providers.read_settings") as read,
        patch("keyring.get_password", return_value="STORED-KEY"),
    ):
        read.return_value = {"llm": {"per_provider": {pid: {"base_url": stored_url}}}}
        create.return_value.validate = AsyncMock(return_value=_OK)
        create.return_value.close = AsyncMock()
        resp = client.post(f"/api/providers/{pid}/validate", json=body, headers=headers)
    assert resp.status_code == 200
    return create.call_args.kwargs


@pytest.mark.parametrize("pid", ["openai_compatible", "ollama", "lm_studio", "nvidia_nim"])
def test_validate_does_not_send_the_stored_key_to_another_host(pid):
    kwargs = _validate(pid, {"base_url": "http://attacker.example/v1"}, _LOCAL)
    assert kwargs["api_key"] is None
    assert kwargs["base_url"] == "http://attacker.example/v1"


def test_validate_still_uses_the_stored_key_for_the_stored_host():
    assert _validate("openai_compatible", {}, _LOCAL)["api_key"] == "STORED-KEY"
    assert _validate("openai_compatible", {"base_url": _LOCAL + "/"}, _LOCAL)["api_key"] == (
        "STORED-KEY"
    )


def test_validate_sends_a_key_the_caller_supplied_to_the_typed_host():
    kwargs = _validate("openai_compatible", {"base_url": _REMOTE, "api_key": "typed"}, _LOCAL)
    assert kwargs["api_key"] == "typed"
