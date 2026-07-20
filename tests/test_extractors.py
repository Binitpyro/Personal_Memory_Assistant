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
        mock_pypdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Hello world from PDF"
        mock_reader = MagicMock()
        mock_reader.is_encrypted = False
        mock_reader.pages = [mock_page]
        mock_pypdf.PdfReader.return_value = mock_reader
        with patch.dict("sys.modules", {"pypdf": mock_pypdf}):
            result = self.ext.extract(fake_path, MAX_SIZE)
        assert "Hello world" in result
        mock_pypdf.PdfReader.assert_called_once_with(str(fake_path), strict=False)

    def test_encrypted_pdf(self, tmp_path):
        fake_path = tmp_path / "encrypted.pdf"
        fake_path.touch()
        mock_pypdf = MagicMock()
        mock_reader = MagicMock()
        mock_reader.is_encrypted = True
        mock_pypdf.PdfReader.return_value = mock_reader
        with patch.dict("sys.modules", {"pypdf": mock_pypdf}):
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
        mock_docx = MagicMock()
        mock_para = MagicMock()
        mock_para.text = "Paragraph text content"

        mock_paragraph_module = MagicMock()
        mock_paragraph_module.Paragraph.return_value = mock_para
        mock_table_module = MagicMock()

        mock_doc = MagicMock()
        mock_child_p = MagicMock()
        mock_child_p.tag = "}p"
        mock_doc.element.body.iterchildren.return_value = [mock_child_p]
        mock_docx.Document.return_value = mock_doc

        with patch.dict(
            "sys.modules",
            {
                "docx": mock_docx,
                "docx.text.paragraph": mock_paragraph_module,
                "docx.table": mock_table_module,
            },
        ):
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
        assert self.ext.can_handle(tmp_path / "sheet.xls")
        assert not self.ext.can_handle(tmp_path / "sheet.csv")

    def test_extract_success(self, tmp_path):
        fake_path = tmp_path / "test.xlsx"
        fake_path.touch()
        mock_openpyxl = MagicMock()
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
        mock_openpyxl.load_workbook.return_value = mock_wb
        with patch.dict("sys.modules", {"openpyxl": mock_openpyxl}):
            result = self.ext.extract(fake_path, MAX_SIZE)
        assert "Sheet1" in result
        assert "Header" in result
        assert "Row1" in result

    def test_extract_xlsx_size_truncation(self, tmp_path):
        fake_path = tmp_path / "test.xlsx"
        fake_path.touch()
        mock_openpyxl = MagicMock()
        mock_ws = MagicMock()
        mock_ws.title = "Sheet1"
        mock_ws.iter_rows.return_value = iter(
            [
                ("HeaderLongValue1234567890", "Value"),
            ]
        )
        mock_wb = MagicMock()
        mock_wb.worksheets = [mock_ws]
        mock_openpyxl.load_workbook.return_value = mock_wb
        with patch.dict("sys.modules", {"openpyxl": mock_openpyxl}):
            result = self.ext.extract(fake_path, 10)
        # Should truncate or exit loop early when size exceeded
        assert len(result) <= 30

    def test_extract_legacy_xls(self, tmp_path):
        fake_path = tmp_path / "test.xls"
        fake_path.touch()

        mock_xlrd = MagicMock()
        mock_wb = MagicMock()
        mock_sheet = MagicMock()
        mock_sheet.name = "LegacySheet"
        mock_sheet.nrows = 2

        c1 = MagicMock()
        c1.value = "H1"
        c2 = MagicMock()
        c2.value = "V1"
        c3 = MagicMock()
        c3.value = "H2"
        c4 = MagicMock()
        c4.value = "V2"

        mock_sheet.row.side_effect = [[c1, c2], [c3, c4]]
        mock_wb.sheets.return_value = [mock_sheet]
        mock_xlrd.open_workbook.return_value = mock_wb

        with patch.dict("sys.modules", {"xlrd": mock_xlrd}):
            result = self.ext.extract(fake_path, MAX_SIZE)
        assert "LegacySheet" in result
        assert "H1 | V1" in result
        assert "H2 | V2" in result

    def test_extract_legacy_xls_size_truncation(self, tmp_path):
        fake_path = tmp_path / "test.xls"
        fake_path.touch()

        mock_xlrd = MagicMock()
        mock_wb = MagicMock()
        mock_sheet = MagicMock()
        mock_sheet.name = "S"
        mock_sheet.nrows = 10
        c = MagicMock()
        c.value = "SomeLongTextValue"
        mock_sheet.row.return_value = [c]
        mock_wb.sheets.return_value = [mock_sheet]
        mock_xlrd.open_workbook.return_value = mock_wb

        with patch.dict("sys.modules", {"xlrd": mock_xlrd}):
            result = self.ext.extract(fake_path, 5)
        # Should stop processing sheets
        assert len(result) < 50

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
        mock_pptx = MagicMock()
        mock_shape = MagicMock()
        mock_shape.has_text_frame = False  # use .text attribute path
        mock_shape.text = "Slide text content"
        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]
        mock_pptx.Presentation.return_value = mock_prs
        with patch.dict("sys.modules", {"pptx": mock_pptx}):
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
        assert result == ""

    def test_epub_extraction_success(self, tmp_path):
        fake_epub = tmp_path / "test.epub"

        # We can write a real zip file to test real EPUB structure
        import zipfile

        with zipfile.ZipFile(fake_epub, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            # Subfolder OEBPS/ or any folder
            zf.writestr(
                "OEBPS/ch1.xhtml",
                "<html><body><h1>Introduction</h1><p>Welcome to &amp; standard text. This is a longer text paragraph that contains enough characters to pass the fifty character check in the extractor.</p></body></html>",  # noqa: E501
            )
            zf.writestr(
                "OEBPS/ch2.html",
                "<html><body><p>This is second page. It has a lot of additional characters in order to exceed fifty characters as well.</p></body></html>",  # noqa: E501
            )
            # Ignored folder
            zf.writestr("__MACOSX/ch1.xhtml", "ignored")
            # Non-html file suffix
            zf.writestr("OEBPS/image.png", "binarydata")

        result = self.ext.extract(fake_epub, MAX_SIZE)
        assert "Introduction" in result
        assert "Welcome to standard text." in result
        assert "This is second page." in result
        assert "ignored" not in result

    def test_epub_zip_bomb_prevention(self, tmp_path):
        fake_epub = tmp_path / "zip_bomb.epub"
        fake_epub.touch()

        mock_zf = MagicMock()
        mock_zf.__enter__.return_value = mock_zf
        mock_zf.namelist.return_value = ["ch1.xhtml"]
        mock_zf.getinfo.return_value.file_size = 101 * 1024 * 1024

        with patch("zipfile.ZipFile", return_value=mock_zf):
            result = self.ext.extract(fake_epub, MAX_SIZE)
        # Oversized entries are rejected before they are decompressed.
        assert result == ""
        mock_zf.open.assert_not_called()


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
