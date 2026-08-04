import os
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# Retrieve token set in conftest
token = os.environ.get("X_LOCAL_ACCESS_TOKEN", "test-token")
headers = {"X-Local-Access-Token": token}


@patch("app.api.providers.read_settings")
@patch("app.api.providers.write_settings")
@patch("keyring.get_password")
def test_get_providers(mock_get_pw, mock_write, mock_read):
    mock_read.return_value = {
        "llm": {
            "provider": "openai",
            "per_provider": {
                "openai": {"base_url": "https://api.openai.com/v1", "default_model": "gpt-4o"}
            },
        }
    }
    mock_get_pw.return_value = "fake_key"

    resp = client.get("/api/providers", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    openai_spec = next(p for p in data if p["spec"]["id"] == "openai")
    assert openai_spec["is_set"] is True
    assert openai_spec["stored_in"] == "keyring"


@patch("app.api.providers.read_settings")
@patch("app.api.providers.write_settings")
@patch("keyring.set_password")
def test_set_provider_key(mock_set_pw, mock_write, mock_read):
    mock_read.return_value = {}

    resp = client.put("/api/providers/openai/key", json={"api_key": "new_key"}, headers=headers)
    assert resp.status_code == 200
    mock_set_pw.assert_called_with("pma_backend", "openai", "new_key")


@patch("app.api.providers.read_settings")
@patch("app.api.providers.write_settings")
@patch("keyring.delete_password")
def test_delete_provider_key(mock_del_pw, mock_write, mock_read):
    mock_read.return_value = {}

    resp = client.delete("/api/providers/openai/key", headers=headers)
    assert resp.status_code == 200
    mock_del_pw.assert_called_with("pma_backend", "openai")


@patch("app.api.providers.read_settings")
@patch("app.api.providers.write_settings")
def test_set_default_model(mock_write, mock_read):
    mock_read.return_value = {"llm": {"per_provider": {}}}

    resp = client.put(
        "/api/providers/openai/default_model", json={"model": "gpt-4o"}, headers=headers
    )
    assert resp.status_code == 200
    mock_write.assert_called()


@patch("app.api.providers.write_settings")
def test_reading_provider_settings_does_not_write_fallback_chain(mock_write, tmp_path, monkeypatch):
    import json
    from app.settings_store import CURRENT_SCHEMA_VERSION

    test_path = tmp_path / "settings.json"
    test_path.write_text(json.dumps({"schema_version": CURRENT_SCHEMA_VERSION}), encoding="utf-8")
    monkeypatch.setattr("app.settings_store.SETTINGS_PATH", test_path)

    resp = client.get("/api/providers/settings", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "fallback_chain" in data
    # Assert GET read endpoint never wrote to disk
    mock_write.assert_not_called()


# ── Cloud privacy consent gate ───────────────────────────────────────────────


@patch("app.api.providers.read_settings")
def test_get_settings_reports_consent_and_notice(mock_read):
    mock_read.return_value = {"llm": {"per_provider": {}, "cloud_privacy_consent": True}}

    resp = client.get("/api/providers/settings", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["cloud_privacy_consent"] is True
    assert data.get("cloud_privacy_notice")


@patch("app.api.providers.read_settings")
@patch("app.api.providers.write_settings")
def test_put_settings_rejects_cloud_provider_without_consent(mock_write, mock_read):
    mock_read.return_value = {"llm": {"per_provider": {}}}

    resp = client.put(
        "/api/providers/settings", json={"provider": "gemini"}, headers=headers
    )
    assert resp.status_code == 400
    mock_write.assert_not_called()


@patch("app.api.providers.read_settings")
@patch("app.api.providers.write_settings")
def test_put_settings_allows_cloud_provider_when_consent_already_stored(mock_write, mock_read):
    mock_read.return_value = {"llm": {"per_provider": {}, "cloud_privacy_consent": True}}

    resp = client.put(
        "/api/providers/settings", json={"provider": "gemini"}, headers=headers
    )
    assert resp.status_code == 200
    mock_write.assert_called()


@patch("app.api.providers.read_settings")
@patch("app.api.providers.write_settings")
def test_put_settings_allows_cloud_provider_when_consent_given_in_same_request(
    mock_write, mock_read
):
    mock_read.return_value = {"llm": {"per_provider": {}}}

    resp = client.put(
        "/api/providers/settings",
        json={"provider": "gemini", "cloud_privacy_consent": True},
        headers=headers,
    )
    assert resp.status_code == 200
    mock_write.assert_called()


@patch("app.api.providers.read_settings")
@patch("app.api.providers.write_settings")
def test_put_settings_does_not_gate_local_providers(mock_write, mock_read):
    mock_read.return_value = {"llm": {"per_provider": {}}}

    resp = client.put("/api/providers/settings", json={"provider": "ollama"}, headers=headers)
    assert resp.status_code == 200
    mock_write.assert_called()


@patch("app.api.providers.read_settings")
@patch("app.api.providers.write_settings")
def test_put_settings_does_not_gate_openai_compatible(mock_write, mock_read):
    """openai_compatible is a user-supplied endpoint (often self-hosted), excluded from the gate."""
    mock_read.return_value = {"llm": {"per_provider": {}}}

    resp = client.put(
        "/api/providers/settings", json={"provider": "openai_compatible"}, headers=headers
    )
    assert resp.status_code == 200
    mock_write.assert_called()


@patch("app.api.providers.read_settings")
@patch("app.api.providers.write_settings")
def test_put_settings_can_set_consent_alone(mock_write, mock_read):
    mock_read.return_value = {"llm": {"per_provider": {}}}

    resp = client.put(
        "/api/providers/settings", json={"cloud_privacy_consent": True}, headers=headers
    )
    assert resp.status_code == 200
    saved = mock_write.call_args[0][0]
    assert saved["llm"]["cloud_privacy_consent"] is True


# ── Local provider launch ──────────────────────────────────────────────────────


@patch("app.api.providers.read_settings")
def test_launch_status_shape(mock_read):
    mock_read.return_value = {"llm": {"per_provider": {}}}

    resp = client.get("/api/providers/ollama/launch_status", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider_id"] == "ollama"
    assert data["supported"] is True
    assert data["install_url"] == "https://ollama.com/download"
    assert isinstance(data["installed"], bool)
    assert isinstance(data["running"], bool)


@patch("app.api.providers.read_settings")
def test_launch_status_rejects_unknown_provider(mock_read):
    mock_read.return_value = {}

    resp = client.get("/api/providers/not_a_provider/launch_status", headers=headers)
    assert resp.status_code == 400


@patch("app.api.providers.read_settings")
def test_launching_a_cloud_provider_is_refused(mock_read):
    """Only the two local providers are launchable; nothing else may reach a spawn."""
    mock_read.return_value = {"llm": {"per_provider": {}}}

    resp = client.post("/api/providers/openai/launch", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["error_code"] == "not_supported"


@patch("app.api.providers.launch_local_provider", new_callable=AsyncMock)
@patch("app.api.providers.read_settings")
def test_launch_uses_saved_base_url(mock_read, mock_launch):
    """A custom base URL saved in settings must be what we probe after starting."""
    mock_read.return_value = {
        "llm": {"per_provider": {"ollama": {"base_url": "http://127.0.0.1:9999"}}}
    }
    mock_launch.return_value = {
        "ok": True,
        "running": True,
        "already_running": False,
        "message": "Ollama is running.",
        "error_code": None,
        "elapsed_ms": 12,
    }

    resp = client.post("/api/providers/ollama/launch", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_launch.assert_awaited_once_with("ollama", "http://127.0.0.1:9999")

