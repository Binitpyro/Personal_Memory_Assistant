import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


class PptxExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pptx"

    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str]:
        """Yield text from slides in a PPTX document."""
        try:
            from pptx import Presentation

            prs = Presentation(str(path))
            total = 0
            for i, slide in enumerate(prs.slides):
                yield f"--- Slide {i + 1} ---"
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text:
                        yield shape.text
                        total += len(shape.text)
                if total > max_file_size:
                    break
        except Exception as e:
            logger.warning("Failed to extract PPTX %s: %s", path, e)

    def extract(self, path: Path, max_file_size: int) -> str:
        """Legacy extraction for backward compatibility."""
        return "\n".join(self.extract_stream(path, max_file_size))[:max_file_size]
