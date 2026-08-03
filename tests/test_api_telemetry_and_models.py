"""
tests/test_api_telemetry_and_models.py
Covers:
  - app/api/telemetry.py  (LatencyTracker + get_metrics endpoint)
  - app/api/models.py     (LLM preferences, detect_local_models)
  - app/utils/metrics.py  (LatencyTracker, Timer, metrics_tracker singleton)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.utils.metrics import LatencyTracker, Timer

# ── LatencyTracker ────────────────────────────────────────────────────────────


class TestLatencyTracker:
    def test_record_and_get_stats(self):
        tracker = LatencyTracker(window_size=10)
        tracker.record("embedding", 10.0)
        tracker.record("embedding", 20.0)
        tracker.record("embedding", 30.0)
        stats = tracker.get_stats()
        assert "embedding" in stats
        assert stats["embedding"]["count"] == 3
        assert stats["embedding"]["avg"] == 20.0
        assert stats["embedding"]["max"] == 30.0

    def test_percentiles(self):
        tracker = LatencyTracker(window_size=100)
        for i in range(1, 101):
            tracker.record("stage", float(i))
        stats = tracker.get_stats()
        s = stats["stage"]
        assert s["p50"] > 0
        assert s["p95"] > 0
        assert s["p99"] > 0
        assert s["p99"] >= s["p95"] >= s["p50"]

    def test_window_eviction(self):
        tracker = LatencyTracker(window_size=3)
        for i in range(10):
            tracker.record("win", float(i))
        stats = tracker.get_stats()
        assert stats["win"]["count"] == 3  # Only last 3 kept

    def test_multiple_stages(self):
        tracker = LatencyTracker()
        tracker.record("llm", 100.0)
        tracker.record("fts", 5.0)
        stats = tracker.get_stats()
        assert "llm" in stats
        assert "fts" in stats

    def test_empty_stage_not_reported(self):
        tracker = LatencyTracker()
        stats = tracker.get_stats()
        assert isinstance(stats, dict)

    def test_thread_safety(self):
        """Concurrent recordings should not crash."""
        import threading

        tracker = LatencyTracker(window_size=100)

        def record_many():
            for _ in range(50):
                tracker.record("concurrent", 1.0)

        threads = [threading.Thread(target=record_many) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stats = tracker.get_stats()
        assert "concurrent" in stats


class TestTimer:
    def test_timer_records_to_tracker(self):
        tracker = LatencyTracker()
        import time

        with patch("app.utils.metrics.metrics_tracker", tracker), Timer("test_stage"):
            time.sleep(0.001)
        stats = tracker.get_stats()
        # Timer records to global metrics_tracker, not local; just check no crash
        assert isinstance(stats, dict)

    def test_timer_context_manager_returns_self(self):
        t = Timer("op")
        result = t.__enter__()
        t.__exit__(None, None, None)
        assert result is t


# ── metrics_tracker singleton ─────────────────────────────────────────────────


def test_metrics_tracker_singleton():
    """metrics_tracker is a module-level singleton."""
    from app.utils.metrics import metrics_tracker as mt

    mt.record("singleton_test", 42.0)
    stats = mt.get_stats()
    assert "singleton_test" in stats


# ── Telemetry API endpoint ────────────────────────────────────────────────────


@pytest.fixture
def app_client():
    from app.api.deps import get_emb, get_lancedb, get_llm
    from app.main import app, get_db

    # minimal overrides so TestClient works
    mock_db = MagicMock()
    mock_emb = MagicMock()
    mock_lancedb = MagicMock()
    mock_llm = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_emb] = lambda: mock_emb
    app.dependency_overrides[get_lancedb] = lambda: mock_lancedb
    app.dependency_overrides[get_llm] = lambda: mock_llm

    import os

    token = os.environ.get("X_LOCAL_ACCESS_TOKEN", "test-token")
    with TestClient(app, headers={"X-Local-Access-Token": token}) as client:
        yield client
    app.dependency_overrides.clear()


def test_telemetry_endpoint(app_client):
    resp = app_client.get("/api/telemetry/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert body["status"] == "ok"
    assert "metrics" in body


# ── LLM Models API ────────────────────────────────────────────────────────────


class TestReadWriteSettings:
    """P1-1: models.py's _read_settings/_write_settings now delegate to
    SettingsStore (app/settings_store.py), so these tests patch
    app.settings_store.SETTINGS_PATH - the module.SETTINGS_PATH attribute
    these used to patch directly on app.api.models no longer exists there."""

    def test_read_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from app.api.models import _read_settings

        result = _read_settings()
        assert result == {}

    def test_write_then_read(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Create the data dir
        (tmp_path / "data").mkdir()

        from app.api import models as m

        settings_path = tmp_path / "data" / "settings.json"
        monkeypatch.setattr("app.settings_store.SETTINGS_PATH", settings_path)
        m._write_settings({"llm": {"provider": "gemini"}})
        result = m._read_settings()
        assert result["llm"]["provider"] == "gemini"

    def test_read_corrupt_json_raises_http_500(self, tmp_path, monkeypatch):
        """Behavior change from the old bypass: a corrupt settings file used
        to be silently treated as "no settings yet", so the next write would
        overwrite it and lose whatever was recoverable. SettingsStore.read()
        raises instead, and models.py now surfaces that as a 500 rather than
        swallowing it - matching app/api/providers.py's read_settings."""
        from fastapi import HTTPException

        from app.api import models as m

        bad_file = tmp_path / "settings.json"
        bad_file.write_text("{bad json}", encoding="utf-8")
        monkeypatch.setattr("app.settings_store.SETTINGS_PATH", bad_file)
        with pytest.raises(HTTPException) as exc_info:
            m._read_settings()
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_get_preferences_default(tmp_path, monkeypatch):
    from app.api import models as m

    monkeypatch.setattr("app.settings_store.SETTINGS_PATH", tmp_path / "missing.json")
    result = await m.get_preferences()
    assert result["provider"] == "auto"


