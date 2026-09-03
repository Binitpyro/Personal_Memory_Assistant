from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.providers import create_provider


@pytest.mark.asyncio
async def test_openai_provider_validate_success():
    provider = create_provider(
        "openai",
        api_key="sk-testopenaiapikey123",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
    )

    mock_response = httpx.Response(
        200,
        json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]},
        headers={"Date": "Mon, 01 Jan 2026 00:00:00 GMT"},
        request=httpx.Request("GET", "https://api.openai.com/v1/models"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        res = await provider.validate()

        assert res["ok"] is True
        assert len(res["models"]) == 2
        assert res["models"][0]["id"] == "gpt-4o"
        assert res["server_time"] == "Mon, 01 Jan 2026 00:00:00 GMT"

    await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_validate_auth_failure():
    provider = create_provider(
        "openai", api_key="sk-invalid", base_url="https://api.openai.com/v1", default_model="gpt-4o"
    )

    mock_response = httpx.Response(
        401,
        text="Unauthorized",
        headers={"Date": "Mon, 01 Jan 2026 00:00:00 GMT"},
        request=httpx.Request("GET", "https://api.openai.com/v1/models"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        res = await provider.validate()

        assert res["ok"] is False
        assert res["error_code"] == "auth_failed"
        assert "Key is invalid" in res["error"]

    await provider.close()
