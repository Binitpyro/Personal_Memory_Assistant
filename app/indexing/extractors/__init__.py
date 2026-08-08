from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


# Defined before the submodule imports below: pdf_extractor does
# `from . import ExtractMeta`, so the name has to exist on this partially
# initialized module by the time that import runs.
@dataclass(frozen=True)
class ExtractMeta:
    """Out-of-band summary an extractor may yield once, as its final item.

    Lives here rather than in `app.ocr` because "this file has pages we
    couldn't read" is an extraction-layer fact. Keeping it here means
    `app.indexing` never imports `app.ocr` at module scope.

    `ocr_pages` holds 0-based page indices. An image extractor would emit
    `ExtractMeta(page_count=1, ocr_pages=(0,))` and need no protocol change.
    """

    page_count: int = 0
    ocr_pages: tuple[int, ...] = ()
    native_pages: int = 0
    blank_pages: int = 0
    #: True when the max_file_size cap stopped us before every page was seen.
    truncated: bool = False
    #: "encrypted" | "corrupt" | ""
    reason: str = ""


from .csv_extractor import CsvExtractor  # noqa: E402
from .docx_extractor import DocxExtractor  # noqa: E402
from .epub_extractor import EpubExtractor  # noqa: E402
from .json_extractor import JsonExtractor  # noqa: E402
from .pdf_extractor import PdfExtractor  # noqa: E402
from .pptx_extractor import PptxExtractor  # noqa: E402
from .xlsx_extractor import XlsxExtractor  # noqa: E402


class Extractor(Protocol):
    def can_handle(self, path: Path) -> bool: ...
    def extract(self, path: Path, max_file_size: int) -> str: ...
    # Iterator is covariant, so the six extractors that only ever yield str
    # still satisfy this widened signature without modification.
    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str | ExtractMeta]: ...


EXTRACTORS: list[Extractor] = [
    PdfExtractor(),
    DocxExtractor(),
    XlsxExtractor(),
    PptxExtractor(),
    EpubExtractor(),
    CsvExtractor(),
    JsonExtractor(),
]
