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

    **This number is the model's declared window, NOT a budget**, and nothing in
    the context-budget path reads it. Display and diagnostics only.

    The reason that separation matters turned out to be different from the one
    written here first. This docstring originally cited CLAUDE.md 8.7f's ~4,099
    truncation on `gemma2-2b` as proof that a declared window can be 2x what a
    model honours. **That reading is retracted** - see `_required_num_ctx`: the
    4,096 cliff is Ollama's own default `num_ctx`, reproduced identically on
    `gemma4-local` with its 131,072-token window, so it was never a property of
    the model or of this number.

    What survives is the narrower and still sufficient point: the declared window
    says what the model was trained for, not what this machine can serve. Section
    6 targets ~4GB VRAM, and a 262,144-token window is a fact about the 12B, not
    about the hardware underneath it.
    """
    raw = (item.get("details") or {}).get("context_length")
    if isinstance(raw, int) and raw > 0:
        return raw
    return _FALLBACK_CONTEXT_LENGTH


# Ollama's own default context window. Measured on this machine 2026-09-04, not
# recalled: a prompt of ~4,000 tokens is ingested whole (prompt_eval_count 4015)
# and ~4,200 collapses to 2051 - so the cliff is 4096, and past it Ollama
# discards roughly HALF the prompt rather than just the overflow.
_OLLAMA_DEFAULT_NUM_CTX = 4096

# Chars per token. Deliberately low: CLAUDE.md section 6 measured 5.09 for this
# corpus, so dividing by 4 OVER-estimates the token count. Over-estimating costs
# a slightly larger KV cache; under-estimating silently loses context, which is
# the bug this exists to prevent.
_CHARS_PER_TOKEN = 4

# Room for the chat template, role markers and any tool preamble Ollama wraps
# around the messages - `prompt_eval_count` ran ~15 tokens above the raw content
# in every measurement above.
_NUM_CTX_HEADROOM = 256


def _required_num_ctx(messages: list[dict[str, Any]], max_tokens: int) -> int:
    """How large Ollama's context window must be for this request.

    **PMA never set `num_ctx`, and Ollama's default silently discarded most of a
    long prompt.** Measured against a live server on 2026-09-04 with
    `gemma4-local`, whose declared window is 131,072:

        ~6,000-token prompt, num_ctx unset   -> prompt_eval_count = 2051
        ~6,000-token prompt, num_ctx=16384   -> prompt_eval_count = 6015

    So `compute_context_budget` would hand `7b_local` an 8,520-token budget,
    `build_context` would fill it, and Ollama would throw most of it away before
    the model saw a word of it. The class with the LARGEST budget was the one
    losing the most.

    **This retracts a claim.** CLAUDE.md 8.7f recorded `gemma2-2b` truncating at
    ~4,099 tokens "head first" and read that as a property of the model. It is
    not: `gemma4-local` behaves identically despite a 131,072-token declared
    window, because the limit belongs to the *server default*, not the model.

    Sized to the request rather than maxed out, because `num_ctx` sizes the KV
    cache and section 6 targets a ~4GB VRAM machine. Left unset below the default
    so short prompts allocate exactly what they do today.

    Not clamped to the model's declared window: every chat model here declares at
    least 8,192 and PMA's largest local ceiling is 10,000, so the clamp cannot
    bind. Revisit if a ceiling ever exceeds a declared window.
    """
    chars = sum(len(str(m.get("content") or "")) for m in messages)
    return chars // _CHARS_PER_TOKEN + max_tokens + _NUM_CTX_HEADROOM


def _family(item: dict[str, Any]) -> str:
    """Whether this model can read an image, from Ollama rather than its name.

    `/api/tags` reports a `capabilities` list per model, in the response
    `list_models` already fetches - so this is authoritative and costs nothing.

    It replaces `looks_like_vision_model`, a substring match over a fragment list
    that includes `"gemma4"`. Measured against the live server on 2026-09-04, that
    heuristic was **wrong in the dangerous direction** on two of six models here:

        glm-ocr            vision,completion,tools      guessed vision   correct
        gemma4-local       completion,tools,thinking    guessed vision   FALSE POSITIVE
        gemma4-12B-local   completion,tools,thinking    guessed vision   FALSE POSITIVE
        gemma2-2b          completion                   guessed chat     correct
        qwen-coder-local   completion                   guessed chat     correct
        nomic-embed-text   embedding                    guessed chat     correct

    `looks_like_vision_model`'s own docstring names why a false positive is the
    costly one: "silently running OCR through a text-only model produces
    confident hallucinated page text that lands in the search index". The OCR
    Tier 3 picker (`app/ocr/api.py`) is built from this, so both Gemma 4 models
    were being offered as vision models for page images they cannot read.

    The heuristic stays as the fallback for an older server that reports no
    capabilities at all, and remains the only option for providers that expose
    nothing equivalent - LM Studio goes through `openai_compat`, which has no
    such field.
    """
    from app.providers.vision import looks_like_vision_model

    caps = item.get("capabilities")
    if isinstance(caps, list) and caps:
        return "vision" if "vision" in caps else "chat"
    return "vision" if looks_like_vision_model(str(item.get("name") or "")) else "chat"


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
                        "family": _family(item),
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
        options: dict[str, Any] = {"temperature": temperature, "num_predict": max_tokens}
        num_ctx = _required_num_ctx(messages, max_tokens)
        if num_ctx > _OLLAMA_DEFAULT_NUM_CTX:
            options["num_ctx"] = num_ctx
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
            "options": options,
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
