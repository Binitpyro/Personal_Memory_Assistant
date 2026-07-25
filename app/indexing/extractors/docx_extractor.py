import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


class DocxExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".docx"

    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str]:
        """Yield text from paragraphs and tables in a DOCX document."""
        try:
            from docx import Document

            doc = Document(str(path))
            total = 0

            from docx.table import Table
            from docx.text.paragraph import Paragraph

            for child in doc.element.body.iterchildren():
                if child.tag.endswith("}p"):
                    p = Paragraph(child, doc)
                    txt = p.text.strip()
                    if txt:
                        yield txt
                        total += len(txt)
                        if total > max_file_size:
                            return
                elif child.tag.endswith("}tbl"):
                    t = Table(child, doc)
                    for row in t.rows:
                        row_data = [cell.text for cell in row.cells if cell.text.strip()]
                        if row_data:
                            line = " | ".join(row_data)
                            yield line
                            total += len(line)
                            if total > max_file_size:
                                return
        except Exception as e:
            err_msg = str(e).lower()
            if "encrypted" in err_msg or "password" in err_msg:
                yield (
                    f"[ENCRYPTED DOCX: {path.name}] "
                    "Cannot extract text from password-protected file."
                )
                return
            logger.warning("Failed to extract DOCX %s: %s", path, e)

    def extract(self, path: Path, max_file_size: int) -> str:
        """Legacy extraction for backward compatibility."""
        return "\n".join(self.extract_stream(path, max_file_size))[:max_file_size]
