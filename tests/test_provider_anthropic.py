from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.providers.anthropic import AnthropicProvider


@pytest.mark.asyncio
async def test_anthropic_provider_headers():
    provider = AnthropicProvider(
        api_key="sk-ant-testkey123", base_url="https://api.anthropic.com/v1"
    )

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

        _, kwargs = mock_get.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("anthropic-version") == "2023-06-01"
        assert headers.get("x-api-key") == "sk-ant-testkey123"

    await provider.close()
