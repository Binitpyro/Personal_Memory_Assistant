"""Detection gate tests.

The single most important assertion in this file is that a NATIVE verdict is
reached *without touching page resources*. That short-circuit is the whole
reason the gate is cheap enough to run on every page of every PDF, and it is
the thing most likely to be broken by a well-meaning refactor.
"""

from unittest.mock import MagicMock

import pytest

from app.ocr.gate import (
    GateConfig,
    _content_stream_bytes,
    _count_image_xobjects,
    classify_page,
    garbage_ratio,
)
from app.ocr.types import PageVerdict

CFG = GateConfig(min_chars=100, garbage_ratio=0.30, blank_stream_bytes=512)

GOOD_TEXT = (
    "This is an ordinary paragraph of extracted text. It is long enough to clear "
    "the minimum character threshold and contains nothing unusual at all."
)


class _FakeIndirect:
    """Stands in for pypdf's IndirectObject: resolves via .get_object()."""

    def __init__(self, target):
        self._target = target

    def get_object(self):
        return self._target


class _FakeDict(dict):
    def get_object(self):
        return self

    def raw_get(self, key):
        return dict.__getitem__(self, key)


def make_page(*, xobjects=None, content_length=0, explode_on_resources=False):
    """A page object shaped like pypdf's, with tripwires on the expensive parts."""
    page = MagicMock()

    resources = _FakeDict()
    if xobjects is not None:
        resources["/XObject"] = _FakeIndirect(_FakeDict(xobjects))

    contents = _FakeDict({"/Length": content_length})

    def _get(key, default=None):
        if key == "/Resources":
            if explode_on_resources:
                raise ValueError("malformed resource dictionary")
            return resources
        if key == "/Contents":
            return contents
        return default

    page.get = MagicMock(side_effect=_get)

    # page.images decodes every image stream. The gate must never reach it.
    type(page).images = property(
        lambda self: (_ for _ in ()).throw(
            AssertionError("gate touched page.images - that decodes streams")
        )
    )
    return page


def image_xobject(width=800, height=1000):
    return _FakeIndirect(_FakeDict({"/Subtype": "/Image", "/Width": width, "/Height": height}))


def form_xobject():
    return _FakeIndirect(_FakeDict({"/Subtype": "/Form"}))


# ── the short-circuit ────────────────────────────────────────────────────


def test_native_returns_before_touching_resources():
    page = make_page(xobjects={"/Im0": image_xobject()}, content_length=9999)

    signal = classify_page(page, GOOD_TEXT, CFG)

    assert signal.verdict == PageVerdict.NATIVE
    # -1 is the assertion that no resource traversal happened at all.
    assert signal.image_xobjects == -1
    assert signal.stream_bytes == -1
    page.get.assert_not_called()


def test_native_page_with_image_is_still_native():
    """A scanned figure inside an otherwise readable page is not an OCR job."""
    page = make_page(xobjects={"/Im0": image_xobject()})
    assert classify_page(page, GOOD_TEXT * 3, CFG).verdict == PageVerdict.NATIVE


# ── OCR verdicts ─────────────────────────────────────────────────────────


def test_scanned_page_with_no_text_goes_to_ocr():
    page = make_page(xobjects={"/Im0": image_xobject()})
    signal = classify_page(page, "", CFG)
    assert signal.verdict == PageVerdict.OCR
    assert signal.image_xobjects == 1


def test_mojibake_over_threshold_still_goes_to_ocr():
    """The CID-font failure mode: plenty of characters, none of them usable."""
    garbage = "�" * 400
    signal = classify_page(make_page(xobjects={"/Im0": image_xobject()}), garbage, CFG)
    assert signal.verdict == PageVerdict.OCR
    assert signal.char_count >= CFG.min_chars  # long enough to pass step 1
    assert signal.garbage_ratio > CFG.garbage_ratio  # but fails step 2


def test_text_as_outlines_goes_to_ocr():
    """No images, no text, but a big content stream: vector-drawn glyphs."""
    page = make_page(xobjects={}, content_length=4096)
    signal = classify_page(page, "", CFG)
    assert signal.verdict == PageVerdict.OCR
    assert signal.image_xobjects == 0
    assert signal.stream_bytes == 4096


def test_short_text_with_logo_is_the_documented_false_positive():
    page = make_page(xobjects={"/Im0": image_xobject(40, 40)})
    assert classify_page(page, "Page 3", CFG).verdict == PageVerdict.OCR


# ── BLANK ────────────────────────────────────────────────────────────────


def test_empty_page_is_blank():
    page = make_page(xobjects={}, content_length=100)
    signal = classify_page(page, "", CFG)
    assert signal.verdict == PageVerdict.BLANK


def test_page_with_no_resources_at_all_is_blank():
    assert classify_page(make_page(content_length=10), "", CFG).verdict == PageVerdict.BLANK


# ── robustness ───────────────────────────────────────────────────────────


def test_form_xobject_is_not_counted_as_an_image():
    page = make_page(xobjects={"/Fm0": form_xobject()}, content_length=100)
    signal = classify_page(page, "", CFG)
    assert signal.image_xobjects == 0
    assert signal.verdict == PageVerdict.BLANK


def test_malformed_resources_degrade_to_zero_images():
    """A broken resource dict must cost one verdict, not the whole document."""
    page = make_page(explode_on_resources=True)
    assert _count_image_xobjects(page) == 0
    # No exception escapes into the extractor.
    assert classify_page(page, "", CFG).verdict in (PageVerdict.OCR, PageVerdict.BLANK)


def test_one_bad_xobject_entry_does_not_lose_the_others():
    bad = MagicMock()
    bad.get_object = MagicMock(side_effect=ValueError("corrupt"))
    page = make_page(xobjects={"/Bad": bad, "/Im0": image_xobject()})
    assert _count_image_xobjects(page) == 1


def test_content_stream_array_lengths_are_summed():
    page = MagicMock()
    page.get = MagicMock(
        return_value=[_FakeDict({"/Length": 300}), _FakeDict({"/Length": 250})]
    )
    assert _content_stream_bytes(page) == 550


# ── garbage_ratio ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Perfectly ordinary English text, with punctuation!",
        "これは日本語のテキストです。",  # CJK is alnum, must not score as garbage
        "한국어 텍스트입니다",
        "Résumé naïve café — em-dash and accents",
    ],
)
def test_clean_text_scores_low(text):
    assert garbage_ratio(text) < 0.30


@pytest.mark.parametrize("text", ["", "   \n\t  "])
def test_empty_text_scores_as_fully_garbage(text):
    # 1.0 rather than 0.0: an empty page must never read as "clean".
    assert garbage_ratio(text) == 1.0


def test_replacement_characters_score_high():
    assert garbage_ratio("�" * 50) == 1.0


def test_control_characters_score_high():
    assert garbage_ratio("\x01\x02\x03\x04\x05") == 1.0
