import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
import httpx
from app.providers.base import ModelInfo, ValidationResult
from app.providers.openai_compat import OpenAICompatibleProvider
from app.providers.registry import spec_of

logger = logging.getLogger(__name__)


class AnthropicProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None = None,
        default_model: str | None = None,
        timeout: float = 30.0
    ):
        spec = spec_of("anthropic")
        super().__init__(
            spec,
            api_key=api_key,
            base_url=base_url,
            default_model=default_model or "claude-3-5-sonnet-20241022",
            timeout=timeout,
        )

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "anthropic-version": "2023-06-01",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    async def list_models(self) -> list[ModelInfo]:
        client = self._get_client()
        url = f"{self.base_url}{self.spec.models_endpoint}"
        headers = self._get_headers()

        logger.debug("Listing Anthropic models from %s", url)
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        models = []
        for item in data.get("data", []):
            model_id = item.get("id")
            if model_id:
                models.append({
                    "id": model_id,
                    "context_length": 200000,  # Standard Anthropic context window
                    "pricing_hint": None,
                    "family": "chat"
                })
        return models

    def _build_messages_payload(self, messages: list[dict[str, str]]) -> tuple[str | None, list[dict[str, str]]]:
        system_prompt = None
        filtered_messages = []
        for msg in messages:
            role = msg["role"]
            if role == "system":
                system_prompt = msg["content"]
            else:
                if role == "model" or role == "assistant":
                    role = "assistant"
                else:
                    role = "user"
                filtered_messages.append({"role": role, "content": msg["content"]})
        return system_prompt, filtered_messages

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096
    ) -> str:
        client = self._get_client()
        url = f"{self.base_url}/v1/messages"
        headers = self._get_headers()
        model_name = model or self.default_model

        system_prompt, anthropic_msgs = self._build_messages_payload(messages)

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt

        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return str(data["content"][0]["text"])

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096
    ) -> AsyncGenerator[str, None]:
        client = self._get_client()
        url = f"{self.base_url}/v1/messages"
        headers = self._get_headers()
        model_name = model or self.default_model

        system_prompt, anthropic_msgs = self._build_messages_payload(messages)

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if system_prompt:
            payload["system"] = system_prompt

        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload_str = line[6:].strip()
                try:
                    parsed = json.loads(payload_str)
                    event_type = parsed.get("type")
                    if event_type == "content_block_delta":
                        delta_text = parsed.get("delta", {}).get("text")
                        if delta_text:
                            yield delta_text
                except Exception as e:
                    logger.debug("Failed to parse Anthropic stream chunk: %s", e)
                    continue
