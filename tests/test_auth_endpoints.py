import json
import os
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from app.config import settings

@pytest.fixture(autouse=True)
def clean_settings():
    old_key = settings.gemini_api_key
    settings.gemini_api_key = None
    yield
    settings.gemini_api_key = old_key

@pytest.fixture(autouse=True)
def patch_token_path(tmp_path):
    fake_token_path = tmp_path / "credentials.json"
    with patch("app.api.auth.TOKEN_PATH", fake_token_path):
        yield fake_token_path

@pytest.mark.asyncio
async def test_get_oauth_base():
    from app.api.auth import _get_oauth_base
    with patch.dict(os.environ, {"PORT": "12345"}):
        assert _get_oauth_base() == "http://localhost:12345"
    with patch.dict(os.environ, {}):
        if "PORT" in os.environ:
            del os.environ["PORT"]
        assert _get_oauth_base() == "http://localhost:8000"

@pytest.mark.asyncio
async def test_get_client_config(tmp_path):
    from app.api.auth import get_client_config
    
    # 1. Secret file exists
    fake_secret = tmp_path / "client_secret.json"
    fake_secret.write_text(json.dumps({"installed": {"client_id": "file_id"}}))
    with patch("app.api.auth.CLIENT_SECRETS_FILE", fake_secret):
        config = await get_client_config()
        assert config == {"installed": {"client_id": "file_id"}}

    # 2. Secret file missing, env vars exist
    with patch("app.api.auth.CLIENT_SECRETS_FILE", tmp_path / "missing.json"):
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "env_id", "GOOGLE_CLIENT_SECRET": "env_secret"}):
            config = await get_client_config()
            assert config["installed"]["client_id"] == "env_id"
            assert config["installed"]["client_secret"] == "env_secret"

        # 3. Secret file missing, env vars missing
        with patch.dict(os.environ, {}, clear=True):
            config = await get_client_config()
            assert config is None

@pytest.mark.asyncio
async def test_start_oauth_no_config(client):
    with patch("app.api.auth.get_client_config", return_value=None):
        response = await client.get("/api/auth/google/start")
        assert response.status_code == 200
        assert "Google Sign-In Unavailable" in response.text

@pytest.mark.asyncio
async def test_start_oauth_success(client):
    mock_flow_instance = MagicMock()
    mock_flow_instance.authorization_url.return_value = ("http://auth_url", "state_val")
    
    with patch("app.api.auth.get_client_config", return_value={"installed": {}}):
        with patch("app.api.auth.Flow.from_client_config", return_value=mock_flow_instance):
            response = await client.get("/api/auth/google/start")
            assert response.status_code == 307
            assert response.headers["location"] == "http://auth_url"

@pytest.mark.asyncio
async def test_callback_no_code(client):
    response = await client.get("/api/auth/google/callback")
    assert response.status_code == 200
    assert "Google Sign-In Failed" in response.text
    assert "The callback did not include an authorization code" in response.text

@pytest.mark.asyncio
async def test_callback_no_config(client):
    with patch("app.api.auth.get_client_config", return_value=None):
        response = await client.get("/api/auth/google/callback?code=123")
        assert response.status_code == 200
        assert "Google Sign-In Unavailable" in response.text

@pytest.mark.asyncio
async def test_callback_success(client, patch_token_path):
    mock_flow_instance = MagicMock()
    mock_creds = MagicMock()
    mock_creds.token = "token123"
    mock_creds.refresh_token = "refresh123"
    mock_creds.token_uri = "uri123"
    mock_creds.client_id = "cid123"
    mock_creds.client_secret = "csec123"
    mock_creds.scopes = ["scope1"]
    mock_flow_instance.credentials = mock_creds

    with patch("app.api.auth.get_client_config", return_value={"installed": {}}):
        with patch("app.api.auth.Flow.from_client_config", return_value=mock_flow_instance):
            response = await client.get("/api/auth/google/callback?code=123")
            assert response.status_code == 200
            assert "Google Account Connected" in response.text
            assert patch_token_path.exists()
            data = json.loads(patch_token_path.read_text())
            assert data["token"] == "token123"

@pytest.mark.asyncio
async def test_callback_exception(client):
    mock_flow_instance = MagicMock()
    mock_flow_instance.fetch_token.side_effect = Exception("Fetch error")

    with patch("app.api.auth.get_client_config", return_value={"installed": {}}):
        with patch("app.api.auth.Flow.from_client_config", return_value=mock_flow_instance):
            response = await client.get("/api/auth/google/callback?code=123")
            assert response.status_code == 200
            assert "Google Sign-In Failed" in response.text
            assert "Fetch error" in response.text

@pytest.mark.asyncio
async def test_status_env_key(client):
    # Setup the key explicitly for this test
    settings.gemini_api_key = "env_key_val"
    response = await client.get("/api/auth/google/status")
    assert response.status_code == 200
    assert response.json() == {"connected": True, "method": "env"}

@pytest.mark.asyncio
async def test_status_no_token_file(client):
    # Ensure token file doesn't exist
    response = await client.get("/api/auth/google/status")
    assert response.status_code == 200
    assert response.json() == {"connected": False}

