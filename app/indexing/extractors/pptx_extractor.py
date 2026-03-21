from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class PptxExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pptx"

    def extract(self, path: Path, max_file_size: int) -> str:
        try:
            from pptx import Presentation
            prs = Presentation(str(path))
            content, total = [], 0
            for i, slide in enumerate(prs.slides):
                content.append(f"--- Slide {i+1} ---")
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text:
                        content.append(shape.text)
                        total += len(shape.text)
                if total > max_file_size: break
            return "\n".join(content)[:max_file_size]
        except Exception as e: 
            logger.warning("Failed to extract PPTX %s: %s", path, e)
            return ""