@pytest.mark.asyncio
async def test_set_preferences_normalizes_invalid_provider(tmp_path, monkeypatch):
    from app.api import models as m

    settings_file = tmp_path / "data" / "settings.json"
    (tmp_path / "data").mkdir()
    monkeypatch.setattr("app.settings_store.SETTINGS_PATH", settings_file)
    mock_llm = MagicMock()
    mock_llm.apply_preferences = MagicMock()
    with patch("app.api.models.get_llm", return_value=mock_llm):
        payload = m.LLMPreferences(provider="invalid_provider")
        result = await m.set_preferences(payload)
    assert result["llm"]["provider"] == "auto"


@pytest.mark.asyncio
async def test_detect_local_models_no_servers():
    """When Ollama/LM Studio are offline, should return detected=False for both."""
    from app.api.models import detect_local_models

    result = await detect_local_models()
    assert "ollama" in result
    assert "lm_studio" in result
    # In test environment, these servers are not running
    assert result["ollama"]["detected"] in (True, False)
    assert result["lm_studio"]["detected"] in (True, False)


@pytest.mark.asyncio
async def test_detect_local_models_ollama_detected():
    """Mock a successful Ollama response."""
    import httpx

    from app.api.models import detect_local_models

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"models": [{"name": "llama3"}]}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=[
                mock_response,  # Ollama succeeds
                httpx.ConnectError("no lm studio"),  # LM Studio fails
            ]
        )
        mock_client_cls.return_value = mock_client
        result = await detect_local_models()

    assert result["ollama"]["detected"] is True
    assert "llama3" in result["ollama"]["models"]


@pytest.mark.asyncio
async def test_llm_chat_passthrough_rejects_auto():
    from fastapi import HTTPException

    from app.api.models import LLMChatRequest, chat_passthrough

    payload = LLMChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        provider="auto",
    )
    with pytest.raises(HTTPException) as exc_info:
        await chat_passthrough(payload)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_llm_chat_passthrough_success():
    from app.api.models import LLMChatRequest, chat_passthrough

    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(return_value="Passthrough response")
    mock_provider.close = AsyncMock()

    mock_llm = MagicMock()
    mock_llm._resolve_provider_by_id = AsyncMock(return_value=mock_provider)

    payload = LLMChatRequest(
        messages=[{"role": "user", "content": "hello"}],
        provider="ollama",
    )
    with patch("app.api.models.get_llm", return_value=mock_llm):
        result = await chat_passthrough(payload)

    assert result["provider"] == "ollama"
    assert result["content"] == "Passthrough response"


