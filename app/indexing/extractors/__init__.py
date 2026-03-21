from typing import Protocol, List
from pathlib import Path

class Extractor(Protocol):
    def can_handle(self, path: Path) -> bool:
        ...
    def extract(self, path: Path, max_file_size: int) -> str:
        ...

from .pdf_extractor import PdfExtractor
from .docx_extractor import DocxExtractor
from .xlsx_extractor import XlsxExtractor
from .pptx_extractor import PptxExtractor
from .epub_extractor import EpubExtractor
from .csv_extractor import CsvExtractor
from .json_extractor import JsonExtractor

EXTRACTORS: List[Extractor] = [
    PdfExtractor(),
    DocxExtractor(),
    XlsxExtractor(),
    PptxExtractor(),
    EpubExtractor(),
    CsvExtractor(),
    JsonExtractor()
]
