import pytest
import httpx
from unittest.mock import AsyncMock, patch
from app.providers.openrouter import OpenRouterProvider


@pytest.mark.asyncio
async def test_openrouter_provider_pricing():
    provider = OpenRouterProvider(
        api_key="sk-or-v1-testkey123",
        base_url="https://openrouter.ai/api/v1"
    )

    mock_response = httpx.Response(
        200,
        json={
            "data": [
                {
                    "id": "meta-llama/llama-3-8b-instruct",
                    "pricing": {
                        "prompt": "0.0000001",  # $0.10 per million tokens
                        "completion": "0.0000002"
                    }
                }
            ]
        },
        request=httpx.Request("GET", "https://openrouter.ai/api/v1/models")
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        models = await provider.list_models()

        assert len(models) == 1
        assert models[0]["id"] == "meta-llama/llama-3-8b-instruct"
        assert pytest.approx(models[0]["pricing_hint"]) == 0.1

    await provider.close()
