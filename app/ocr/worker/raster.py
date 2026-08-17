"""PDF page rasterization via pypdfium2. Runs only inside `<ocr_env>`.

MUST NOT IMPORT `app.*`.

pypdfium2 rather than PyMuPDF: PMA is MIT-licensed and PyMuPDF is AGPL. The
repo has already removed one dependency on license grounds.
"""

import contextlib
import math

#: Refuse to allocate more than this many pixels for one page. A page declared
#: 200x200 inches at 300 DPI is 3.6 gigapixels - without this cap a single
#: malformed document takes the worker down with an OOM instead of failing one
#: page and moving on.
#:
#: 20 MP, not the 40 MP this used to be. The cost is 1 byte per pixel for the
#: 2D grayscale array returned to RapidOCR. Resident memory is ~20 MB at 20 MP
#: (or ~8.7 MB for A4 at 300 DPI, ~17.4 MB for A3 at 300 DPI), so standard
#: office documents pass easily; only genuinely outsized scans are refused,
#: and with a clear per-page error rather than an OOM.
_MAX_PIXELS = 20_000_000


class RasterError(Exception):
    """Page could not be rendered. Caller skips the page and continues."""


def open_document(path):
    """Open a PDF once per document.

    PDFium is not thread-safe, but the worker is single-threaded by
    construction, so no external locking is needed here.
    """
    import pypdfium2 as pdfium

    try:
        return pdfium.PdfDocument(path)
    except Exception as exc:
        raise RasterError(f"cannot open PDF: {exc}") from exc


def close_document(doc):
    if doc is None:
        return
    with contextlib.suppress(Exception):
        doc.close()


def render_page(doc, page_num, dpi):
    """Render one page to a grayscale numpy array.

    `scale` is dpi/72 because PDF user-space units are points.
    """

    try:
        page = doc[page_num]
    except Exception as exc:
        raise RasterError(f"no such page {page_num}: {exc}") from exc

    bitmap = None
    try:
        scale = dpi / 72.0
        width, height = page.get_size()
        pixels = math.ceil(width * scale) * math.ceil(height * scale)
        if pixels > _MAX_PIXELS:
            raise RasterError(
                f"page {page_num} too large to raster: {pixels / 1e6:.1f} MP at {dpi} DPI"
            )

        bitmap = page.render(scale=scale, grayscale=True)
        array = bitmap.to_numpy()
        # RapidOCR accepts 2D grayscale arrays directly or handles conversion in C++.
        # Keeping this 2D avoids an unnecessary 3x byte allocation on the Python heap.
        if array.ndim == 3 and array.shape[2] == 1:
            array = array.squeeze(axis=-1)
        return array
    except RasterError:
        raise
    except MemoryError:
        raise
    except Exception as exc:
        raise RasterError(f"render failed on page {page_num}: {exc}") from exc
    finally:
        # Explicit: PDFium bitmaps are native allocations that the GC will not
        # reclaim promptly, and a 300-page scan would otherwise accumulate them.
        if bitmap is not None:
            with contextlib.suppress(Exception):
                bitmap.close()
        with contextlib.suppress(Exception):
            page.close()
