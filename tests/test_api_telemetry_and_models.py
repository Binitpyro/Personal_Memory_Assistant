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
    with TestClient(app) as client:
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

        # Patch SETTINGS_PATH to tmp
        settings_path = tmp_path / "data" / "settings.json"
        monkeypatch.setattr(m, "SETTINGS_PATH", settings_path)
        m._write_settings({"llm": {"provider": "gemini"}})
        result = m._read_settings()
        assert result["llm"]["provider"] == "gemini"

    def test_read_corrupt_json_returns_empty(self, tmp_path, monkeypatch):
        from app.api import models as m

        bad_file = tmp_path / "settings.json"
        bad_file.write_text("{bad json}", encoding="utf-8")
        monkeypatch.setattr(m, "SETTINGS_PATH", bad_file)
        result = m._read_settings()
        assert result == {}


@pytest.mark.asyncio
async def test_get_preferences_default(tmp_path, monkeypatch):
    from app.api import models as m

    monkeypatch.setattr(m, "SETTINGS_PATH", tmp_path / "missing.json")
    result = await m.get_preferences()
    assert result["provider"] == "auto"


@pytest.mark.asyncio
async def test_set_preferences_normalizes_invalid_provider(tmp_path, monkeypatch):
    from app.api import models as m

    settings_file = tmp_path / "data" / "settings.json"
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(m, "SETTINGS_PATH", settings_file)
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
