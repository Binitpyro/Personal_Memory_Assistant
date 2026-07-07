import os
from unittest.mock import patch

from app.main import _init_local_access_token


def test_token_already_in_environment():
    """Verify that if X_LOCAL_ACCESS_TOKEN is already set in env, it is left unchanged."""
    with (
        patch.dict(os.environ, {"X_LOCAL_ACCESS_TOKEN": "pre-existing-token"}),
        patch("keyring.get_password") as mock_get,
    ):
        _init_local_access_token()

        assert os.environ["X_LOCAL_ACCESS_TOKEN"] == "pre-existing-token"  # noqa: S105
        mock_get.assert_not_called()


def test_token_retrieved_from_keyring():
    """Verify that if token is not in env, it is loaded from the keyring."""
    with (
        patch.dict(os.environ, {}),
        patch("keyring.get_password", return_value="token-from-keyring") as mock_get,
        patch("keyring.set_password") as mock_set,
    ):
        # Clear env var if set by other tests
        if "X_LOCAL_ACCESS_TOKEN" in os.environ:
            del os.environ["X_LOCAL_ACCESS_TOKEN"]

        _init_local_access_token()

        assert os.environ["X_LOCAL_ACCESS_TOKEN"] == "token-from-keyring"  # noqa: S105
        mock_get.assert_called_once_with("PersonalMemoryAssistant", "X_LOCAL_ACCESS_TOKEN")
        mock_set.assert_not_called()


def test_token_generated_and_stored_in_keyring():
    """Verify that if token is not in env or keyring, a new one is generated and stored."""
    with (
        patch.dict(os.environ, {}),
        patch("keyring.get_password", return_value=None) as mock_get,
        patch("keyring.set_password") as mock_set,
    ):
        # Clear env var if set by other tests
        if "X_LOCAL_ACCESS_TOKEN" in os.environ:
            del os.environ["X_LOCAL_ACCESS_TOKEN"]

        _init_local_access_token()

        token = os.environ["X_LOCAL_ACCESS_TOKEN"]
        assert token is not None
        assert len(token) > 20
        mock_get.assert_called_once_with("PersonalMemoryAssistant", "X_LOCAL_ACCESS_TOKEN")
        mock_set.assert_called_once_with("PersonalMemoryAssistant", "X_LOCAL_ACCESS_TOKEN", token)


def test_keyring_exception_fallback():
    """Verify that if keyring throws an error, a secure random fallback token is generated."""
    with (
        patch.dict(os.environ, {}),
        patch("keyring.get_password", side_effect=Exception("Keyring is broken")),
        patch("app.main.logger") as mock_logger,
    ):
        # Clear env var if set by other tests
        if "X_LOCAL_ACCESS_TOKEN" in os.environ:
            del os.environ["X_LOCAL_ACCESS_TOKEN"]

        _init_local_access_token()

        token = os.environ["X_LOCAL_ACCESS_TOKEN"]
        assert token is not None
        assert len(token) > 20
        mock_logger.warning.assert_called_once()
        assert "Failed to access OS keyring" in mock_logger.warning.call_args[0][0]
