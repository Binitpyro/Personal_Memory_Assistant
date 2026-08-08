"""Detection gate: decide whether a PDF page needs OCR.

Runs inside `PdfExtractor.extract_stream`, which executes on the shared
4-worker extract pool. Everything here must therefore be O(1) per page and
must never decode a stream. The ordering below is the whole feature - a page
that already has good text returns before any resource dictionary is touched.

Known limitation, inherited from the design: this detects *absent* extraction,
never *bad* extraction. A multi-column layout that pypdf scrambles into
readable-but-wrong order still has plenty of printable characters and passes
as NATIVE. The only mitigation is an explicit Force OCR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.ocr.types import PageSignal, PageVerdict

logger = logging.getLogger(__name__)

# Characters that are legitimate in extracted text but not alphanumeric.
# Anything outside this set and str.isalnum() counts toward the garbage score.
_ALLOWED_PUNCT = frozenset(" \t\r\n.,;:!?'\"()[]{}<>/\\|-_=+*&^%$#@~`" + "‘’“”–—…· ")


@dataclass(frozen=True)
class GateConfig:
    """Thresholds for one gate pass.

    Passed in rather than read from globals so the gate is testable without
    monkeypatching `app.config.settings`.
    """

    min_chars: int = 100
    garbage_ratio: float = 0.30
    blank_stream_bytes: int = 512


def default_gate_config() -> GateConfig:
    return GateConfig(
        min_chars=settings.ocr_min_chars_per_page,
        garbage_ratio=settings.ocr_garbage_ratio,
        blank_stream_bytes=settings.ocr_blank_stream_bytes,
    )


def garbage_ratio(text: str) -> float:
    """Fraction of characters that look like mojibake or control bytes.

    Catches the CID-font failure mode where pypdf returns a page's worth of
    U+FFFD replacement characters - technically "text", entirely useless.
    Returns 1.0 for empty input so a blank page never reads as clean.

    CJK passes cheaply: `str.isalnum()` is true for Han, Hiragana, Katakana
    and Hangul, so a Japanese page scores near zero rather than 1.0.
    """
    if not text:
        return 1.0

    bad = 0
    total = 0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
        if ch == "�" or not ch.isprintable() or (not ch.isalnum() and ch not in _ALLOWED_PUNCT):
            bad += 1

    if total == 0:
        return 1.0
    return bad / total


def _count_image_xobjects(page: Any) -> int:
    """Count image XObjects on a page without decoding any stream.

    `.get_object()` on an IndirectObject resolves to the EncodedStreamObject
    but does *not* decode its data, so reading /Subtype off it stays a
    dictionary lookup. `page.images`, by contrast, decodes every image on the
    page - on a 300-page scan that is minutes of wasted work, which is exactly
    what this gate exists to avoid.

    Any malformed structure degrades to 0 ("no images") rather than raising:
    a broken resource dict must not abort extraction of an otherwise fine PDF.
    """
    try:
        resources = page.get("/Resources")
        if resources is None:
            return 0
        if hasattr(resources, "get_object"):
            resources = resources.get_object()

        xobjects = resources.get("/XObject")
        if xobjects is None:
            return 0
        if hasattr(xobjects, "get_object"):
            xobjects = xobjects.get_object()

        count = 0
        for key in xobjects:
            try:
                entry = xobjects.raw_get(key) if hasattr(xobjects, "raw_get") else xobjects[key]
                if hasattr(entry, "get_object"):
                    entry = entry.get_object()
                if entry.get("/Subtype") == "/Image":
                    count += 1
            except Exception:
                continue
        return count
    except Exception:
        return 0


def _content_stream_bytes(page: Any) -> int:
    """Encoded length of the page's content stream(s), from the dictionary.

    Reads /Length rather than measuring decoded data, so this stays O(1) and
    never inflates a compressed bomb.
    """
    try:
        contents = page.get("/Contents")
        if contents is None:
            return 0
        if hasattr(contents, "get_object"):
            contents = contents.get_object()

        # /Contents may be a single stream or an array of them.
        streams = contents if isinstance(contents, list) else [contents]
        total = 0
        for stream in streams:
            try:
                obj = stream.get_object() if hasattr(stream, "get_object") else stream
                length = obj.get("/Length")
                if hasattr(length, "get_object"):
                    length = length.get_object()
                total += int(length or 0)
            except Exception:
                continue
        return total
    except Exception:
        return 0


def classify_page(page: Any, text: str, cfg: GateConfig | None = None) -> PageSignal:
    """Decide NATIVE / OCR / BLANK for one page.

    The order matters and is the reason this is not a set of independent
    checks: step 2 must return before step 3 runs, so a normal text page never
    pays for resource traversal.
    """
    cfg = cfg or default_gate_config()

    # 1. How much text did the extractor actually get?
    stripped = text.strip() if text else ""
    n = len(stripped)
    gr = garbage_ratio(stripped)

    # 2. Enough clean text -> done. No resource inspection whatsoever.
    if n >= cfg.min_chars and gr < cfg.garbage_ratio:
        return PageSignal(PageVerdict.NATIVE, n, gr, -1, -1)

    # 3-4. Any image on the page means there is probably ink we can't read.
    #
    # This uses image *presence*, not coverage. True coverage needs the content
    # stream's CTM, which costs a parse per page. The accepted false positive
    # is a short text page carrying a logo: it gets OCR'd unnecessarily. That
    # is bounded work; a missed scan is a silently unsearchable document.
    images = _count_image_xobjects(page)
    if images > 0:
        return PageSignal(PageVerdict.OCR, n, gr, images, -1)

    # 5. No images but a substantial content stream: text drawn as vector
    #    outlines. Rasterizing is the only way to read it.
    stream_bytes = _content_stream_bytes(page)
    if stream_bytes > cfg.blank_stream_bytes:
        return PageSignal(PageVerdict.OCR, n, gr, 0, stream_bytes)

    # 6. Nothing to extract and nothing to draw.
    return PageSignal(PageVerdict.BLANK, n, gr, 0, stream_bytes)
