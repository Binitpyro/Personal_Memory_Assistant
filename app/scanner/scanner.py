"""
Cross-platform file scanner module.
Provides file scanning using different methods based on the platform.
"""

import logging
import os
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Outcome of a folder scan."""

    files: list[Path] = field(default_factory=list)
    method: str = "scandir"  # "ntfs_mft" | "scandir"
    duration_ms: float = 0.0
    total_mft_entries: int = 0  # only set for MFT scans


def scan_folder(folder: Path, extensions: set[str]) -> ScanResult:
    """
    Scan *folder* for files matching *extensions*.

    Automatically picks the fastest available backend.
    """
    t0 = time.perf_counter()

    if platform.system() == "Windows":
        try:
            # pylint: disable=import-outside-toplevel
            from app.scanner.ntfs_mft import NTFSScanner

            scanner = NTFSScanner()
            paths = scanner.scan_folder(folder, extensions)
            if paths is not None:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                logger.info(
                    "NTFS MFT scan: %d files in %.0f ms (%s total MFT entries)",
                    len(paths),
                    elapsed_ms,
                    f"{scanner.entry_count:,}",
                )
                return ScanResult(
                    files=paths,
                    method="ntfs_mft",
                    duration_ms=elapsed_ms,
                    total_mft_entries=scanner.entry_count,
                )
        except (ImportError, OSError) as e:
            logger.debug("NTFS MFT scan unavailable: %s", e)

    files = _scandir_walk(folder, extensions)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info("scandir walk: %d files in %.0f ms", len(files), elapsed_ms)
    return ScanResult(files=files, method="scandir", duration_ms=elapsed_ms)


def _scandir_walk(folder: Path, extensions: set[str]) -> list[Path]:
    """Recursive file enumeration using ``os.scandir`` (faster than rglob).

    Uses an iterative stack instead of recursion to avoid hitting
    Python's default recursion limit on deeply nested directories.
    """
    results: list[Path] = []
    stack: list[str] = [str(folder)]
    while stack:
        current = stack.pop()
        try:
            entries = _list_dir_entries(current, extensions)
            for is_file, path_str in entries:
                if is_file:
                    results.append(Path(path_str))
                else:
                    stack.append(path_str)
        except OSError as e:
            logger.debug("Skipping %s: %s", current, e)
    return results


def _list_dir_entries(directory: str, extensions: set[str]) -> list[tuple]:
    """List directory entries as (is_file, path) tuples.

    Returns files matching *extensions* and sub-directories for further
    traversal.  Each entry that raises ``OSError`` is silently skipped.
    """
    entries: list[tuple] = []
    with os.scandir(directory) as it:
        for entry in it:
            try:
                if entry.is_dir(follow_symlinks=False):
                    entries.append((False, entry.path))
                elif entry.is_file(follow_symlinks=False):
                    ext = os.path.splitext(entry.name)[1].lower()
                    if not extensions or ext in extensions:
                        entries.append((True, entry.path))
            except OSError:
                continue
    return entries
