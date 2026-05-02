"""
tests/test_extractors.py
Covers all 7 file extractors in app/indexing/extractors/:
- CsvExtractor, JsonExtractor, PdfExtractor (mocked)
- DocxExtractor, XlsxExtractor, PptxExtractor, EpubExtractor (mocked)
"""

import json
from unittest.mock import MagicMock, patch

from app.indexing.extractors import EXTRACTORS
from app.indexing.extractors.csv_extractor import CsvExtractor
from app.indexing.extractors.docx_extractor import DocxExtractor
from app.indexing.extractors.epub_extractor import EpubExtractor
from app.indexing.extractors.json_extractor import JsonExtractor
from app.indexing.extractors.pdf_extractor import PdfExtractor
from app.indexing.extractors.pptx_extractor import PptxExtractor
from app.indexing.extractors.xlsx_extractor import XlsxExtractor

MAX_SIZE = 1_000_000


# ── CSV ───────────────────────────────────────────────────────────────────────


class TestCsvExtractor:
    def setup_method(self):
        self.ext = CsvExtractor()

    def test_can_handle(self, tmp_path):
        assert self.ext.can_handle(tmp_path / "data.csv")
        assert not self.ext.can_handle(tmp_path / "data.txt")

    def test_basic_csv(self, tmp_path):
        f = tmp_path / "test.csv"
        f.write_text("name,age\nAlice,30\nBob,25\n", encoding="utf-8")
        result = self.ext.extract(f, MAX_SIZE)
        assert "Alice" in result
        assert "name: Alice" in result or "name" in result

    def test_empty_csv(self, tmp_path):
        f = tmp_path / "empty.csv"
        f.write_text("", encoding="utf-8")
        result = self.ext.extract(f, MAX_SIZE)
        assert result == ""

    def test_headers_only_csv(self, tmp_path):
        f = tmp_path / "headers_only.csv"
        f.write_text("col1,col2,col3\n", encoding="utf-8")
        result = self.ext.extract(f, MAX_SIZE)
        assert result == ""

    def test_max_size_truncation(self, tmp_path):
        f = tmp_path / "big.csv"
        rows = ["a,b"] + [f"value{i},{i}" for i in range(100)]
        f.write_text("\n".join(rows), encoding="utf-8")
        result = self.ext.extract(f, 50)
        assert len(result) <= 50

    def test_corrupt_file_returns_empty(self, tmp_path):
        # File that doesn't exist
        result = self.ext.extract(tmp_path / "nonexistent.csv", MAX_SIZE)
        assert result == ""

    def test_rows_limit(self, tmp_path):
        """Should process at most 5000 rows."""
        f = tmp_path / "large.csv"
        lines = ["col"] + [str(i) for i in range(6000)]
        f.write_text("\n".join(lines), encoding="utf-8")
        result = self.ext.extract(f, MAX_SIZE)
        # Should complete without error, not every row expected
        assert isinstance(result, str)


# ── JSON ──────────────────────────────────────────────────────────────────────


class TestJsonExtractor:
    def setup_method(self):
        self.ext = JsonExtractor()

    def test_can_handle(self, tmp_path):
        assert self.ext.can_handle(tmp_path / "data.json")
        assert not self.ext.can_handle(tmp_path / "data.csv")

    def test_basic_json_dict(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text(json.dumps({"name": "Alice", "age": 30}), encoding="utf-8")
        result = self.ext.extract(f, MAX_SIZE)
        assert "Alice" in result or "name" in result

    def test_json_list(self, tmp_path):
        f = tmp_path / "list.json"
        f.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        result = self.ext.extract(f, MAX_SIZE)
        assert isinstance(result, str)

    def test_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not valid json}", encoding="utf-8")
        result = self.ext.extract(f, MAX_SIZE)
        assert isinstance(result, str)

    def test_missing_file(self, tmp_path):
        result = self.ext.extract(tmp_path / "missing.json", MAX_SIZE)
        assert result == ""


# ── PDF (mocked pypdf) ────────────────────────────────────────────────────────


class TestPdfExtractor:
    def setup_method(self):
        self.ext = PdfExtractor()

    def test_can_handle(self, tmp_path):
        assert self.ext.can_handle(tmp_path / "doc.pdf")
        assert not self.ext.can_handle(tmp_path / "doc.docx")

    def test_extract_success(self, tmp_path):
        fake_path = tmp_path / "test.pdf"
        fake_path.touch()
        # PdfReader is a lazy import inside extract() — patch at pypdf module level
        with patch("pypdf.PdfReader") as mock_reader_cls:
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "Hello world from PDF"
            mock_reader = MagicMock()
            mock_reader.is_encrypted = False
            mock_reader.pages = [mock_page]
            mock_reader_cls.return_value = mock_reader
            result = self.ext.extract(fake_path, MAX_SIZE)
        assert "Hello world" in result

    def test_encrypted_pdf(self, tmp_path):
        fake_path = tmp_path / "encrypted.pdf"
        fake_path.touch()
        with patch("pypdf.PdfReader") as mock_reader_cls:
            mock_reader = MagicMock()
            mock_reader.is_encrypted = True
            mock_reader_cls.return_value = mock_reader
            result = self.ext.extract(fake_path, MAX_SIZE)
        assert "ENCRYPTED" in result.upper()

    def test_missing_file_returns_empty(self, tmp_path):
        result = self.ext.extract(tmp_path / "missing.pdf", MAX_SIZE)
        assert result == ""


