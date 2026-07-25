from typing import cast

from app.providers.base import ModelInfo
from app.providers.openai_compat import OpenAICompatibleProvider
from app.providers.registry import spec_of


class OpenRouterProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None = None,
        default_model: str | None = None,
        timeout: float = 30.0,
    ):
        spec = spec_of("openrouter")
        super().__init__(
            spec,
            api_key=api_key,
            base_url=base_url,
            default_model=default_model or "google/gemini-2.5-flash",
            timeout=timeout,
        )

    async def list_models(self) -> list[ModelInfo]:
        client = self._get_client()
        url = f"{self.base_url}{self.spec.models_endpoint}"
        headers = self._get_headers()

        # OpenRouter suggests sending referrer and title headers
        headers["HTTP-Referer"] = "https://github.com/Binitpyro/Personal_Memory_Assistant"
        headers["X-Title"] = "Personal Memory Assistant"

        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        models = []
        for item in data.get("data", []):
            model_id = item.get("id")
            if model_id:
                pricing = item.get("pricing", {})
                prompt_price = pricing.get("prompt", 0)
                try:
                    pricing_hint = float(prompt_price) * 1_000_000
                except (ValueError, TypeError):
                    pricing_hint = 0.0

                models.append(
                    {
                        "id": model_id,
                        "context_length": item.get("context_length"),
                        "pricing_hint": pricing_hint,
                        "family": self._detect_family(model_id),
                    }
                )
        return cast(list[ModelInfo], models)