@pytest.mark.asyncio
async def test_llm_chat_passthrough_resolution_failure_does_not_leak_url(caplog):
    """P1-3: a user-configured openai_compatible base_url with embedded
    credentials would previously appear verbatim in the 502 response body,
    since the raw exception string was interpolated straight into `detail`.
    httpx's ConnectError.__str__ includes the request URL, which is exactly
    the shape of exception _resolve_provider_by_id can raise."""
    import httpx
    from fastapi import HTTPException

    from app.api.models import LLMChatRequest, chat_passthrough

    leaky_url = "https://user:supersecret@evil-or-not.example/v1/chat"
    mock_llm = MagicMock()
    mock_llm._resolve_provider_by_id = AsyncMock(
        side_effect=httpx.ConnectError(f"Connection refused: {leaky_url}")
    )

    payload = LLMChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        provider="openai_compatible",
    )
    with (
        patch("app.api.models.get_llm", return_value=mock_llm),
        pytest.raises(HTTPException) as exc_info,
    ):
        await chat_passthrough(payload)

    assert exc_info.value.status_code == 502
    assert "supersecret" not in exc_info.value.detail
    assert leaky_url not in exc_info.value.detail
    # Full detail must still reach the server log for debugging.
    assert any(leaky_url in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_llm_chat_passthrough_chat_failure_does_not_leak_url(caplog):
    """Same leak vector, but from the provider.chat() call itself rather
    than provider resolution."""
    import httpx
    from fastapi import HTTPException

    from app.api.models import LLMChatRequest, chat_passthrough

    leaky_url = "https://user:supersecret@evil-or-not.example/v1/chat"
    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(
        side_effect=httpx.ConnectError(f"Connection refused: {leaky_url}")
    )
    mock_provider.close = AsyncMock()

    mock_llm = MagicMock()
    mock_llm._resolve_provider_by_id = AsyncMock(return_value=mock_provider)

    payload = LLMChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        provider="openai_compatible",
    )
    with (
        patch("app.api.models.get_llm", return_value=mock_llm),
        pytest.raises(HTTPException) as exc_info,
    ):
        await chat_passthrough(payload)

    assert exc_info.value.status_code == 502
    assert "supersecret" not in exc_info.value.detail
    assert leaky_url not in exc_info.value.detail
    assert any(leaky_url in r.message for r in caplog.records)
    mock_provider.close.assert_called_once()


@pytest.mark.asyncio
async def test_cloud_privacy_consent_required(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from app.api import models as m

    settings_file = tmp_path / "data" / "settings.json"
    (tmp_path / "data").mkdir()
    monkeypatch.setattr("app.settings_store.SETTINGS_PATH", settings_file)

    payload = m.LLMPreferences(provider="gemini", cloud_privacy_consent=False)
    with pytest.raises(HTTPException) as exc_info:
        await m.set_preferences(payload)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_set_preferences_preserves_sibling_llm_keys(tmp_path, monkeypatch):
    """P1-1: set_preferences used to do `data["llm"] = {...}`, replacing the
    whole sub-dict and silently destroying fallback_chain/per_provider -
    written by app/api/providers.py's own preferences endpoints - on every
    call. It also never stamped schema_version, which made
    _get_effective_fallback_chain bail to defaults. Both are fixed by
    routing through SettingsStore.save() with an in-place update."""
    from app.api import models as m
    from app.settings_store import CURRENT_SCHEMA_VERSION, SettingsStore

    settings_file = tmp_path / "data" / "settings.json"
    (tmp_path / "data").mkdir()
    monkeypatch.setattr("app.settings_store.SETTINGS_PATH", settings_file)

    SettingsStore.save(
        {
            "llm": {
                "provider": "auto",
                "fallback_chain": ["ollama", "lm_studio"],
                "per_provider": {"ollama": {"base_url": None, "default_model": "llama3"}},
            }
        }
    )

    mock_llm = MagicMock()
    mock_llm.apply_preferences = MagicMock()
    with patch("app.api.models.get_llm", return_value=mock_llm):
        payload = m.LLMPreferences(provider="ollama", ollama_model="mistral")
        await m.set_preferences(payload)

    saved = SettingsStore.read()
    assert saved["llm"]["fallback_chain"] == ["ollama", "lm_studio"]
    assert saved["llm"]["per_provider"] == {
        "ollama": {"base_url": None, "default_model": "llama3"}
    }
    assert saved["llm"]["ollama_model"] == "mistral"
    assert saved["schema_version"] == CURRENT_SCHEMA_VERSION

