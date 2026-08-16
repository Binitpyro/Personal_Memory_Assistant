"""OCR Tier 3: transcribe a page with a vision model the user already runs.

No local engine and no worker venv. The page is rasterized in-process
(`raster_png`), sent to the user's own Ollama or LM Studio through the existing
provider abstraction, and the reply is turned into the same `OcrPage` the ONNX
tiers produce - so the cache, the queue and the indexer need no special case.

Two things are deliberately unlike Tier 1/2:

* **Confidence is synthetic.** A chat model returns text, not per-line scores.
  Every line is recorded at a fixed confidence rather than an invented one, and
  that value sits above the default floor so the text is indexed. Pretending to
  a per-line score the model never produced would make `ocr_conf_floor`
  meaningless on this tier.
* **Timeouts are separate.** Minutes per page is normal here; the Tier 1/2
  budgets would fail every document. See `ocr_vlm_*` in `app.config`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from app.config import settings
from app.ocr.raster_png import RasterError, render_page_png
from app.ocr.types import OcrLine, OcrPage

logger = logging.getLogger(__name__)

#: Confidence recorded for every VLM line. Above the 0.30 default floor so the
#: text reaches the index, and a round number so it is obvious in the cache that
#: it was assigned rather than measured.
VLM_CONFIDENCE = 0.90

#: Kept blunt on purpose. Anything inviting commentary ("describe", "summarise")
#: gets commentary, and it would be cached and indexed as the page's text.
TRANSCRIBE_PROMPT = (
    "Transcribe all text in this image exactly as it appears. "
    "Preserve line breaks and reading order. "
    "Output only the transcribed text, with no commentary, no preamble, "
    "and no markdown fences. If the image contains no text, output nothing."
)

#: Openers a chat model reaches for when it is about to editorialise instead of
#: transcribe. Matched case-insensitively against the first line only.
_REFUSAL_PREFIXES = (
    "i'm sorry",
    "i am sorry",
    "i cannot",
    "i can't",
    "sorry,",
    "as an ai",
    "the image shows",
    "the image contains",
    "this image shows",
    "this appears to be",
    "here is the transcription",
    "here's the transcription",
)


class VlmNotConfiguredError(RuntimeError):
    """No provider/model selected, or the selection is unusable."""


def _strip_wrapper(text: str) -> str:
    """Remove a markdown fence and a leading meta sentence, if present.

    Asked for no fences, models still emit them; asked for no preamble, they
    still say "Here is the transcription:". Both would otherwise be cached and
    indexed as if they were words on the page.
    """
    cleaned = (text or "").strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    lines = cleaned.splitlines()
    if lines:
        head = lines[0].strip().lower()
        # Only drop it when it is a short standalone lead-in; a page can
        # legitimately begin with a sentence about an image.
        if (
            head.endswith(":")
            and len(head) < 60
            and any(head.startswith(p) for p in _REFUSAL_PREFIXES)
        ):
            cleaned = "\n".join(lines[1:]).strip()

    return cleaned


def looks_like_commentary(text: str) -> bool:
    """True when the reply reads as description rather than transcription.

    A text-only model handed an image it cannot see will confidently describe
    nothing, or apologise. Either way the result must not be cached as page
    text - that is the corpus-poisoning failure mode this tier is most exposed
    to, because the user picks the model themselves.
    """
    head = (text or "").strip().splitlines()
    if not head:
        return False
    first = head[0].strip().lower()
    return any(first.startswith(prefix) for prefix in _REFUSAL_PREFIXES)


#: Maximum allowed character count for a VLM transcription response.
#: Prevents misbehaving or looping local models from inflating memory in the main process.
_MAX_VLM_REPLY_CHARS = 500_000


def to_page(page_num: int, text: str, elapsed_ms: int) -> OcrPage:
    """Turn a model reply into an `OcrPage`."""
    if text and len(text) > _MAX_VLM_REPLY_CHARS:
        logger.warning(
            "VLM reply for page %s exceeded max length (%d chars), rejecting",
            page_num,
            len(text),
        )
        return OcrPage(
            page_num=page_num,
            lines=(),
            mean_conf=0.0,
            elapsed_ms=elapsed_ms,
            error="VLM_PAYLOAD_TOO_LARGE",
        )

    cleaned = _strip_wrapper(text)
    if not cleaned:
        return OcrPage(page_num=page_num, lines=(), mean_conf=0.0, elapsed_ms=elapsed_ms)

    if looks_like_commentary(cleaned):
        return OcrPage(
            page_num=page_num,
            lines=(),
            mean_conf=0.0,
            elapsed_ms=elapsed_ms,
            error="VLM_COMMENTARY",
        )

    lines = tuple(
        OcrLine(text=line.rstrip(), conf=VLM_CONFIDENCE, low=False)
        for line in cleaned.splitlines()
        if line.strip()
    )
    return OcrPage(
        page_num=page_num,
        lines=lines,
        mean_conf=VLM_CONFIDENCE if lines else 0.0,
        elapsed_ms=elapsed_ms,
    )


async def recognize_page(path: str | Path, page_num: int) -> OcrPage:
    """Rasterize one page and transcribe it with the selected vision model.

    Never raises for a page-level problem: a bad page returns an error record so
    the rest of the document still indexes, matching the worker's contract.
    """
    from app.ocr.settings import vlm_selection
    from app.providers import create_provider, env_base_url
    from app.providers.registry import PROVIDER_REGISTRY
    from app.providers.vision import build_vision_messages

    selection = vlm_selection()
    if not selection:
        raise VlmNotConfiguredError("No vision model has been selected for OCR.")

    provider_id, model = selection["provider"], selection["model"]
    spec = PROVIDER_REGISTRY.get(provider_id)
    if spec is None:
        raise VlmNotConfiguredError(f"Unknown provider {provider_id!r}.")

    started = time.time()
    try:
        image = await asyncio.to_thread(render_page_png, path, page_num, settings.ocr_dpi)
    except RasterError as exc:
        logger.debug("raster failed on page %s: %s", page_num, exc)
        return OcrPage(
            page_num=page_num,
            error="RASTER_FAILED",
            elapsed_ms=int((time.time() - started) * 1000),
        )

    messages = build_vision_messages(provider_id, TRANSCRIBE_PROMPT, image)
    provider = create_provider(
        provider_id,
        api_key=None,
        base_url=env_base_url(provider_id) or spec.default_base_url,
        default_model=model,
        timeout=settings.ocr_vlm_request_timeout_s,
    )
    try:
        reply = await asyncio.wait_for(
            provider.chat(messages, model=model, temperature=0.0, max_tokens=4096),
            timeout=settings.ocr_vlm_page_timeout_s,
        )
    except TimeoutError:
        return OcrPage(
            page_num=page_num,
            error="OCR_PAGE_TIMEOUT",
            elapsed_ms=int((time.time() - started) * 1000),
        )
    except Exception as exc:
        logger.warning("VLM OCR failed on page %s: %s", page_num, exc)
        return OcrPage(
            page_num=page_num,
            error="VLM_REQUEST_FAILED",
            elapsed_ms=int((time.time() - started) * 1000),
        )
    finally:
        await provider.close()

    return to_page(page_num, reply, int((time.time() - started) * 1000))
