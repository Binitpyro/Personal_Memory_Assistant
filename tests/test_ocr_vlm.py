"""Tier 3: turning a chat reply into page text. No network, no model."""

import pytest

from app.ocr.types import OcrLine, OcrPage
from app.ocr.vlm_engine import VLM_CONFIDENCE, looks_like_commentary, to_page


def test_a_plain_transcription_becomes_lines():
    page = to_page(0, "INVOICE 4471\nAcme Ltd\nTotal: 91.20", elapsed_ms=1200)

    assert page.error is None
    assert [ln.text for ln in page.lines] == ["INVOICE 4471", "Acme Ltd", "Total: 91.20"]
    assert page.mean_conf == VLM_CONFIDENCE
    assert page.elapsed_ms == 1200


def test_confidence_is_uniform_and_above_the_floor():
    """A chat model returns no per-line scores, so none are invented.

    The value must clear the default `ocr_conf_floor` (0.30) or every VLM line
    would be filtered out of the index as low-confidence.
    """
    page = to_page(0, "one\ntwo", elapsed_ms=0)
    assert {ln.conf for ln in page.lines} == {VLM_CONFIDENCE}
    assert not any(ln.low for ln in page.lines)
    assert VLM_CONFIDENCE > 0.30


def test_markdown_fences_are_stripped():
    """Models add fences despite being told not to; they are not page text."""
    page = to_page(0, "```\nHello world\n```", elapsed_ms=0)
    assert [ln.text for ln in page.lines] == ["Hello world"]


def test_a_lead_in_sentence_is_dropped():
    page = to_page(0, "Here is the transcription:\nPage one text", elapsed_ms=0)
    assert [ln.text for ln in page.lines] == ["Page one text"]


def test_commentary_is_refused_rather_than_indexed():
    """The corpus-poisoning case this tier is most exposed to.

    The user picks the model themselves, so a text-only model can be selected.
    Handed an image it cannot see, it describes nothing or apologises - and that
    reply must not be stored as the document's text.
    """
    page = to_page(0, "I'm sorry, I cannot read images.", elapsed_ms=0)

    assert page.error == "VLM_COMMENTARY"
    assert page.lines == ()
    assert page.indexable_text == ""


@pytest.mark.parametrize(
    "reply",
    [
        "The image shows a scanned invoice with a logo.",
        "As an AI, I am unable to process this.",
        "This appears to be a photograph of a document.",
    ],
)
def test_description_style_replies_are_caught(reply):
    assert looks_like_commentary(reply)


def test_real_text_starting_with_a_similar_phrase_is_kept():
    """A page may legitimately begin with a sentence about an image."""
    body = "The image shown on page 4 is reproduced with permission of the archive."
    assert not looks_like_commentary("INTRODUCTION\n" + body)

    page = to_page(0, "INTRODUCTION\n" + body, elapsed_ms=0)
    assert page.error is None
    assert len(page.lines) == 2


def test_an_empty_reply_is_a_blank_page_not_an_error():
    """ "No text on this page" is a valid outcome, not a failure to retry."""
    page = to_page(3, "   \n  \n", elapsed_ms=50)

    assert page.error is None
    assert page.lines == ()
    assert page.mean_conf == 0.0


def test_blank_lines_inside_a_page_are_dropped():
    page = to_page(0, "alpha\n\n\nbeta\n", elapsed_ms=0)
    assert [ln.text for ln in page.lines] == ["alpha", "beta"]


def test_the_vlm_cache_identity_names_the_model(monkeypatch):
    """Two vision models transcribe differently, so switching must miss cache."""
    from app.ocr import settings as ocr_settings

    monkeypatch.setattr(ocr_settings.settings, "ocr_tier", "vlm")
    monkeypatch.setattr(
        ocr_settings, "vlm_selection", lambda: {"provider": "ollama", "model": "glm-ocr"}
    )
    first = ocr_settings.expected_engine_identity()

    monkeypatch.setattr(
        ocr_settings, "vlm_selection", lambda: {"provider": "ollama", "model": "qwen3-vl"}
    )
    second = ocr_settings.expected_engine_identity()

    assert first == "vlm:ollama:glm-ocr"
    assert first != second


async def test_vlm_document_opens_the_pdf_once(monkeypatch, tmp_path):
    """The loop called render_page_png per page, which is a full parse each time.

    A 50-page document therefore paid 50 PDF opens. This asserts the count
    directly rather than the timing, so it cannot pass by being fast.
    """
    from app.ocr import manager as manager_mod
    from app.ocr import raster_png
    from app.ocr.registry import _smoke_test_pdf

    pdf = _smoke_test_pdf(tmp_path / "multi.pdf")
    opens = {"n": 0}

    # manager imports open_pdf inside the function, so the name resolves from
    # raster_png at call time - that is what has to be patched.
    real_open = raster_png.open_pdf

    def counting_open(path):
        opens["n"] += 1
        return real_open(path)

    monkeypatch.setattr(raster_png, "open_pdf", counting_open)

    async def fake_recognize(path, page_num, *, doc=None):
        assert doc is not None, "the loop must pass its open handle down"
        return OcrPage(page_num=page_num, lines=(OcrLine(f"page {page_num}", 0.9, False),))

    monkeypatch.setattr("app.ocr.vlm_engine.recognize_page", fake_recognize, raising=True)

    mgr = manager_mod.OcrManager.__new__(manager_mod.OcrManager)
    mgr._stopping = False

    pages, err = await mgr._run_document_vlm(pdf, [0, 0, 0, 0, 0])

    assert err == ""
    assert len(pages) == 5
    assert opens["n"] == 1, f"expected one open for the document, got {opens['n']}"


async def test_vlm_unopenable_pdf_still_reports_per_page(monkeypatch, tmp_path):
    """Opening once must not turn a bad file into a silent whole-document loss."""
    from app.ocr import manager as manager_mod

    junk = tmp_path / "broken.pdf"
    junk.write_bytes(b"not a pdf at all")

    mgr = manager_mod.OcrManager.__new__(manager_mod.OcrManager)
    mgr._stopping = False

    pages, err = await mgr._run_document_vlm(junk, [0, 1, 2])

    assert err == ""
    assert [p.page_num for p in pages] == [0, 1, 2]
    assert all(p.error == "RASTER_FAILED" for p in pages)
