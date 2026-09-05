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

# Used only when Ollama does not report a window for a model. Ollama has carried
# `details.context_length` in /api/tags for a long time, but an older server or a
# hand-built manifest may omit it, and a listing that drops the model entirely
# would be worse than one carrying a conservative number.
_FALLBACK_CONTEXT_LENGTH = 8192


def _reported_context_length(item: dict[str, Any]) -> int:
    """The model's real context window, as Ollama reports it.

    This was the literal 8192 for **every** model until 2026-09-04, which is
    wrong for most of them and is rendered to the user at
    `frontend/src/pages/ProvidersPage.tsx:700` as "N ctx" - so someone picking a
    model for long documents was shown 8,192 for a 131,072-token model. Measured
    on this machine:

        gemma4-local       131072      qwen-coder-local     32768
        gemma4-12B-local   262144      gemma2-2b             8192
        glm-ocr            131072      nomic-embed-text      2048

    Read from the `/api/tags` response that `list_models` already fetches - the
    value is inside `details` - so this costs no extra request. Verified against
    `/api/show` for all six models above: identical every time.

    **This number is the model's declared window, NOT a budget.** CLAUDE.md 8.7f
    measured `gemma2-2b` truncating at ~4,099 tokens, head-first and silently,
    while declaring 8,192 - so the declared window can be 2x what the model
    actually honours. Nothing in the context-budget path reads this, and the
    design review that added it rejected making it a budget source for exactly
    that reason. It is display and diagnostics only.
    """
    raw = (item.get("details") or {}).get("context_length")
    if isinstance(raw, int) and raw > 0:
        return raw
    return _FALLBACK_CONTEXT_LENGTH


class OllamaProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
        timeout: float = 30.0,
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

        from app.providers.vision import looks_like_vision_model

        models = []
        for item in data.get("models", []):
            name = item.get("name")
            if name:
                # Was hardcoded "chat" for every model, which made a vision model
                # indistinguishable from a text one - so a picker had nothing to
                # filter on and no way to detect "you have none installed".
                models.append(
                    {
                        "id": name,
                        "context_length": _reported_context_length(item),
                        "pricing_hint": 0.0,
                        "family": "vision" if looks_like_vision_model(name) else "chat",
                    }
                )
        return cast(list[ModelInfo], models)

    async def validate(self) -> ValidationResult:
        cached = validation_cache.get(self.spec.id, self.base_url, self.api_key)
        if cached:
            return cast(ValidationResult, cached)

        client = self._get_client()
        url = f"{self.base_url}{self.spec.models_endpoint}"

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
                    result["error"] = f"Failed to parse Ollama models: {parse_err!s}"
            else:
                result["error_code"] = "provider_down"
                result["error"] = f"Ollama returned status {resp.status_code}."

        except Exception as e:
            fallback = validation_cache.get_offline_fallback(self.spec.id)
            if fallback:
                result = fallback
            else:
                result["error_code"] = "network"
                result["error"] = (
                    f"Cannot connect to Ollama at {self.base_url}. Is Ollama running? ({e!s})"
                )

        logger.info(
            "Validation for ollama: ok=%s, error_code=%s, error=%s",
            result["ok"],
            result["error_code"],
            result["error"],
        )

        validation_cache.set(self.spec.id, self.base_url, self.api_key, result)
        return result

    def _chat_payload(
        self,
        messages: list[dict[str, Any]],
        model_name: str,
        temperature: float,
        max_tokens: int,
        *,
        stream: bool,
        think: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if think is not None:
            payload["think"] = think
        return payload

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        """Ask the model and return its answer text.

        **A thinking model can spend the whole `num_predict` budget reasoning and
        return an EMPTY `content`.** Ollama puts the reasoning in a separate
        `message.thinking` field, which this provider does not use and the UI
        never shows, so before 2026-09-04 the user simply got a blank answer with
        no error to explain it. Measured on this machine: 13 of 100 eval queries
        on `gemma4-local` at chunk_size=2048 (CLAUDE.md 8.7h).

        Reproduced directly - a 6,000-character RAG prompt at `num_predict=256`
        returns `content=''`, `thinking=1113 chars`, `done_reason='length'`,
        while the identical request with `think: false` returns 1,271 characters
        of real answer inside the same budget.

        So the recovery is one retry with thinking off. Deliberately a RECOVERY
        and not a blanket `think: false`: reasoning measured fine at the shipped
        chunk_size=1024 (zero empties, 0.9133 answer-recall), and turning it off
        everywhere would change behaviour that currently works on the strength of
        no evidence at all. This path only fires where the answer is already lost.

        `think` is safe to send to models that do not support it - verified
        against `gemma2-2b`, which accepts it and answers normally.
        """
        client = self._get_client()
        url = f"{self.base_url}/api/chat"
        model_name = model or self.default_model

        resp = await client.post(
            url,
            json=self._chat_payload(messages, model_name, temperature, max_tokens, stream=False),
        )
        resp.raise_for_status()
        data = resp.json()
        content = str(data.get("message", {}).get("content") or "")
        if content:
            return content

        # Only retry the diagnosable case. An empty answer with no reasoning
        # behind it is the model's own choice and repeating the call would just
        # cost another round trip to get the same nothing.
        thinking = str(data.get("message", {}).get("thinking") or "")
        if not thinking:
            logger.warning(
                "Ollama model %s returned empty content (done_reason=%s) and no reasoning; "
                "not retrying.",
                model_name,
                data.get("done_reason"),
            )
            return ""

        logger.warning(
            "Ollama model %s spent its %d-token budget reasoning (%d chars of thinking, "
            "done_reason=%s) and returned no answer. Retrying once with think=false.",
            model_name,
            max_tokens,
            len(thinking),
            data.get("done_reason"),
        )
        retry = await client.post(
            url,
            json=self._chat_payload(
                messages, model_name, temperature, max_tokens, stream=False, think=False
            ),
        )
        retry.raise_for_status()
        return str(retry.json().get("message", {}).get("content") or "")

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        """Stream the model's answer.

        Carries the same thinking-model failure `chat` documents: reasoning
        arrives as `message.thinking` and is not answer text, so a model that
        exhausts its budget reasoning streams nothing at all and the user watches
        an empty response complete successfully. Recovered the same way - if the
        stream produced no content and reasoning was seen, replay it once with
        `think: false`.
        """
        client = self._get_client()
        url = f"{self.base_url}/api/chat"
        model_name = model or self.default_model

        async def _once(think: bool | None) -> AsyncGenerator[tuple[str, bool], None]:
            """Yield (text, is_thinking) for one pass over the stream."""
            payload = self._chat_payload(
                messages, model_name, temperature, max_tokens, stream=True, think=think
            )
            async with client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                        msg = parsed.get("message", {})
                        content = msg.get("content")
                        if content:
                            yield content, False
                        elif msg.get("thinking"):
                            # Not yielded to the caller - it is reasoning, not an
                            # answer - but recorded so the retry can be gated on
                            # having actually seen some.
                            yield "", True
                        if parsed.get("done", False):
                            break
                    except Exception as e:
                        logger.debug("Failed to parse Ollama stream chunk: %s", e)
                        continue

        produced = thought = False
        async for text, is_thinking in _once(None):
            if is_thinking:
                thought = True
                continue
            produced = True
            yield text

        if produced or not thought:
            return

        logger.warning(
            "Ollama model %s streamed only reasoning and no answer. Replaying with think=false.",
            model_name,
        )
        async for text, is_thinking in _once(False):
            if not is_thinking:
                yield text
