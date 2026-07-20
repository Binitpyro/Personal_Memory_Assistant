import json
import logging
import time
from collections.abc import AsyncGenerator
import httpx
from app.providers.base import ModelInfo, ValidationResult
from app.providers.registry import spec_of
from app.providers.cache import validation_cache

logger = logging.getLogger(__name__)


class OllamaProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
        timeout: float = 30.0
    ):
        self.spec = spec_of("ollama")
        self.api_key = api_key
        self.base_url = base_url or self.spec.default_base_url
        self.default_model = default_model or "llama3"
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def list_models(self) -> list[ModelInfo]:
        client = self._get_client()
        url = f"{self.base_url}{self.spec.models_endpoint}"

        logger.debug("Listing Ollama models from %s", url)
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

        models = []
        for item in data.get("models", []):
            name = item.get("name")
            if name:
                models.append({
                    "id": name,
                    "context_length": 8192,
                    "pricing_hint": 0.0,
                    "family": "chat"
                })
        return models

    async def validate(self) -> ValidationResult:
        cached = validation_cache.get(self.spec.id, self.base_url, self.api_key)
        if cached:
            return cached

        client = self._get_client()
        url = f"{self.base_url}{self.spec.models_endpoint}"

        start_time = time.time()
        result: ValidationResult = {
            "ok": False,
            "latency_ms": 0,
            "models": [],
            "error": None,
            "error_code": None,
            "server_time": None
        }

        try:
            timeout = httpx.Timeout(1.5, read=3.0)
            resp = await client.get(url, timeout=timeout)
            latency = int((time.time() - start_time) * 1000)
            result["latency_ms"] = latency
            result["server_time"] = resp.headers.get("Date")

            if resp.status_code == 200:
                try:
                    models = await self.list_models()
                    result["models"] = models
                    result["ok"] = True
                except Exception as parse_err:
                    result["error_code"] = "wrong_base_url"
                    result["error"] = f"Failed to parse Ollama models: {str(parse_err)}"
            else:
                result["error_code"] = "provider_down"
                result["error"] = f"Ollama returned status {resp.status_code}."

        except httpx.ConnectTimeout:
            result["error_code"] = "network"
            result["error"] = f"Cannot reach Ollama at {self.base_url}."
        except Exception as e:
            result["error_code"] = "provider_down"
            result["error"] = f"Unexpected Ollama validation error: {str(e)}"

        logger.info(
            "Validation for ollama: ok=%s, error_code=%s, error=%s",
            result["ok"], result["error_code"], result["error"]
        )

        validation_cache.set(self.spec.id, self.base_url, self.api_key, result)
        return result

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096
    ) -> str:
        client = self._get_client()
        url = f"{self.base_url}/api/chat"
        model_name = model or self.default_model

        payload = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }

        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return str(data["message"]["content"])

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096
    ) -> AsyncGenerator[str, None]:
        client = self._get_client()
        url = f"{self.base_url}/api/chat"
        model_name = model or self.default_model

        payload = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }

        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    content = parsed.get("message", {}).get("content")
                    if content:
                        yield content
                    if parsed.get("done", False):
                        break
                except Exception as e:
                    logger.debug("Failed to parse Ollama stream chunk: %s", e)
                    continue
