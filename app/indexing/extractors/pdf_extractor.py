from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class PdfExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def extract(self, path: Path, max_file_size: int) -> str:
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            content, total = [], 0
            if reader.is_encrypted:
                return f"[ENCRYPTED PDF: {path.name}] Cannot extract text from password-protected file."
            for page in reader.pages:
                txt = page.extract_text()
                if txt:
                    content.append(txt)
                    total += len(txt)
                    if total > max_file_size: break
            return "\n".join(content)[:max_file_size]
        except Exception as e:
            logger.warning("Failed to extract PDF %s: %s", path, e)
            return ""
