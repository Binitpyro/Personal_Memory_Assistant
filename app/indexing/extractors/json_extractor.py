import json
import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


class JsonExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".json"

    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str]:
        """Yield text from a JSON file, prettifying if small."""
        try:
            # P10-3: Only attempt to parse if file is small. Truncated large JSON is invalid.
            if path.stat().st_size > 500_000:
                with open(path, encoding="utf-8-sig", errors="replace") as f:
                    chunk = f.read(max_file_size)
                    yield chunk
                return

            with open(path, encoding="utf-8-sig", errors="replace") as f:
                text = f.read(max_file_size)
            try:
                # Prettify for better RAG context
                yield json.dumps(json.loads(text), indent=2, ensure_ascii=False)[:200000]
            except Exception:
                yield text[:200000]
        except Exception as e:
            logger.warning("Failed to extract JSON %s: %s", path, e)

    def extract(self, path: Path, max_file_size: int) -> str:
        """Legacy extraction for backward compatibility."""
        return "".join(self.extract_stream(path, max_file_size))
