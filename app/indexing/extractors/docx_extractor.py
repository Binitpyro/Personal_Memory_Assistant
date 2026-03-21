from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class DocxExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".docx"

    def extract(self, path: Path, max_file_size: int) -> str:
        try:
            from docx import Document
            doc = Document(str(path))
            content, total = [], 0
            for p in doc.paragraphs:
                if p.text.strip():
                    content.append(p.text)
                    total += len(p.text)
                    if total > max_file_size: break
            # Support basic table extraction
            if total <= max_file_size:
                for table in doc.tables:
                    for row in table.rows:
                        row_data = [cell.text for cell in row.cells if cell.text.strip()]
                        if row_data:
                            line = " | ".join(row_data)
                            content.append(line)
                            total += len(line)
                        if total > max_file_size: break
                    if total > max_file_size: break
            return "\n".join(content)[:max_file_size]
        except Exception as e:
            err_msg = str(e).lower()
            if "encrypted" in err_msg or "password" in err_msg:
                return f"[ENCRYPTED DOCX: {path.name}] Cannot extract text from password-protected file."
            logger.warning("Failed to extract DOCX %s: %s", path, e)
            return ""
