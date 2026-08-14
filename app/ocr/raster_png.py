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
_MAX_PIXELS = 40_000_000


class RasterError(Exception):
    """Page could not be rendered. The caller skips it and continues."""


def render_page_png(path: str | Path, page_num: int, dpi: int) -> bytes:
    """Render one page of `path` to PNG bytes.

    Rendered in colour, unlike the OCR path's grayscale: a vision model is
    reading a picture rather than feeding a binarizer, and colour carries
    information (highlighting, coloured stamps, ink vs preprint) that grayscale
    throws away.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RasterError(f"pypdfium2 is not available: {exc}") from exc

    doc = None
    page = None
    bitmap = None
    try:
        try:
            doc = pdfium.PdfDocument(str(path))
        except Exception as exc:
            raise RasterError(f"cannot open PDF: {exc}") from exc

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
        for handle in (bitmap, page, doc):
            if handle is not None:
                with contextlib.suppress(Exception):
                    handle.close()
