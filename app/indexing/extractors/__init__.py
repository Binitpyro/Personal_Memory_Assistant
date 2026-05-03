from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from .csv_extractor import CsvExtractor
from .docx_extractor import DocxExtractor
from .epub_extractor import EpubExtractor
from .json_extractor import JsonExtractor
from .pdf_extractor import PdfExtractor
from .pptx_extractor import PptxExtractor
from .xlsx_extractor import XlsxExtractor


class Extractor(Protocol):
    def can_handle(self, path: Path) -> bool: ...
    def extract(self, path: Path, max_file_size: int) -> str: ...
    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str]: ...


EXTRACTORS: list[Extractor] = [
    PdfExtractor(),
    DocxExtractor(),
    XlsxExtractor(),
    PptxExtractor(),
    EpubExtractor(),
    CsvExtractor(),
    JsonExtractor(),
]
