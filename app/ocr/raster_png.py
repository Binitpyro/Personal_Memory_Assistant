"""Rasterize a PDF page to PNG bytes, in the main process.

The Tier 1/2 path renders inside the OCR worker venv (`ocr/worker/raster.py`)
and hands back a numpy array for the ONNX engine. Tier 3 has no venv of its
own - it sends the page to a vision model over HTTP - so it needs a renderer
here, and it needs PNG bytes rather than an array because that is what both
provider dialects carry.

Deliberately a separate module from `ocr/worker/raster.py` rather than an import
of it: that file documents "MUST NOT IMPORT app.*" because it is copied into a
foreign venv, and reaching into it from the main process would couple the two
lifetimes together.
"""

from __future__ import annotations

import contextlib
import io
import math
from pathlib import Path

#: Same ceiling as the worker's renderer. A page declared 200x200 inches at
#: 300 DPI is 3.6 gigapixels; without a cap one malformed document is an OOM.
#:
#: This one matters more than the worker's copy, because it runs in the *main*
#: process: colour rather than grayscale, through to_pil(), then a PNG encode
#: into a BytesIO. Measured peak RSS for a single page at the old 40 MP ceiling
#: was 322 MB. Held at 20 MP to match the worker - A4 and A3 at 300 DPI both
#: still pass.
_MAX_PIXELS = 20_000_000


class RasterError(Exception):
    """Page could not be rendered. The caller skips it and continues."""


@contextlib.contextmanager
def open_pdf(path: str | Path):
    """Open a document once for a multi-page render pass.

    `render_page_png` opens, renders and closes on every call, which is right
    for a one-off but meant a 50-page Tier 3 document paid 50 full PDF parses -
    the VLM loop called it once per page. Callers rendering several pages of
    the same file should hold this open and use `render_page_from`.

    Not safe for *concurrent* use: pdfium handles tolerate being touched from
    different threads but not at the same time. The VLM loop awaits each page
    before starting the next, so it never overlaps.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RasterError(f"pypdfium2 is not available: {exc}") from exc

    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise RasterError(f"cannot open PDF: {exc}") from exc

    try:
        yield doc
    finally:
        with contextlib.suppress(Exception):
            doc.close()


def render_page_from(doc, page_num: int, dpi: int) -> bytes:
    """Render one page of an already-open document to PNG bytes.

    Closes the page and bitmap it allocates, never the document - that belongs
    to `open_pdf`.
    """
    page = None
    bitmap = None
    try:
        try:
            page = doc[page_num]
        except Exception as exc:
            raise RasterError(f"no such page {page_num}: {exc}") from exc

        scale = dpi / 72.0  # PDF user-space units are points
        width, height = page.get_size()
        pixels = math.ceil(width * scale) * math.ceil(height * scale)
        if pixels > _MAX_PIXELS:
            raise RasterError(
                f"page {page_num} too large to raster: {pixels / 1e6:.1f} MP at {dpi} DPI"
            )

        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except RasterError:
        raise
    except MemoryError:
        raise
    except Exception as exc:
        raise RasterError(f"render failed on page {page_num}: {exc}") from exc
    finally:
        # PDFium allocations are native and the GC will not reclaim them
        # promptly; a long document would otherwise accumulate them.
        for handle in (bitmap, page):
            if handle is not None:
                with contextlib.suppress(Exception):
                    handle.close()


def render_page_png(path: str | Path, page_num: int, dpi: int) -> bytes:
    """Render one page of `path` to PNG bytes, opening and closing the document.

    Rendered in colour, unlike the OCR path's grayscale: a vision model is
    reading a picture rather than feeding a binarizer, and colour carries
    information (highlighting, coloured stamps, ink vs preprint) that grayscale
    throws away.
    """
    with open_pdf(path) as doc:
        return render_page_from(doc, page_num, dpi)
