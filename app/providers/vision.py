"""Building a chat message that carries an image, per provider.

Ollama and the OpenAI-compatible providers accept images in two incompatible
shapes, and neither matches the plain ``{"role", "content"}`` text message:

* Ollama puts the image in a **sibling key** on the message -
  ``{"role": "user", "content": "...", "images": ["<base64>"]}``.
* OpenAI-compatible (LM Studio, and anything speaking that dialect) replaces
  ``content`` with a **list of parts** -
  ``[{"type": "text", ...}, {"type": "image_url", "image_url": {"url": "data:..."}}]``.

Both providers post ``messages`` to their API verbatim, so shaping it here means
no provider implementation needs a vision-specific code path. The alternative -
a ``chat_vision()`` on every provider - would add a method to eight classes that
seven of them could not implement.

Stdlib only, so this is importable from the OCR worker's constrained venv as
well as the main process.
"""

from __future__ import annotations

import base64
from typing import Any

#: Providers whose message shape this module knows how to build. A provider
#: absent here cannot be handed an image at all, which is a different failure
#: from "the model does not have a vision head".
OLLAMA_STYLE = frozenset({"ollama"})
OPENAI_STYLE = frozenset({"lm_studio", "openai_compatible", "openai", "openrouter", "nvidia_nim"})


#: Name fragments that identify a model with a vision head. Matched as
#: substrings on the lowercased model id, which is all a model list gives us -
#: neither Ollama's /api/tags nor the OpenAI /models response reports
#: modalities. It is therefore a heuristic and is treated as one: it drives a
#: *warning* on the picker, never a refusal, so a vision model this list has not
#: heard of is still selectable.
_VISION_NAME_FRAGMENTS: tuple[str, ...] = (
    "llava",
    "bakllava",
    "moondream",
    "minicpm-v",
    "vision",
    "-vl",
    "vl-",
    "pixtral",
    "cogvlm",
    "internvl",
    "gemma3",
    "gemma4",
    "medgemma",
    # Document-OCR vision models (glm-ocr, deepseek-ocr). Hyphen-anchored so a
    # model merely mentioning ocr in some other position does not match.
    "-ocr",
)

#: Shown when the user has no vision model installed. Names verified against
#: ollama.com's vision listing on 2026-08-14 rather than recalled - the library
#: turns over fast, and a name that is merely plausible produces an `ollama
#: pull` that fails in the user's terminal with nothing to explain why.
#:
#: The OCR-specific models lead deliberately: they are trained for document
#: transcription, which is the job here, rather than for describing pictures.
SUGGESTED_VISION_MODELS: tuple[str, ...] = (
    "glm-ocr",
    "deepseek-ocr",
    "minicpm-v4.6",
    "qwen3-vl",
)


def looks_like_vision_model(model_id: str) -> bool:
    """Best-effort guess at whether `model_id` can read an image.

    Deliberately not authoritative. The consequence of a wrong guess matters
    asymmetrically: a missed vision model is a mild annoyance, whereas silently
    running OCR through a text-only model produces confident hallucinated page
    text that lands in the search index. So callers warn on a negative rather
    than blocking, and the real proof is the first page coming back sane.
    """
    name = (model_id or "").lower()
    return any(fragment in name for fragment in _VISION_NAME_FRAGMENTS)


class UnsupportedVisionProviderError(ValueError):
    """Raised when a provider has no known way to accept an image."""


def supports_vision_messages(provider_id: str) -> bool:
    return provider_id in OLLAMA_STYLE or provider_id in OPENAI_STYLE


def build_vision_messages(
    provider_id: str,
    prompt: str,
    image_bytes: bytes,
    *,
    mime_type: str = "image/png",
) -> list[dict[str, Any]]:
    """One user message carrying `prompt` and `image_bytes`, shaped for the provider.

    Raises `UnsupportedVisionProviderError` rather than silently sending a
    text-only message: a page image that quietly failed to attach would make
    the model describe nothing at all, and that output would be cached and
    indexed as though it were the document's text.
    """
    if not image_bytes:
        raise ValueError("refusing to build a vision message with no image")

    encoded = base64.b64encode(image_bytes).decode("ascii")

    if provider_id in OLLAMA_STYLE:
        return [{"role": "user", "content": prompt, "images": [encoded]}]

    if provider_id in OPENAI_STYLE:
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                    },
                ],
            }
        ]

    raise UnsupportedVisionProviderError(
        f"Provider {provider_id!r} has no known message format for images."
    )
