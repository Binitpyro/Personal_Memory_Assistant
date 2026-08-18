"""Main-process page rasterization for the VLM tier. No network."""

import pytest

from app.ocr.raster_png import RasterError, render_page_png
from app.ocr.registry import SMOKE_TEST_PHRASE, _smoke_test_pdf

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def one_page_pdf(tmp_path):
    return _smoke_test_pdf(tmp_path / "page.pdf")


def test_renders_a_page_to_png_bytes(one_page_pdf):
    data = render_page_png(one_page_pdf, 0, dpi=150)

    assert data.startswith(PNG_MAGIC)
    # A blank-ish 8.5x11 page at 150 DPI is still substantial; this guards
    # against a zero-byte or truncated buffer being returned as success.
    assert len(data) > 1000


def test_dpi_changes_the_rendered_size(one_page_pdf):
    small = render_page_png(one_page_pdf, 0, dpi=72)
    large = render_page_png(one_page_pdf, 0, dpi=200)
    assert len(large) > len(small)


def test_a_missing_page_raises_rather_than_returning_empty(one_page_pdf):
    """Returning b"" would be sent to the model as a blank page."""
    with pytest.raises(RasterError):
        render_page_png(one_page_pdf, 99, dpi=150)


def test_a_file_that_is_not_a_pdf_raises(tmp_path):
    junk = tmp_path / "not.pdf"
    junk.write_bytes(b"this is not a pdf")
    with pytest.raises(RasterError):
        render_page_png(junk, 0, dpi=150)


def test_a_missing_file_raises(tmp_path):
    with pytest.raises(RasterError):
        render_page_png(tmp_path / "absent.pdf", 0, dpi=150)


def test_an_absurd_dpi_is_refused_rather_than_allocated(one_page_pdf):
    """The pixel ceiling exists so one malformed document is not an OOM."""
    with pytest.raises(RasterError, match="too large"):
        render_page_png(one_page_pdf, 0, dpi=20000)


def test_the_render_is_legible(one_page_pdf):
    """Round-trips through the real OCR engine when one is installed.

    Skipped rather than failed without a provisioned tier: this file must stay
    runnable on a machine that has never installed OCR.
    """
    engine = pytest.importorskip("rapidocr_onnxruntime", reason="OCR engine not in this venv")
    import io as _io

    import numpy as np
    from PIL import Image

    data = render_page_png(one_page_pdf, 0, dpi=300)
    array = np.array(Image.open(_io.BytesIO(data)).convert("RGB"))
    result, _ = engine.RapidOCR()(array)
    text = " ".join(row[1] for row in (result or []))

    assert SMOKE_TEST_PHRASE.split()[0] in text.upper()


def test_open_pdf_renders_many_pages_from_one_handle(one_page_pdf):
    """render_page_png opens and closes per call; open_pdf is the multi-page path."""
    from app.ocr.raster_png import open_pdf, render_page_from

    with open_pdf(one_page_pdf) as doc:
        first = render_page_from(doc, 0, dpi=150)
        second = render_page_from(doc, 0, dpi=150)

    assert first.startswith(PNG_MAGIC)
    assert first == second, "the handle must stay usable across renders"


def test_render_page_from_leaves_the_document_open(one_page_pdf):
    """It must close only the page and bitmap it allocated."""
    from app.ocr.raster_png import open_pdf, render_page_from

    with open_pdf(one_page_pdf) as doc:
        render_page_from(doc, 0, dpi=72)
        # Still usable: a closed document raises here instead.
        assert len(doc) >= 1
        render_page_from(doc, 0, dpi=72)


def test_open_pdf_rejects_a_non_pdf(tmp_path):
    from app.ocr.raster_png import open_pdf

    junk = tmp_path / "notes.txt"
    junk.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(RasterError, match="cannot open PDF"), open_pdf(junk):
        pass