@pytest.mark.asyncio
async def test_status_valid_token(client, patch_token_path):
    patch_token_path.write_text(json.dumps({"token": "t1"}))
    mock_creds_instance = MagicMock()
    mock_creds_instance.valid = True

    with patch("app.api.auth.Credentials.from_authorized_user_info", return_value=mock_creds_instance):
        response = await client.get("/api/auth/google/status")
        assert response.status_code == 200
        assert response.json() == {"connected": True, "method": "oauth"}

@pytest.mark.asyncio
async def test_status_expired_refreshed(client, patch_token_path):
    token_data = {"token": "t1", "refresh_token": "r1"}
    patch_token_path.write_text(json.dumps(token_data))
    
    mock_creds_instance = MagicMock()
    mock_creds_instance.valid = False
    mock_creds_instance.expired = True
    mock_creds_instance.refresh_token = "r1"
    mock_creds_instance.token = "t2"

    with patch("app.api.auth.Credentials.from_authorized_user_info", return_value=mock_creds_instance):
        with patch.object(mock_creds_instance, "refresh") as mock_refresh:
            response = await client.get("/api/auth/google/status")
            assert response.status_code == 200
            assert response.json() == {"connected": True, "method": "oauth"}
            mock_refresh.assert_called_once()
            
            # Verify saved refreshed token
            saved = json.loads(patch_token_path.read_text())
            assert saved["token"] == "t2"

@pytest.mark.asyncio
async def test_status_expired_no_refresh(client, patch_token_path):
    patch_token_path.write_text(json.dumps({"token": "t1"}))
    mock_creds_instance = MagicMock()
    mock_creds_instance.valid = False
    mock_creds_instance.expired = True
    mock_creds_instance.refresh_token = None

    with patch("app.api.auth.Credentials.from_authorized_user_info", return_value=mock_creds_instance):
        response = await client.get("/api/auth/google/status")
        assert response.status_code == 200
        assert response.json() == {"connected": False}

@pytest.mark.asyncio
async def test_status_exception(client, patch_token_path):
    patch_token_path.write_text(json.dumps({"token": "t1"}))
    with patch("app.api.auth.Credentials.from_authorized_user_info", side_effect=Exception("parse error")):
        response = await client.get("/api/auth/google/status")
        assert response.status_code == 200
        assert response.json() == {"connected": False}

@pytest.mark.asyncio
async def test_disconnect(client, patch_token_path):
    # Already disconnected
    response = await client.post("/api/auth/google/disconnect")
    assert response.json() == {"message": "Already disconnected."}

    # Connect then disconnect
    patch_token_path.touch()
    response = await client.post("/api/auth/google/disconnect")
    assert response.json() == {"message": "Disconnected successfully."}
    assert not patch_token_path.exists()

    # Deletion fails
    patch_token_path.touch()
    with patch("app.api.auth.os.remove", side_effect=OSError("Permission denied")):
        response = await client.post("/api/auth/google/disconnect")
        assert response.status_code == 500
        assert "Failed to disconnect" in response.json()["error"]

@pytest.mark.asyncio
async def test_keyring_endpoints(client):
    # 1. POST api key
    with patch("app.api.auth.keyring.set_password") as mock_set:
        response = await client.post("/api/auth/google/keys", json={"provider": "gemini", "api_key": "my_long_key_value"})
        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        mock_set.assert_called_once_with("pma_backend", "gemini", "my_long_key_value")

    # POST error
    with patch("app.api.auth.keyring.set_password", side_effect=Exception("Keyring error")):
        response = await client.post("/api/auth/google/keys", json={"provider": "gemini", "api_key": "my_key"})
        assert response.status_code == 500
        assert "Keyring error" in response.json()["error"]

    # 2. GET api key status - Long key
    with patch("app.api.auth.keyring.get_password", return_value="my_long_key_value"):
        response = await client.get("/api/auth/google/keys/gemini")
        assert response.status_code == 200
        assert response.json() == {"provider": "gemini", "preview": "my_l...alue", "is_set": True}

    # GET api key status - Short key
    with patch("app.api.auth.keyring.get_password", return_value="short"):
        response = await client.get("/api/auth/google/keys/gemini")
        assert response.status_code == 200
        assert response.json() == {"provider": "gemini", "preview": "****", "is_set": True}

    # GET api key status - Not set
    with patch("app.api.auth.keyring.get_password", return_value=None):
        response = await client.get("/api/auth/google/keys/gemini")
        assert response.status_code == 200
        assert response.json() == {"provider": "gemini", "is_set": False}

    # GET error
    with patch("app.api.auth.keyring.get_password", side_effect=Exception("Keyring read error")):
        response = await client.get("/api/auth/google/keys/gemini")
        assert response.status_code == 500
        assert "Keyring read error" in response.json()["error"]

    # 3. DELETE api key
    with patch("app.api.auth.keyring.delete_password") as mock_del:
        response = await client.delete("/api/auth/google/keys/gemini")
        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        mock_del.assert_called_once_with("pma_backend", "gemini")

    # DELETE error
    with patch("app.api.auth.keyring.delete_password", side_effect=Exception("Not found")):
        response = await client.delete("/api/auth/google/keys/gemini")
        assert response.status_code == 500
        assert "Not found" in response.json()["error"]
