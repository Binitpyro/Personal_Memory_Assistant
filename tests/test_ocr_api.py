"""OCR HTTP surface. Every endpoint must be reachable with OCR switched off."""

import pytest

from app.config import settings
from app.ocr import queue as ocr_queue

# Note: /ocr/enable persists the toggle through SettingsStore. The autouse
# `isolate_settings_file` fixture in conftest.py keeps that away from the
# developer's real data/settings.json, which also holds LLM provider config.


@pytest.fixture(autouse=True)
def ocr_off(monkeypatch):
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr(settings, "ocr_tier", "none")


async def test_status_is_available_with_ocr_disabled(client):
    res = await client.get("/api/ocr/status")
    assert res.status_code == 200

    body = res.json()
    assert body["enabled"] is False
    assert body["installed"] is False
    assert "queue" in body and "pages_pending" in body


async def test_queue_is_empty_by_default(client):
    res = await client.get("/api/ocr/queue")
    assert res.status_code == 200
    assert res.json()["items"] == []


async def test_queue_reports_enqueued_work(client, mock_db):
    await ocr_queue.enqueue_document(mock_db, r"C:\docs\a.pdf", [0, 1], 5)

    body = (await client.get("/api/ocr/queue")).json()

    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["file_name"] == "a.pdf"
    assert item["pages_pending"] == 5
    assert body["counts"]["pages_pending"] == 5


async def test_queue_status_filter(client, mock_db):
    await ocr_queue.enqueue_document(mock_db, r"C:\docs\a.pdf", [0], 1)
    await ocr_queue.enqueue_document(mock_db, r"C:\docs\b.pdf", [0], 1)
    await ocr_queue.mark_failed(mock_db, r"C:\docs\a.pdf", "nope", terminal=True)

    body = (await client.get("/api/ocr/queue?status=failed")).json()
    assert [i["file_name"] for i in body["items"]] == ["a.pdf"]


async def test_install_status_is_readable(client):
    res = await client.get("/api/ocr/install/status")
    assert res.status_code == 200
    assert res.json()["status"] in ("idle", "running", "ok", "failed", "cancelled")


async def test_enable_refuses_when_no_tier_is_installed(client):
    """Otherwise normalize_ocr silently flips it back and the UI looks broken."""
    res = await client.post("/api/ocr/enable", json={"enabled": True})

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["error_code"] == "TIER_NOT_INSTALLED"
    assert settings.ocr_enabled is False


async def test_disable_always_works(client):
    res = await client.post("/api/ocr/enable", json={"enabled": False})
    assert res.json() == {"ok": True, "enabled": False}


async def test_retry_on_an_unknown_file_is_rejected(client):
    res = await client.post("/api/ocr/retry", json={"file_path": r"C:\nope.pdf"})
    assert res.json()["error_code"] == "NOT_QUEUED"


async def test_retry_rearms_a_failed_row(client, mock_db):
    path = r"C:\docs\a.pdf"
    await ocr_queue.enqueue_document(mock_db, path, [0], 1)
    await ocr_queue.mark_failed(mock_db, path, "boom", terminal=True)

    assert (await client.post("/api/ocr/retry", json={"file_path": path})).json()["ok"] is True
    assert (await ocr_queue.get_row(mock_db, path)).status.value == "pending"


async def test_force_ocr_on_an_unindexed_file_is_rejected(client):
    res = await client.post("/api/ocr/force", json={"file_path": r"C:\nope.pdf"})
    assert res.json()["error_code"] == "NOT_INDEXED"


async def test_clear_queue(client, mock_db):
    await ocr_queue.enqueue_document(mock_db, r"C:\docs\a.pdf", [0], 1)
    assert (await client.post("/api/ocr/queue/clear")).json()["removed"] == 1
    assert await ocr_queue.get_row(mock_db, r"C:\docs\a.pdf") is None


async def test_clear_cache(client):
    res = await client.delete("/api/ocr/cache")
    assert res.status_code == 200
    assert res.json()["removed"] == 0


async def test_file_path_length_is_bounded(client):
    """Guards against an oversized body reaching the queue table."""
    res = await client.post("/api/ocr/retry", json={"file_path": "x" * 5000})
    assert res.status_code == 422


async def test_tiers_endpoint_reports_installed_and_active_states(client, monkeypatch):
    import app.ocr.settings as ocr_settings

    monkeypatch.setattr(ocr_settings, "is_tier_installed", lambda tier: tier == "cpu")
    monkeypatch.setattr(settings, "ocr_tier", "cpu")
    monkeypatch.setattr(settings, "ocr_enabled", True)

    res = await client.get("/api/ocr/tiers")
    assert res.status_code == 200
    data = res.json()
    assert data["installed"] == "cpu"
    tiers_map = {t["id"]: t for t in data["tiers"]}
    assert tiers_map["cpu"]["installed"] is True
    assert tiers_map["cpu"]["active"] is True
    assert tiers_map["gpu"]["installed"] is False
    assert tiers_map["gpu"]["active"] is False


async def test_select_tier_switches_installed_tier(client, monkeypatch):
    import app.ocr.settings as ocr_settings

    monkeypatch.setattr(ocr_settings, "is_tier_installed", lambda tier: tier in ("cpu", "gpu"))

    res = await client.post("/api/ocr/select", json={"tier": "gpu"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["tier"] == "gpu"
    assert settings.ocr_tier == "gpu"
    assert settings.ocr_enabled is True


async def test_select_tier_fails_for_uninstalled_tier(client, monkeypatch):
    import app.ocr.settings as ocr_settings

    monkeypatch.setattr(ocr_settings, "is_tier_installed", lambda tier: False)

    res = await client.post("/api/ocr/select", json={"tier": "gpu"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert data["error_code"] == "TIER_NOT_INSTALLED"
