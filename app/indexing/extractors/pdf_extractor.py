import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


class PdfExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str]:
        """Yield text page-by-page from the PDF."""
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(str(path))
            total = 0
            if reader.is_encrypted:
                yield (
                    f"[ENCRYPTED PDF: {path.name}] "
                    "Cannot extract text from password-protected file."
                )
                return

            for page in reader.pages:
                txt = page.extract_text()
                if txt:
                    yield txt
                    total += len(txt)
                    if total > max_file_size:
                        break
        except Exception as e:
            logger.warning("Failed to extract PDF %s: %s", path, e)

    def extract(self, path: Path, max_file_size: int) -> str:
        """Legacy extraction for backward compatibility."""
        return "\n".join(self.extract_stream(path, max_file_size))[:max_file_size]
