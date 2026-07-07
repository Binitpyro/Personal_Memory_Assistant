"""
EPUB text extractor using zip-based parsing (no AGPL dependencies).

Instead of EbookLib (AGPL-3.0), we parse EPUB files directly as ZIP archives
containing XHTML/HTML content, which is the EPUB format spec. This is
permissive-license-safe and works for all standard EPUBs (2.x and 3.x).
"""

import logging
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


class EpubExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".epub"

    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str]:
        """Yield text from an EPUB by streaming its internal XHTML documents."""
        try:
            total_chars = 0
            cumulative_decompressed_bytes = 0
            MAX_CUMULATIVE_DECOMPRESSED_SIZE = 100 * 1024 * 1024  # 100MB

            with zipfile.ZipFile(str(path), "r") as zf:
                content_files = sorted(
                    name
                    for name in zf.namelist()
                    if name.lower().endswith((".xhtml", ".html", ".htm", ".xml"))
                    and not name.lower().startswith("__macosx")
                )

                for file_name in content_files:
                    if total_chars >= max_file_size:
                        break

                    try:
                        # P10-2: Protect against ZIP bombs by reading in chunks
                        with zf.open(file_name) as f:
                            # Read internal member in up to 10MB chunks for safety
                            raw_bytes = f.read(10_000_000)
                            cumulative_decompressed_bytes += len(raw_bytes)
                            if cumulative_decompressed_bytes > MAX_CUMULATIVE_DECOMPRESSED_SIZE:
                                logger.warning(
                                    "EPUB extraction stopped: cumulative decompressed size limit (%d bytes) exceeded for %s",
                                    MAX_CUMULATIVE_DECOMPRESSED_SIZE, path
                                )
                                raise ValueError("Decompression limit exceeded (potential ZIP bomb)")

                            raw_html = raw_bytes.decode("utf-8", errors="ignore")

                            # Strip XML/HTML tags and collapse whitespace
                            text = re.sub(r"<[^>]+>", " ", raw_html)
                            text = re.sub(r"&[a-zA-Z]+;", " ", text)
                            text = re.sub(r"&#\d+;", " ", text)
                            text = re.sub(r"\s+", " ", text).strip()

                            if len(text) > 50:
                                yield text
                                total_chars += len(text)
                    except ValueError as ve:
                        raise ve
                    except Exception as inner_e:
                        logger.debug("Skipping EPUB entry %s: %s", file_name, inner_e)
                        continue
        except ValueError as ve:
            logger.warning("EPUB extraction aborted: %s", ve)
        except Exception as e:
            logger.warning("Failed to extract EPUB %s: %s", path, e)

    def extract(self, path: Path, max_file_size: int) -> str:
        """Legacy extraction for backward compatibility."""
        return "\n".join(self.extract_stream(path, max_file_size))[:max_file_size]
