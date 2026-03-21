from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class XlsxExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in {".xlsx", ".xls"}

    def extract(self, path: Path, max_file_size: int) -> str:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            content, total = [], 0
            for sheet in wb.worksheets:
                content.append(f"--- Sheet: {sheet.title} ---")
                for row in sheet.iter_rows(values_only=True):
                    row_data = [str(cell) for cell in row if cell is not None]
                    if row_data:
                        line = " | ".join(row_data)
                        content.append(line)
                        total += len(line)
                    if total > max_file_size: break
                if total > max_file_size: break
            return "\n".join(content)[:max_file_size]
        except Exception as e: 
            logger.warning("Failed to extract XLSX %s: %s", path, e)
            return ""
