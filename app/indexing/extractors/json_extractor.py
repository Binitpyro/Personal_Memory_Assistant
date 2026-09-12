import json
import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

# Matches the block size `IndexingService._extract_plain_text_stream` reads with.
# The exact size is not load-bearing; that no single fragment is the size of the
# whole document is.
_READ_BLOCK_CHARS = 128 * 1024


class JsonExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".json"

    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str]:
        """Yield text from a JSON file, prettifying if small."""
        try:
            # P10-3: Only attempt to parse if file is small. Truncated large JSON is invalid.
            if path.stat().st_size > 500_000:
                # Yielded in fixed blocks rather than as one read(max_file_size).
                # StreamChunker holds a whole fragment in its buffer, so a
                # fragment the size of the document made peak memory a function
                # of the largest file in the corpus rather than of the tunables -
                # which is what CLAUDE.md section 6's boundedness invariant
                # forbids. The concatenation is unchanged, so `extract` is too.
                with open(path, encoding="utf-8-sig", errors="replace") as f:
                    remaining = max_file_size
                    while remaining > 0:
                        block = f.read(min(_READ_BLOCK_CHARS, remaining))
                        if not block:
                            break
                        remaining -= len(block)
                        yield block
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
