import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


class XlsxExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in {".xlsx", ".xls"}

    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str]:
        """Yield text from sheets and rows in an XLSX document."""
        try:
            import openpyxl  # type: ignore

            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            try:
                total = 0
                for sheet in wb.worksheets:
                    yield f"--- Sheet: {sheet.title} ---"
                    for row in sheet.iter_rows(values_only=True):
                        row_data = [str(cell) for cell in row if cell is not None]
                        if row_data:
                            line = " | ".join(row_data)
                            yield line
                            total += len(line)
                        if total > max_file_size:
                            return
            finally:
                wb.close()
        except Exception as e:
            logger.warning("Failed to extract XLSX %s: %s", path, e)

    def extract(self, path: Path, max_file_size: int) -> str:
        """Legacy extraction for backward compatibility."""
        return "\n".join(self.extract_stream(path, max_file_size))[:max_file_size]
