import os
from unittest.mock import patch

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
