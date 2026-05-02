"""
app/indexing/folder_profiler.py
P3-1: Extracted from service.py — folder profile analysis and project type detection.

Responsibilities:
- Detect project type from file markers (extensions, filenames, directories)
- Build rich folder profile dicts for retrieval signal
- Resolve folder overlap (parent-child deduplication)
"""

import asyncio
import concurrent.futures
import contextlib
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Any

from app.project_constants import (
    KEY_EXTS,
    KEY_NAMES,
    PROJECT_SIGNATURES,
)

logger = logging.getLogger(__name__)


# ── Project Type Detection ───────────────────────────────────────────────────


def _indicator_matches(
    kind: str,
    pattern: str,
    extensions: set[str],
    filenames: set[str],
    directories: set[str],
) -> bool:
    if kind == "ext":
        return pattern.lower() in extensions
    if kind == "file":
        return pattern.lower() in filenames
    if kind == "dir":
        return pattern in directories
    return False


def _collect_project_markers(
    files: list[tuple[Path, str]],
    folder: Path,
) -> tuple[set[str], set[str], set[str]]:
    extensions: set[str] = set()
    filenames: set[str] = set()
    directories: set[str] = set()

    for file_path, _ in files:
        extensions.add(file_path.suffix.lower())
        filenames.add(file_path.name.lower())
        _add_relative_directory(file_path, folder, directories)

    _add_direct_child_directories(folder, directories)
    return extensions, filenames, directories


def _add_relative_directory(file_path: Path, folder: Path, directories: set[str]) -> None:
    try:
        rel = file_path.relative_to(folder)
    except ValueError:
        return
    if len(rel.parts) > 1:
        directories.add(rel.parts[0])


def _add_direct_child_directories(folder: Path, directories: set[str]) -> None:
    try:
        for entry in folder.iterdir():
            if entry.is_dir():
                directories.add(entry.name)
    except OSError:
        return


def _dominant_extension_project_type(
    files: list[tuple[Path, str]],
) -> tuple[str, str] | None:
    ext_counts = Counter(file_path.suffix.lower() for file_path, _ in files if file_path.suffix)
    dominant = ext_counts.most_common(1)
    if not dominant:
        return None
    extension = dominant[0][0]
    return f"{extension} files", f"Collection of {extension} files"


def detect_project_type(
    files: list[tuple[Path, str]],
    folder: Path,
) -> tuple[str, str]:
    """Infer the project type from file extensions, filenames, and directories."""
    extensions, filenames, directories = _collect_project_markers(files, folder)

    for proj_type, desc, indicators in PROJECT_SIGNATURES:
        if all(
            _indicator_matches(kind, pattern, extensions, filenames, directories)
            for kind, pattern in indicators
        ):
            return proj_type, desc

    dominant_type = _dominant_extension_project_type(files)
    if dominant_type:
        return dominant_type

    return "unknown", "General file collection"


# ── Folder Profile Builder ───────────────────────────────────────────────────


def build_folder_profile(
    folder: Path,
    folder_tag: str,
    files: list[tuple[Path, str]],
) -> dict[str, Any]:
    """Analyse an indexed folder and produce a rich profile dict."""
    folder_files = [(fp, ft) for fp, ft in files if str(fp).startswith(str(folder))]

    ext_counts: Counter = Counter()
    total_size = 0
    key_files_list: list[str] = []

    for fp, _ in folder_files:
        ext = fp.suffix.lower()
        ext_counts[ext] += 1
        if fp.name.lower() in KEY_NAMES or ext in KEY_EXTS:
            key_files_list.append(fp.name)

    for fp, _ in folder_files:
        with contextlib.suppress(OSError):
            total_size += fp.stat().st_size

    project_type, description = detect_project_type(folder_files, folder)

    top_exts = ", ".join(f"{ext} ({cnt})" for ext, cnt in ext_counts.most_common(8))

    profile_lines = [
        f"Folder: {folder.name}",
        f"Project type: {project_type} — {description}",
        f"Files: {len(folder_files)} ({top_exts or 'various'})",
        f"Size: {total_size // 1024} KB",
    ]
    if key_files_list:
        profile_lines.append(f"Key files: {', '.join(key_files_list[:15])}")

    return {
        "folder_path": str(folder),
        "folder_tag": folder_tag,
        "profile_text": " ".join(profile_lines),
        "project_type": project_type,
        "file_count": len(folder_files),
        "total_size_bytes": total_size,
        "top_extensions": top_exts,
        "key_files": ", ".join(key_files_list[:15]),
    }


async def generate_folder_profiles_async(
    all_files: list[tuple[Path, str]],
    folders: list[Path],
) -> list[dict[str, Any]]:
    """Run build_folder_profile concurrently for all folders."""
    loop = asyncio.get_running_loop()
    max_workers = min(len(folders), (os.cpu_count() or 4) + 2)
    profiles: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [
            loop.run_in_executor(pool, build_folder_profile, f, f.name, all_files) for f in folders
        ]
        results = await asyncio.gather(*futs, return_exceptions=True)

    for folder, res in zip(folders, results, strict=False):
        if isinstance(res, Exception):
            logger.warning("Folder profile failed for %s: %s", folder, res)
            continue
        profiles.append(res)

    return profiles


# ── Folder Overlap Resolution ────────────────────────────────────────────────


def resolve_folder_overlaps(folders: list[str]) -> list[Path]:
    """Deduplicate folders so no child folder is indexed twice when a parent is present."""
    resolved: list[Path] = []
    for raw in folders:
        clean = raw.strip().strip('"').strip("'")
        p = Path(clean).resolve()
        if not p.exists() or not p.is_dir():
            continue
        resolved.append(p)

    resolved.sort(key=lambda p: len(p.parts))

    kept: list[Path] = []
    for candidate in resolved:
        dominated = False
        for parent in kept:
            try:
                candidate.relative_to(parent)
                dominated = True
                logger.info(
                    "Folder overlap detected: '%s' is inside already-queued '%s' - skipping.",
                    candidate,
                    parent,
                )
                break
            except ValueError:
                pass
        if not dominated:
            kept.append(candidate)
    return kept
