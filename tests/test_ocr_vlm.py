"""Tier 3: turning a chat reply into page text. No network, no model."""

import pytest

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