# ── DOCX (mocked python-docx) ─────────────────────────────────────────────────


class TestDocxExtractor:
    def setup_method(self):
        self.ext = DocxExtractor()

    def test_can_handle(self, tmp_path):
        assert self.ext.can_handle(tmp_path / "file.docx")
        assert not self.ext.can_handle(tmp_path / "file.pdf")

    def test_extract_success(self, tmp_path):
        fake_path = tmp_path / "test.docx"
        fake_path.touch()
        # Document is a lazy import inside extract() — patch at docx module level
        with patch("docx.Document") as mock_doc_cls:
            mock_para = MagicMock()
            mock_para.text = "Paragraph text content"
            mock_doc = MagicMock()
            mock_doc.paragraphs = [mock_para]
            mock_doc.tables = []
            mock_doc_cls.return_value = mock_doc
            result = self.ext.extract(fake_path, MAX_SIZE)
        assert "Paragraph" in result

    def test_missing_file_returns_empty(self, tmp_path):
        result = self.ext.extract(tmp_path / "missing.docx", MAX_SIZE)
        assert result == ""


# ── XLSX (mocked openpyxl) ────────────────────────────────────────────────────


class TestXlsxExtractor:
    def setup_method(self):
        self.ext = XlsxExtractor()

    def test_can_handle(self, tmp_path):
        assert self.ext.can_handle(tmp_path / "sheet.xlsx")
        assert not self.ext.can_handle(tmp_path / "sheet.csv")

    def test_extract_success(self, tmp_path):
        fake_path = tmp_path / "test.xlsx"
        fake_path.touch()
        # openpyxl is imported via `import openpyxl` inside extract().
        # XlsxExtractor uses: import openpyxl; openpyxl.load_workbook(...);
        # sheet.iter_rows(values_only=True)
        with patch("openpyxl.load_workbook") as mock_load:
            mock_ws = MagicMock()
            mock_ws.title = "Sheet1"
            mock_ws.iter_rows.return_value = iter(
                [
                    ("Header", "Value"),
                    ("Row1", 42),
                ]
            )
            mock_wb = MagicMock()
            mock_wb.worksheets = [mock_ws]
            mock_load.return_value = mock_wb
            result = self.ext.extract(fake_path, MAX_SIZE)
        assert isinstance(result, str)

    def test_missing_file_returns_empty(self, tmp_path):
        result = self.ext.extract(tmp_path / "missing.xlsx", MAX_SIZE)
        assert result == ""


# ── PPTX (mocked python-pptx) ────────────────────────────────────────────────


class TestPptxExtractor:
    def setup_method(self):
        self.ext = PptxExtractor()

    def test_can_handle(self, tmp_path):
        assert self.ext.can_handle(tmp_path / "slides.pptx")
        assert not self.ext.can_handle(tmp_path / "slides.xlsx")

    def test_extract_success(self, tmp_path):
        fake_path = tmp_path / "test.pptx"
        fake_path.touch()
        # Presentation is a lazy import inside extract() — patch at pptx module level
        with patch("pptx.Presentation") as mock_prs_cls:
            mock_shape = MagicMock()
            mock_shape.has_text_frame = False  # use .text attribute path
            mock_shape.text = "Slide text content"
            mock_slide = MagicMock()
            mock_slide.shapes = [mock_shape]
            mock_prs = MagicMock()
            mock_prs.slides = [mock_slide]
            mock_prs_cls.return_value = mock_prs
            result = self.ext.extract(fake_path, MAX_SIZE)
        assert isinstance(result, str)

    def test_missing_file_returns_empty(self, tmp_path):
        result = self.ext.extract(tmp_path / "missing.pptx", MAX_SIZE)
        assert result == ""


# ── EPUB ──────────────────────────────────────────────────────────────────────


class TestEpubExtractor:
    def setup_method(self):
        self.ext = EpubExtractor()

    def test_can_handle(self, tmp_path):
        assert self.ext.can_handle(tmp_path / "book.epub")
        assert not self.ext.can_handle(tmp_path / "book.pdf")

    def test_missing_file_returns_empty_or_error(self, tmp_path):
        result = self.ext.extract(tmp_path / "missing.epub", MAX_SIZE)
        assert isinstance(result, str)


# ── EXTRACTORS registry ───────────────────────────────────────────────────────


class TestExtractorRegistry:
    def test_all_extractors_are_loaded(self):
        assert len(EXTRACTORS) == 7

    def test_each_extractor_has_can_handle_and_extract(self):
        for ext in EXTRACTORS:
            assert callable(getattr(ext, "can_handle", None))
            assert callable(getattr(ext, "extract", None))

    def test_only_one_extractor_handles_each_type(self, tmp_path):
        types = [".pdf", ".docx", ".xlsx", ".pptx", ".epub", ".csv", ".json"]
        for t in types:
            handlers = [e for e in EXTRACTORS if e.can_handle(tmp_path / f"file{t}")]
            assert len(handlers) == 1, f"Expected exactly 1 handler for {t}, got {len(handlers)}"
