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

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_NAMED_ENTITY_RE = re.compile(r"&[a-zA-Z]+;")
_NUMERIC_ENTITY_RE = re.compile(r"&#\d+;")
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_ENTRY_READ_BYTES = 10_000_000
_MAX_CUMULATIVE_DECOMPRESSED_SIZE = 100 * 1024 * 1024


class EpubExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".epub"

    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str]:
        """Yield text from an EPUB by streaming its internal XHTML documents."""
        try:
            total_chars = 0
            cumulative_decompressed_bytes = 0

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
                        entry_info = zf.getinfo(file_name)
                        if (
                            cumulative_decompressed_bytes + entry_info.file_size
                            > _MAX_CUMULATIVE_DECOMPRESSED_SIZE
                        ):
                            logger.warning(
                                "EPUB extraction stopped: cumulative decompressed size limit "
                                "(%d bytes) would be exceeded for %s",
                                _MAX_CUMULATIVE_DECOMPRESSED_SIZE,
                                path,
                            )
                            raise ValueError("Decompression limit exceeded (potential ZIP bomb)")

                        # Read at most 10 MB from a safe entry.
                        with zf.open(entry_info) as f:
                            raw_bytes = f.read(min(entry_info.file_size, _MAX_ENTRY_READ_BYTES))
                            cumulative_decompressed_bytes += len(raw_bytes)
                            if cumulative_decompressed_bytes > _MAX_CUMULATIVE_DECOMPRESSED_SIZE:
                                logger.warning(
                                    "EPUB extraction stopped: cumulative decompressed size limit (%d bytes) exceeded for %s",
                                    _MAX_CUMULATIVE_DECOMPRESSED_SIZE,
                                    path,
                                )
                                raise ValueError(
                                    "Decompression limit exceeded (potential ZIP bomb)"
                                )

                            raw_html = raw_bytes.decode("utf-8", errors="ignore")

                            # Strip XML/HTML tags and collapse whitespace
                            text = _HTML_TAG_RE.sub(" ", raw_html)
                            text = _NAMED_ENTITY_RE.sub(" ", text)
                            text = _NUMERIC_ENTITY_RE.sub(" ", text)
                            text = _WHITESPACE_RE.sub(" ", text).strip()

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
