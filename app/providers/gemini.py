import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any, cast

import httpx

from app.providers.base import ModelInfo, ValidationResult
from app.providers.cache import validation_cache
from app.providers.registry import spec_of

logger = logging.getLogger(__name__)


class GeminiProvider:
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None = None,
        default_model: str | None = None,
        timeout: float = 30.0,
    ):
        self.spec = spec_of("gemini")
        self.api_key = api_key
        self.base_url = base_url or self.spec.default_base_url
        self.default_model = default_model or "gemini-2.5-flash-lite"
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _get_headers(self) -> dict[str, str]:
        headers = {}
        if self.api_key:
            if self.api_key.startswith("ya29."):
                headers["Authorization"] = f"Bearer {self.api_key}"
            else:
                headers["x-goog-api-key"] = self.api_key
        return headers

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def list_models(self) -> list[ModelInfo]:
        client = self._get_client()
        url = f"{self.base_url}{self.spec.models_endpoint}"
        headers = self._get_headers()

        logger.debug("Listing Gemini models from %s", url)
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        models = []
        for item in data.get("models", []):
            name = item.get("name", "")
            # Gemini returns names like "models/gemini-1.5-flash"
            model_id = name.split("/")[-1] if name.startswith("models/") else name
            if model_id:
                models.append(
                    {
                        "id": model_id,
                        "context_length": item.get("inputTokenLimit"),
                        "pricing_hint": None,
                        "family": "chat",
                    }
                )
        return cast(list[ModelInfo], models)

    async def validate(self) -> ValidationResult:
        cached = validation_cache.get(self.spec.id, self.base_url, self.api_key)
        if cached:
            return cast(ValidationResult, cached)

        client = self._get_client()
        url = f"{self.base_url}{self.spec.models_endpoint}"
        headers = self._get_headers()

        start_time = time.time()
        result: ValidationResult = {
            "ok": False,
            "latency_ms": 0,
            "models": [],
            "error": None,
            "error_code": None,
            "server_time": None,
        }

        try:
            timeout = httpx.Timeout(3.0, read=5.0)
            resp = await client.get(url, headers=headers, timeout=timeout)
            latency = int((time.time() - start_time) * 1000)
            result["latency_ms"] = latency
            result["server_time"] = resp.headers.get("Date")

            if resp.status_code == 200:
                try:
                    models = await self.list_models()
                    result["models"] = models
                    result["ok"] = True
                    if not models:
                        result["error_code"] = "empty"
                        result["error"] = "Auth worked but no models are visible."
                except Exception as parse_err:
                    result["error_code"] = "wrong_base_url"
                    result["error"] = f"Failed to parse models payload: {parse_err!s}"
            else:
                if resp.status_code in (400, 401, 403):
                    result["error_code"] = "auth_failed"
                    result["error"] = (
                        "Key is invalid or lacks permissions. Regenerate at Gemini API docs."
                    )
                elif resp.status_code == 404:
                    result["error_code"] = "wrong_base_url"
                    result["error"] = "Gemini endpoint not found."
                elif resp.status_code == 429:
                    result["error_code"] = "rate_limited"
                    result["error"] = "Rate-limited by Gemini."
                else:
                    result["error_code"] = "provider_down"
                    result["error"] = f"Gemini error {resp.status_code}: {resp.text[:100]}"

        except httpx.ConnectTimeout:
            fallback = validation_cache.get_offline_fallback(self.spec.id)
            if fallback:
                result = fallback
            else:
                result["error_code"] = "network"
                result["error"] = f"Cannot reach Gemini at {self.base_url}."
        except Exception as e:
            fallback = validation_cache.get_offline_fallback(self.spec.id)
            if fallback:
                result = fallback
            else:
                result["error_code"] = "provider_down"
                result["error"] = f"Unexpected Gemini validation error: {e!s}"

        logger.info(
            "Validation for gemini: ok=%s, error_code=%s, error=%s",
            result["ok"],
            result["error_code"],
            result["error"],
        )

        validation_cache.set(self.spec.id, self.base_url, self.api_key, result)
        return cast(ValidationResult, result)

    def _build_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        return {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.8,
                "maxOutputTokens": 4096,
            },
        }

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        client = self._get_client()
        model_name = model or self.default_model
        if model_name.startswith("models/"):
            model_name = model_name.split("/")[-1]

        url = f"{self.base_url}/v1/models/{model_name}:generateContent"
        headers = self._get_headers()
        payload = self._build_payload(messages)
        payload["generationConfig"]["temperature"] = temperature
        payload["generationConfig"]["maxOutputTokens"] = max_tokens

        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return str(data["candidates"][0]["content"]["parts"][0]["text"])

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        client = self._get_client()
        model_name = model or self.default_model
        if model_name.startswith("models/"):
            model_name = model_name.split("/")[-1]

        url = f"{self.base_url}/v1/models/{model_name}:streamGenerateContent"
        headers = self._get_headers()
        payload = self._build_payload(messages)
        payload["generationConfig"]["temperature"] = temperature
        payload["generationConfig"]["maxOutputTokens"] = max_tokens

        decoder = json.JSONDecoder()
        buffer = ""
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_text():
                buffer += chunk
                buffer, new_texts = self._parse_stream_buffer(decoder, buffer)
                for text in new_texts:
                    yield text

    def _parse_stream_buffer(self, decoder: json.JSONDecoder, buffer: str) -> tuple[str, list[str]]:
        new_texts = []
        while True:
            buffer = buffer.lstrip(", \r\n\t[]")
            if not buffer:
                break
            try:
                data, end_idx = decoder.raw_decode(buffer)
                if isinstance(data, dict) and "candidates" in data:
                    text = (
                        data["candidates"][0].get("content", {}).get("parts", [{}])[0].get("text")
                    )
                    if text:
                        new_texts.append(text)
                buffer = buffer[end_idx:]
            except json.JSONDecodeError:
                break
        return buffer, new_texts
