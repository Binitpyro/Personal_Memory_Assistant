import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any, cast

import httpx

from app.providers.base import ModelInfo, ValidationResult
from app.providers.cache import validation_cache
from app.providers.registry import ProviderSpec

logger = logging.getLogger(__name__)


# How many models the auth probe may try before giving up. A retired model
# answers 410 before the key is ever checked, so one attempt is not enough;
# three bounds the cost of a validation that is otherwise a single GET.
_AUTH_PROBE_MAX_MODELS = 3


class OpenAICompatibleProvider:
    def __init__(
        self,
        spec: ProviderSpec,
        *,
        api_key: str | None,
        base_url: str | None,
        default_model: str | None,
        timeout: float = 30.0,
    ):
        self.spec = spec
        self.api_key = api_key
        # Use provided base_url or fall back to spec default (or None)
        self.base_url = base_url or spec.default_base_url
        self.default_model = default_model
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _get_headers(self) -> dict[str, str]:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def list_models(self) -> list[ModelInfo]:
        client = self._get_client()
        url = f"{self.base_url}{self.spec.models_endpoint}"
        headers = self._get_headers()

        logger.debug("Listing models from %s", url)
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        models = []
        for item in data.get("data", []):
            model_id = item.get("id")
            if model_id:
                models.append(
                    {
                        "id": model_id,
                        "context_length": item.get("context_window"),
                        "pricing_hint": None,
                        "family": self._detect_family(model_id),
                    }
                )
        return cast(list[ModelInfo], models)

    def _detect_family(self, model_id: str) -> str | None:
        from app.providers.vision import looks_like_vision_model

        model_lower = model_id.lower()
        if any(x in model_lower for x in ["o1", "o3", "reasoning", "deepseek-r"]):
            return "reasoning"
        # Shared with the Ollama lister rather than a local substring list: the
        # bare "vl" test here missed llava, moondream, minicpm-v and pixtral,
        # and also matched any id that happened to contain those two letters.
        if looks_like_vision_model(model_lower):
            return "vision"
        return "chat"

    async def validate(self) -> ValidationResult:
        # Check cache
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
            # Enforce 3s budget for validation, 8s hard cap
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
                if result["ok"] and not self.spec.models_endpoint_authenticates:
                    await self._probe_auth(client, result)
            else:
                self._map_http_error(resp.status_code, resp.text, result)

        except httpx.ConnectTimeout:
            fallback = validation_cache.get_offline_fallback(self.spec.id)
            if fallback:
                result = fallback
            else:
                result["error_code"] = "network"
                result["error"] = (
                    f"Cannot reach {self.base_url}. Check firewall / VPN / captive portal."
                )
        except (httpx.ConnectError, httpx.HTTPError) as http_err:
            fallback = validation_cache.get_offline_fallback(self.spec.id)
            if fallback and not isinstance(http_err, httpx.HTTPStatusError):
                result = fallback
            else:
                err_msg = str(http_err)
                if "ssl" in err_msg.lower() or "cert" in err_msg.lower():
                    result["error_code"] = "tls_error"
                    result["error"] = "TLS handshake failed. Verify certificates."
                else:
                    result["error_code"] = "network"
                    result["error"] = f"HTTP error occurred: {err_msg}"
        except Exception as e:
            fallback = validation_cache.get_offline_fallback(self.spec.id)
            if fallback:
                result = fallback
            else:
                result["error_code"] = "provider_down"
                result["error"] = f"Unexpected validation error: {e!s}"

        # Redact keys in logs
        key_preview = self.api_key[:6] + "..." if self.api_key and len(self.api_key) > 6 else "****"
        logger.info(
            "Validation for %s (%s, key: %s): ok=%s, error_code=%s, error=%s",
            self.spec.id,
            url,
            key_preview,
            result["ok"],
            result["error_code"],
            result["error"],
        )

        validation_cache.set(self.spec.id, self.base_url, self.api_key, result)
        return cast(ValidationResult, result)

    async def _probe_auth(self, client: Any, result: ValidationResult) -> None:
        """Second probe for providers whose model listing needs no key.

        Sends the smallest possible completion - one token - purely to make the
        server check the Authorization header. Without it, a provider with a
        public catalogue reports every key as valid, including a fabricated one.

        **This can only ever turn a pass into a failure, never the reverse, and
        only on 401/403.** A timeout, a connection error, a 404 from an unexpected
        route, a 429, a model that refuses chat - all leave the existing verdict
        alone. The current bug is a silent false positive; trading it for a noisy
        false negative that tells someone their working key is broken would be a
        worse deal, so the probe is deliberately one-directional.
        """
        # Several candidates, because a listed model can be RETIRED and NVIDIA
        # answers 410 Gone for those BEFORE it checks the key - measured with a
        # deliberately invalid key, which also returns 410. A probe that stopped
        # at the first candidate would be blinded by exactly that, and was: the
        # configured default had been retired, so the probe learned nothing.
        candidates: list[str] = []
        if self.default_model:
            candidates.append(self.default_model)
        candidates += [m["id"] for m in result["models"] if m["id"] not in candidates]
        if not candidates:
            return

        for model in candidates[:_AUTH_PROBE_MAX_MODELS]:
            try:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._get_headers(),
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                    },
                    timeout=httpx.Timeout(5.0, read=10.0),
                )
            except Exception:  # nosec B110 - inconclusive probe must not invent a failure
                return
            if resp.status_code in (401, 403):
                result["ok"] = False
                self._map_http_error(resp.status_code, resp.text, result)
                return
            if resp.status_code in (404, 410):
                continue  # model is gone, says nothing about the key - try another
            return  # anything else is conclusive enough to leave the verdict alone

    def _map_http_error(
        self, status_code: int, response_text: str, result: ValidationResult
    ) -> None:
        if status_code in (401, 403):
            result["error_code"] = "auth_failed"
            result["error"] = (
                f"Key is invalid or lacks permissions. Regenerate at {self.spec.api_key_docs_url}."
            )
        elif status_code == 404:
            result["error_code"] = "wrong_base_url"
            result["error"] = (
                "URL responded but doesn't look like an OpenAI-compatible API. Check base URL."
            )
        elif status_code == 429:
            result["error_code"] = "rate_limited"
            result["error"] = (
                "Rate-limited even on model listing—account is likely paused or out of credits. Check billing."
            )
        elif status_code >= 500:
            result["error_code"] = "provider_down"
            result["error"] = f"Provider returned {status_code}—try again or switch to a fallback."
        else:
            result["error_code"] = "provider_down"
            result["error"] = f"Request failed with status {status_code}: {response_text[:100]}"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        client = self._get_client()
        url = f"{self.base_url}/chat/completions"
        headers = self._get_headers()
        model_name = model or self.default_model or "default"

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return str(data["choices"][0]["message"]["content"])

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        client = self._get_client()
        url = f"{self.base_url}/chat/completions"
        headers = self._get_headers()
        model_name = model or self.default_model or "default"

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload_str = line[6:].strip()
                if payload_str == "[DONE]":
                    break
                try:
                    parsed = json.loads(payload_str)
                    delta = parsed.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        yield delta
                except Exception as e:
                    logger.debug("Failed to parse stream chunk: %s", e)
                    continue
