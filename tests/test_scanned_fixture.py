"""The scanned-PDF fixture builder, and the gate verdict that makes it useful.

Nothing in the tree could exercise the OCR path before this: every PDF under
`tests/` and in the perf corpus carries a text layer, so every page classified
NATIVE and was never queued. These assert the fixture is genuinely image-only,
which is the property the whole OCR pipeline depends on.

No network, no OCR engine, no model. pypdfium2 is already pinned for the Tier 3
renderer; nothing new is introduced.
"""

import zlib

import pypdf
import pytest

from app.ocr.gate import classify_page
from app.ocr.types import PageVerdict
from scripts.make_scanned_pdf import (
    build_scanned_pdf,
    build_text_pdf,
    rasterize,
    write_scanned_pdf,
)

PHRASE = "PMA OCR 12345"


@pytest.fixture
def scanned_pdf(tmp_path):
    return write_scanned_pdf(tmp_path / "scan.pdf", PHRASE)


def test_scanned_page_is_classified_for_ocr(scanned_pdf):
    """The point of the fixture: it must reach the OCR queue.

    A scanned page short-circuits at the image-XObject check (gate.py:197-199)
    and never reaches the stream-bytes branch - so it is the image, not the
    absence of content, that earns the OCR verdict.
    """
    page = pypdf.PdfReader(str(scanned_pdf)).pages[0]
    text = page.extract_text() or ""

    signal = classify_page(page, text)

    assert signal.verdict is PageVerdict.OCR
    assert text.strip() == "", "an image-only page must yield no extractable text"
    assert signal.image_xobjects == 1


def test_text_layer_source_is_not_image_only(tmp_path):
    """Control: the rasterization source does carry text, so the two differ."""
    src = tmp_path / "text.pdf"
    src.write_bytes(build_text_pdf(PHRASE))

    page = pypdf.PdfReader(str(src)).pages[0]
    text = page.extract_text() or ""

    assert PHRASE.split()[0] in text
    assert classify_page(page, text).verdict is not PageVerdict.OCR


def test_the_raster_actually_contains_ink(tmp_path):
    """Guards the failure that would make every OCR result vacuously empty.

    If the text never rendered, the fixture would be a blank page and any OCR
    run against it would "succeed" with no text - indistinguishable from a
    broken engine.
    """
    width, height, pixels = rasterize(build_text_pdf(PHRASE), dpi=150)
    assert width > 0 and height > 0
    assert len(pixels) == width * height, "8-bit single channel"

    ink = sum(1 for b in pixels if b < 128)
    assert ink > 500, f"expected rendered glyphs, found {ink} dark pixels"

    blank_w, blank_h, blank = rasterize(build_text_pdf(" "), dpi=150)
    assert all(b == 255 for b in blank), "an empty page must be uniformly white"
    assert (blank_w, blank_h) == (width, height)


def test_scanned_pdf_embeds_a_flate_grayscale_image(scanned_pdf):
    """Structure, not just behaviour: it must be a real image XObject."""
    raw = scanned_pdf.read_bytes()

    assert raw.startswith(b"%PDF-")
    assert raw.rstrip().endswith(b"%%EOF")
    assert b"/Subtype /Image" in raw
    assert b"/ColorSpace /DeviceGray" in raw
    assert b"/Filter /FlateDecode" in raw
    # No text-showing operator anywhere.
    assert b" Tj" not in raw and b" TJ" not in raw


def test_declared_image_length_matches_the_stream(scanned_pdf):
    """A wrong /Length is the classic hand-rolled-PDF bug: readers accept the
    file and then silently decode nothing."""
    raw = scanned_pdf.read_bytes()
    marker = raw.index(b"/Subtype /Image")
    length_at = raw.index(b"/Length ", marker) + len(b"/Length ")
    declared = int(raw[length_at : raw.index(b" ", length_at)])

    stream_at = raw.index(b"stream\n", marker) + len(b"stream\n")
    payload = raw[stream_at : stream_at + declared]

    assert len(payload) == declared
    assert raw[stream_at + declared : stream_at + declared + 10].startswith(b"\nendstream")
    zlib.decompress(payload)  # raises if the slice is wrong


def test_builder_is_deterministic():
    """Same text in, same bytes out - a fixture that drifts is not a fixture."""
    assert build_scanned_pdf(PHRASE) == build_scanned_pdf(PHRASE)
    assert build_scanned_pdf(PHRASE) != build_scanned_pdf("something else")


def test_dpi_changes_the_raster_size():
    small_w, small_h, _ = rasterize(build_text_pdf(PHRASE), dpi=72)
    large_w, large_h, _ = rasterize(build_text_pdf(PHRASE), dpi=200)

    assert large_w > small_w
    assert large_h > small_h
