from pathlib import Path
import logging
import re

logger = logging.getLogger(__name__)

class EpubExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".epub"

    def extract(self, path: Path, max_file_size: int) -> str:
        try:
            import ebooklib
            from ebooklib import epub
            book = epub.read_epub(str(path))
            content, total = [], 0
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    raw_html = item.get_content().decode('utf-8', errors='ignore')
                    text = re.sub(r'<[^>]+>', ' ', raw_html)
                    text = re.sub(r'\s+', ' ', text).strip()
                    if text:
                        content.append(text)
                        total += len(text)
                if total > max_file_size: break
            return "\n".join(content)[:max_file_size]
        except Exception as e: 
            logger.warning("Failed to extract EPUB %s: %s", path, e)
            return ""
