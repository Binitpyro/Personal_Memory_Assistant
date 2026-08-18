"""Build image-only ("scanned") PDFs with known ground-truth text.

Why this exists: nothing in the tree could exercise the OCR path.
`generate_perf_corpus.py` writes text-layer PDFs, so every page classifies
NATIVE at the detection gate (`app/ocr/gate.py`) and is never queued. There was
no scanned or image-only fixture anywhere under `tests/` either, which is why
OCR quality could not be measured and why an image-quality prescan could not be
evaluated.

Approach, and the constraint it works under: **no new dependency**. The text is
laid out in a hand-written text-layer PDF, rasterized with pypdfium2 (already
pinned at 4.30.0 for the Tier 3 renderer), and the resulting grayscale raster is
re-embedded as a `/DeviceGray` image XObject in a second PDF that contains no
text operators at all. zlib is stdlib. Pillow is deliberately avoided - the
raster comes back through `to_numpy()`, not `to_pil()`.

The ground truth is therefore exact: the caller passes the text in, and that is
precisely what a correct OCR run must return.
"""

from __future__ import annotations

import zlib
from pathlib import Path

#: US Letter in PDF points, matching the text-layer builder in
#: `app/ocr/registry.py:_smoke_test_pdf`.
_PAGE_W_PT = 612
_PAGE_H_PT = 792


def _pdf_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_text_pdf(text: str, *, font_size: int = 24) -> bytes:
    """A minimal one-page text-layer PDF. The rasterization source."""
    lines = [ln for ln in text.splitlines() if ln.strip()] or [text]
    parts = [
        "BT",
        f"/F1 {font_size} Tf",
        f"1 0 0 1 60 {_PAGE_H_PT - 90} Tm",
        f"{int(font_size * 1.6)} TL",
    ]
    for line in lines:
        parts.append(f"({_pdf_escape(line)}) Tj T*")
    parts.append("ET")
    stream = "\n".join(parts).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W_PT} {_PAGE_H_PT}] "
            f"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ).encode(),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    return _assemble(objects)


def _assemble(objects: list[bytes]) -> bytes:
    """Serialize numbered objects with a correct xref table."""
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def rasterize(pdf_bytes: bytes, dpi: int = 150) -> tuple[int, int, bytes]:
    """Render page 0 to 8-bit grayscale. Returns (width, height, raw pixels).

    `to_numpy()` rather than `to_pil()`: Pillow is not a declared dependency and
    this must not add one. A grayscale pypdfium2 bitmap comes back as (H, W, 1),
    so the trailing axis is squeezed - the same shape handling as
    `app/ocr/worker/raster.py`.
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page = doc[0]
        bitmap = page.render(scale=dpi / 72.0, grayscale=True)
        array = bitmap.to_numpy()
        if array.ndim == 3:
            array = array[:, :, 0]
        height, width = array.shape[:2]
        return width, height, array.tobytes()
    finally:
        doc.close()


def build_scanned_pdf(text: str, *, dpi: int = 150, font_size: int = 24) -> bytes:
    """An image-only PDF whose picture reads `text`.

    Contains no text-showing operators, so `gate.classify_page` sees zero
    extracted characters and one image XObject - the OCR verdict.
    """
    width, height, pixels = rasterize(build_text_pdf(text, font_size=font_size), dpi=dpi)
    compressed = zlib.compress(pixels, 6)

    content = f"q {_PAGE_W_PT} 0 0 {_PAGE_H_PT} 0 0 cm /Im0 Do Q".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W_PT} {_PAGE_H_PT}] "
            f"/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>"
        ).encode(),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
            f"/Length {len(compressed)} >>"
        ).encode()
        + b"\nstream\n"
        + compressed
        + b"\nendstream",
    ]
    return _assemble(objects)


def write_scanned_pdf(dest: Path, text: str, *, dpi: int = 150, font_size: int = 24) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(build_scanned_pdf(text, dpi=dpi, font_size=font_size))
    return dest


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    import sys

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "scanned_sample.pdf")
    write_scanned_pdf(out, "PMA OCR 12345\nSecond line of the scan")
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
