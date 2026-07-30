from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.providers.anthropic import AnthropicProvider


@pytest.mark.asyncio
async def test_anthropic_provider_spec_default_url():
    # Instantiate without base_url to test registry spec default
    provider = AnthropicProvider(api_key="sk-ant-testkey123")

    mock_response = httpx.Response(
        200,
        json={"data": [{"id": "claude-3-5-sonnet-20241022"}]},
        request=httpx.Request("GET", "https://api.anthropic.com/v1/models"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        models = await provider.list_models()

        assert len(models) == 1
        assert models[0]["id"] == "claude-3-5-sonnet-20241022"

        # Assert URL does NOT contain doubled /v1/v1/models
        url = mock_get.call_args[0][0]
        assert url == "https://api.anthropic.com/v1/models"

        _, kwargs = mock_get.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("anthropic-version") == "2023-06-01"
        assert headers.get("x-api-key") == "sk-ant-testkey123"

    await provider.close()
