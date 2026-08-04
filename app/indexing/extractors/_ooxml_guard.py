"""Zip-bomb pre-flight guard for OOXML formats (DOCX, PPTX).

Unlike EPUB, python-docx/python-pptx own the archive reads (`Document()`,
`Presentation()` decompress the whole package eagerly) so there is no
per-entry read to intercept as content is streamed. This module instead
inspects the zip's central directory before the library ever opens it.

Limitation: this checks the central directory's *declared* sizes only.
zipfile does not enforce declared size against what a member actually
decompresses to, so a crafted central directory that understates
`file_size` defeats this check. It is the same class of protection as
EPUB's pre-check (app/indexing/extractors/epub_extractor.py), not a
complete one - python-docx/python-pptx don't expose a way to bound reads
mid-decompression the way EPUB's own zip-entry reads do.
"""

import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_CUMULATIVE_DECOMPRESSED_SIZE = 100 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 200


def check_ooxml_archive(path: Path) -> None:
    """Raise ValueError if *path* looks like a zip bomb.

    Returns silently when the archive cannot be inspected - the OOXML
    library (python-docx / python-pptx) will surface the real error on
    its own when it tries to open the file.
    """
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            total_declared = 0
            total_compressed = 0
            for info in zf.infolist():
                total_declared += info.file_size
                total_compressed += info.compress_size
    except (zipfile.BadZipFile, OSError, KeyError):
        return

    if total_declared > _MAX_CUMULATIVE_DECOMPRESSED_SIZE:
        logger.warning(
            "OOXML extraction stopped: cumulative decompressed size limit "
            "(%d bytes) would be exceeded for %s",
            _MAX_CUMULATIVE_DECOMPRESSED_SIZE,
            path,
        )
        raise ValueError("Decompression limit exceeded (potential ZIP bomb)")

    if total_declared > total_compressed * _MAX_COMPRESSION_RATIO:
        logger.warning(
            "OOXML extraction stopped: compression ratio limit (%dx) exceeded for %s",
            _MAX_COMPRESSION_RATIO,
            path,
        )
        raise ValueError("Decompression limit exceeded (potential ZIP bomb)")
