import os
import logging
import asyncio
import threading
import concurrent.futures
import hashlib
from collections import Counter
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Set, Tuple
from app.storage.db import DatabaseManager
from app.embeddings.service import EmbeddingService
from app.vector_store.chroma_client import ChromaClient
from app.scanner.scanner import scan_folder as fast_scan
from app.config import settings

logger = logging.getLogger(__name__)

UNREAL_PROJECT_EXT = ".uproject"
UNITY_SCENE_EXT = ".unity"
NODE_PACKAGE_FILE = "package.json"
PYTHON_PROJECT_LABEL = "Python project"

# ── Project-type detection rules ────────────────────────────────────
# Each rule: (project_type, description_template,
#             required_indicators: set of (kind, pattern) tuples)
#   kind = "ext" (file extension present) | "file" (filename present)
#          | "dir" (directory present)
_PROJECT_SIGNATURES: List[Tuple[str, str, List[Tuple[str, str]]]] = [
    ("Unreal Engine", "Unreal Engine game/application project",
     [("ext", UNREAL_PROJECT_EXT)]),
    ("Unreal Engine (assets only)", "Unreal Engine asset folder (Content)",
     [("ext", ".uasset")]),
    ("Unity", "Unity game/application project",
     [("dir", "Assets"), ("ext", UNITY_SCENE_EXT)]),
    ("Unity", "Unity game/application project",
     [("ext", UNITY_SCENE_EXT)]),
    ("Godot", "Godot engine project",
     [("file", "project.godot")]),
    ("React", "React web application",
     [("file", NODE_PACKAGE_FILE), ("dir", "src")]),
    ("Node.js", "Node.js / JavaScript project",
     [("file", NODE_PACKAGE_FILE)]),
    ("Python", PYTHON_PROJECT_LABEL,
     [("file", "pyproject.toml")]),
    ("Python", PYTHON_PROJECT_LABEL,
     [("file", "setup.py")]),
    ("Python", PYTHON_PROJECT_LABEL,
     [("file", "requirements.txt")]),
    ("Rust", "Rust project",
     [("file", "Cargo.toml")]),
    ("Go", "Go project",
     [("file", "go.mod")]),
    ("Java/Maven", "Java Maven project",
     [("file", "pom.xml")]),
    ("Java/Gradle", "Java Gradle project",
     [("file", "build.gradle")]),
    (".NET/C#", ".NET / C# project",
     [("ext", ".csproj")]),
    ("C/C++", "C/C++ project",
     [("file", "CMakeLists.txt")]),
    ("C/C++", "C/C++ project",
     [("file", "Makefile")]),
    ("LaTeX", "LaTeX document project",
     [("ext", ".tex")]),
]


def _indicator_matches(
    kind: str,
    pattern: str,
    extensions: Set[str],
    filenames: Set[str],
    directories: Set[str],
) -> bool:
    if kind == "ext":
        return pattern.lower() in extensions
    if kind == "file":
        return pattern.lower() in filenames
    if kind == "dir":
        return pattern in directories
    return False


def _detect_project_type(
    files: List[Tuple[Path, str]],
    folder: Path,
) -> Tuple[str, str]:
    """Infer the project type from file extensions, filenames, and directories.

    Returns (project_type, description).
    """
    extensions, filenames, directories = _collect_project_markers(files, folder)

    for proj_type, desc, indicators in _PROJECT_SIGNATURES:
        if all(
            _indicator_matches(kind, pattern, extensions, filenames, directories)
            for kind, pattern in indicators
        ):
            return proj_type, desc

    dominant_type = _dominant_extension_project_type(files)
    if dominant_type:
        return dominant_type

    return "unknown", "General file collection"


def _collect_project_markers(
    files: List[Tuple[Path, str]],
    folder: Path,
) -> Tuple[Set[str], Set[str], Set[str]]:
    extensions: Set[str] = set()
    filenames: Set[str] = set()
    directories: Set[str] = set()

    for file_path, _ in files:
        extensions.add(file_path.suffix.lower())
        filenames.add(file_path.name.lower())
        _add_relative_directory(file_path, folder, directories)

    _add_direct_child_directories(folder, directories)
    return extensions, filenames, directories


def _add_relative_directory(file_path: Path, folder: Path, directories: Set[str]) -> None:
    try:
        rel = file_path.relative_to(folder)
    except ValueError:
        return

    if len(rel.parts) > 1:
        directories.add(rel.parts[0])


def _add_direct_child_directories(folder: Path, directories: Set[str]) -> None:
    try:
        for entry in folder.iterdir():
            if entry.is_dir():
                directories.add(entry.name)
    except OSError:
        return


def _dominant_extension_project_type(
    files: List[Tuple[Path, str]],
) -> Optional[Tuple[str, str]]:
    ext_counts = Counter(file_path.suffix.lower() for file_path, _ in files if file_path.suffix)
    dominant = ext_counts.most_common(1)
    if not dominant:
        return None
    extension = dominant[0][0]
    return f"{extension} files", f"Collection of {extension} files"


def _build_folder_profile(
    folder: Path,
    folder_tag: str,
    files: List[Tuple[Path, str]],
) -> Dict[str, Any]:
    """Analyse an indexed folder and produce a rich profile dict."""
    # Filter files belonging to this folder
    folder_files = [(fp, ft) for fp, ft in files if str(fp).startswith(str(folder))]

    ext_counts: Counter = Counter()
    total_size = 0
    key_files_list: List[str] = []

    # Key config/project files to highlight
    _KEY_NAMES = {
        "readme.md", "readme.txt", "readme",
        "package.json", "pyproject.toml", "setup.py", "requirements.txt",
        "cargo.toml", "go.mod", "pom.xml", "build.gradle",
        "cmakelists.txt", "makefile", ".gitignore",
        "dockerfile", "docker-compose.yml",
    }
    # Key extensions for project files
    _KEY_EXTS = {UNREAL_PROJECT_EXT, ".sln", ".csproj", UNITY_SCENE_EXT}

    for fp, _ in folder_files:
        ext = fp.suffix.lower()
        ext_counts[ext] += 1
        if fp.name.lower() in _KEY_NAMES or ext in _KEY_EXTS:
            key_files_list.append(fp.name)

    # Batch stat() — tolerate individual failures instead of per-file try/except
    total_size = 0
    for fp, _ in folder_files:
        try:
            total_size += fp.stat().st_size
        except OSError:
            pass

    project_type, description = _detect_project_type(folder_files, folder)

    top_exts = ", ".join(
        f"{ext} ({cnt})" for ext, cnt in ext_counts.most_common(8)
    )

    # Build human-readable profile text (will be embedded for search)
    profile_lines = [
        f"Project: {folder_tag}",
        f"Type: {project_type} — {description}",
        f"Location: {folder}",
        f"Contains {len(folder_files)} files totalling "
        f"{round(total_size / (1024 * 1024), 2)} MB.",
        f"Main file types: {top_exts}.",
    ]
    if key_files_list:
        profile_lines.append(f"Key files: {', '.join(key_files_list[:15])}.")

    # Add top-level directory structure
    try:
        subdirs = sorted(
            d.name for d in folder.iterdir() if d.is_dir() and not d.name.startswith(".")
        )[:15]
        if subdirs:
            profile_lines.append(f"Top-level folders: {', '.join(subdirs)}.")
    except OSError:
        pass

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

def _resolve_folder_overlaps(folders: List[str]) -> List[Path]:
    """Remove child folders whose content is already covered by a parent.

    Given ["C:/A", "C:/A/B", "D:/X"], returns [Path("C:/A"), Path("D:/X")]
    because scanning C:/A already recurses into C:/A/B.
    """
    resolved: List[Path] = []
    for raw in folders:
        clean = raw.strip().strip('"').strip("'")
        p = Path(clean).resolve()
        if not p.exists() or not p.is_dir():
            continue
        resolved.append(p)

    resolved.sort(key=lambda p: len(p.parts))

    kept: List[Path] = []
    for candidate in resolved:
        dominated = False
        for parent in kept:
            try:
                candidate.relative_to(parent)
                dominated = True
                logger.info(
                    "Folder overlap detected: '%s' is inside already-queued '%s' — skipping.",
                    candidate, parent,
                )
                break
            except ValueError:
                pass
        if not dominated:
            kept.append(candidate)
    return kept

class IndexingProgress:
    """Thread-safe progress tracker for the indexing pipeline."""

    def __init__(self):
        self._lock = threading.Lock()
        self.total_files = 0
        self.processed_files = 0
        self.total_chunks = 0
        self.skipped_files = 0
        self.new_files = 0
        self.changed_files = 0
        self.status = "idle"
        self.scan_method = ""
        self.scan_duration_ms = 0.0
        self.current_file = ""

    def reset(self, total_files: int):
        with self._lock:
            self.total_files = total_files
            self.processed_files = 0
            self.total_chunks = 0
            self.skipped_files = 0
            self.new_files = 0
            self.changed_files = 0
            self.status = "running"
            self.scan_method = ""
            self.scan_duration_ms = 0.0
            self.current_file = ""

    def update(self, chunks_added: int, current_file: str = ""):
        with self._lock:
            self.processed_files += 1
            self.total_chunks += chunks_added
            self.current_file = current_file

    def complete(self):
        with self._lock:
            self.status = "idle"
            self.current_file = ""

progress = IndexingProgress()
indexing_lock = asyncio.Lock()

class IndexingService:
    def __init__(
        self, 
        db: DatabaseManager, 
        embedding_service: EmbeddingService, 
        chroma_client: ChromaClient
    ):
        self.db = db
        self.embedding_service = embedding_service
        self.chroma_client = chroma_client
        self.supported_extensions = settings.extensions_set
        self.chunk_size = settings.chunk_size
        self.chunk_overlap = settings.chunk_overlap
        self.max_file_size = settings.max_file_size_bytes
        self._concurrency = settings.index_concurrency

    async def index_folders(self, folders: List[str]):
        """Recursively scans and indexes the provided folders.

        Optimisation pipeline:
        1. **Folder overlap removal** – child folders already covered by a
           parent in the request are dropped so the scanner never visits them
           twice.
        2. **Batch change detection** – after scanning, every discovered file
           path is checked against the DB in a single query.  Files whose
           ``modified_at`` timestamp matches the stored value are skipped
           entirely (no stat, no text extraction, no embedding).
        3. Only *new* and *changed* files proceed to the full index pipeline.
        """
        if indexing_lock.locked():
            logger.warning("Indexing is already in progress. Skipping duplicate request.")
            return

        async with indexing_lock:
            unique_folders = _resolve_folder_overlaps(folders)
            if not unique_folders:
                logger.warning("No valid folders to index after overlap resolution.")
                progress.status = "idle"
                return

            logger.info(
                "Starting indexing for %d folder(s) (after overlap pruning): %s",
                len(unique_folders),
                [str(f) for f in unique_folders],
            )

            # Set status to 'running' before scan so SSE stream stays open
            progress.reset(0)
            progress.status = "running"
            progress.current_file = "Scanning folders…"

            loop = asyncio.get_running_loop()
            all_files, scan_method, scan_duration = await loop.run_in_executor(
                None, self._scan_all_folders, unique_folders
            )

            if not all_files:
                logger.info(
                    "No files with extensions %s found in %s.",
                    self.supported_extensions,
                    [str(f) for f in unique_folders],
                )
                progress.status = "idle"
                return

            files_to_index, skipped, new_count, changed_count = await self._detect_changes(
                all_files
            )

            progress.reset(len(files_to_index))
            progress.scan_method = scan_method
            progress.scan_duration_ms = scan_duration
            progress.skipped_files = skipped
            progress.new_files = new_count
            progress.changed_files = changed_count

            if not files_to_index:
                logger.info("All files are up-to-date — nothing to index.")
                # Still (re)generate folder profiles even when nothing new to index
                await self._generate_folder_profiles(all_files, unique_folders)
                progress.complete()
                return

            await self._batch_index_pipeline(files_to_index)

            # Generate folder profiles after indexing so the DB has all files
            await self._generate_folder_profiles(all_files, unique_folders)

            # Phase 3.1: Invalidate Retrieval Cache
            from app.search.retrieval import clear_retrieval_cache
            clear_retrieval_cache()

            progress.complete()
            logger.info(
                "Indexing completed: %d files processed (%d skipped).",
                len(files_to_index),
                skipped,
            )

    # ------------------------------------------------------------------ #
    #  Three-phase batch pipeline (extract → embed → store)              #
    # ------------------------------------------------------------------ #

    async def _batch_index_pipeline(
        self, files_to_index: List[Tuple[Path, str]]
    ) -> None:
        """Optimised pipeline that batches work across *all* files.

        Phase 1 – **Parallel text extraction + chunking** (I/O-bound,
                  runs in a ThreadPoolExecutor so PDF/DOCX parsing
                  doesn't block the event loop).
        Phase 2 – **Batch embedding** of every chunk + summary in one
                  call to ``SentenceTransformer.encode``.  This is the
                  single biggest speed-up: the model processes a large
                  matrix in one GPU/CPU pass instead of many small ones.
        Phase 3 – **Batch storage** into SQLite + ChromaDB.
        """
        total = len(files_to_index)

        # ── Phase 1: parallel extract + chunk ──────────────────────────
        logger.info("Pipeline phase 1/3: extracting text from %d files …", total)
        max_workers = min(self._concurrency * 2, (os.cpu_count() or 4) * 2 + 2)  # Phase 5.3: ~80% more parallelism
        loop = asyncio.get_running_loop()

        prepared: List[Dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [
                loop.run_in_executor(pool, self._extract_and_prepare, fp, ft)
                for fp, ft in files_to_index
            ]
            results = await asyncio.gather(*futs, return_exceptions=True)

        prepared = self._collect_prepared_items(files_to_index, results)

        if not prepared:
            return

        # ── Phase 2: batch embed ALL texts at once ─────────────────────
        all_texts, text_map = self._build_embedding_payload(prepared)

        logger.info(
            "Pipeline phase 2/3: embedding %d texts in batch …", len(all_texts)
        )
        all_embeddings = await self.embedding_service.embed_texts(
            all_texts, batch_size=settings.embedding_batch_size
        )

        self._assign_embeddings(prepared, text_map, all_embeddings)

        # ── Phase 3: batch store in DB + ChromaDB ──────────────────────
        logger.info("Pipeline phase 3/3: storing %d files …", len(prepared))
        await self._store_prepared_items(prepared)

    def _collect_prepared_items(
        self,
        files_to_index: List[Tuple[Path, str]],
        results: List[Any],
    ) -> List[Dict[str, Any]]:
        prepared: List[Dict[str, Any]] = []
        for (file_path, _), result in zip(files_to_index, results):
            if isinstance(result, Exception):
                logger.error("Error extracting %s: %s", file_path, result)
                progress.update(0, file_path.name)
                continue
            if result is None:
                progress.update(0, file_path.name)
                continue
            prepared.append(result)
        return prepared

    @staticmethod
    def _build_embedding_payload(
        prepared: List[Dict[str, Any]],
    ) -> Tuple[List[str], List[Tuple[int, str, int]]]:
        all_texts: List[str] = []
        text_map: List[Tuple[int, str, int]] = []

        for file_idx, item in enumerate(prepared):
            for chunk_idx, chunk in enumerate(item["chunks"]):
                all_texts.append(chunk["text_preview"])
                text_map.append((file_idx, "chunk", chunk_idx))
            if item.get("summary"):
                all_texts.append(item["summary"])
                text_map.append((file_idx, "summary", 0))

        return all_texts, text_map

    @staticmethod
    def _assign_embeddings(
        prepared: List[Dict[str, Any]],
        text_map: List[Tuple[int, str, int]],
        all_embeddings: List[List[float]],
    ) -> None:
        for idx, (file_idx, kind, sub_idx) in enumerate(text_map):
            if kind == "chunk":
                prepared[file_idx]["chunks"][sub_idx]["_embedding"] = all_embeddings[idx]
                continue
            prepared[file_idx]["_summary_embedding"] = all_embeddings[idx]

    async def _store_prepared_items(self, prepared: List[Dict[str, Any]]) -> None:
        """Process and store prepared items in micro-batches to bound memory usage."""
        # ── Pre-batch: look up ALL existing files in one DB query ──────
        all_paths = [item["file_data"]["path"] for item in prepared]
        existing_map = await self.db.get_existing_file_ids(all_paths)

        # ── Process in micro-batches for progress + bounded memory ─────
        STORE_BATCH = 100 # Reduced from 200 for even tighter memory bounds
        for batch_start in range(0, len(prepared), STORE_BATCH):
            all_chroma_ids: List[str] = []
            all_chroma_embs: List[List[float]] = []
            all_chroma_metas: List[Dict[str, Any]] = []
            summary_items: List[Dict[str, Any]] = []
            
            batch = prepared[batch_start : batch_start + STORE_BATCH]
            for item in batch:
                await self._store_single_prepared_item(
                    item,
                    all_chroma_ids,
                    all_chroma_embs,
                    all_chroma_metas,
                    summary_items,
                    existing_map,
                )
            
            # Commit SQLite batch
            await self.db.commit()
            
            # Flush ChromaDB batch immediately to reclaim memory
            await self._flush_chroma_batches(
                all_chroma_ids,
                all_chroma_embs,
                all_chroma_metas,
                summary_items,
            )

    async def _store_single_prepared_item(
        self,
        item: Dict[str, Any],
        all_chroma_ids: List[str],
        all_chroma_embs: List[List[float]],
        all_chroma_metas: List[Dict[str, Any]],
        summary_items: List[Dict[str, Any]],
        existing_map: Optional[Dict[str, int]] = None,
    ) -> None:
        try:
            file_data = item["file_data"]
            file_path = file_data["path"]
            folder_tag = item["folder_tag"]

            # Use pre-fetched map instead of per-file DB query
            existing_id = (existing_map or {}).get(file_path)
            if existing_id is not None:
                await self._delete_existing_chunks(existing_id)

            try:
                file_id = await self.db.insert_file(file_data, auto_commit=False)
            except TypeError:
                file_id = await self.db.insert_file(file_data)
            chunk_ids_int = await self.db.insert_chunks_bulk(
                self._build_chunk_rows(item["chunks"], file_id)
            )

            self._append_chunk_vectors(
                item,
                chunk_ids_int,
                file_path,
                folder_tag,
                all_chroma_ids,
                all_chroma_embs,
                all_chroma_metas,
            )

            self._append_summary_item(item, file_id, file_path, folder_tag, summary_items)
            progress.update(len(item["chunks"]), item["path"].name)
        except Exception as e:
            logger.error("Error storing file %s: %s", item["path"], e)
            progress.update(0, item["path"].name)

    async def _delete_existing_chunks(self, file_id: int) -> None:
        old_chunks = await self.db.get_file_chunks(file_id)
        old_ids = [str(chunk["id"]) for chunk in old_chunks]
        if old_ids:
            await self.chroma_client.delete_documents(old_ids)
        try:
            await self.db.delete_file_chunks(file_id, auto_commit=False)
        except TypeError:
            await self.db.delete_file_chunks(file_id)

    @staticmethod
    def _build_chunk_rows(chunks: List[Dict[str, Any]], file_id: int) -> List[Dict[str, Any]]:
        chunk_rows = [{k: v for k, v in chunk.items() if k != "_embedding"} for chunk in chunks]
        for chunk_row in chunk_rows:
            chunk_row["file_id"] = file_id
        return chunk_rows

    @staticmethod
    def _append_chunk_vectors(
        item: Dict[str, Any],
        chunk_ids_int: List[int],
        file_path: str,
        folder_tag: str,
        all_chroma_ids: List[str],
        all_chroma_embs: List[List[float]],
        all_chroma_metas: List[Dict[str, Any]],
    ) -> None:
        for chunk_id_int, chunk in zip(chunk_ids_int, item["chunks"]):
            chunk_id = str(chunk_id_int)
            all_chroma_ids.append(chunk_id)
            all_chroma_embs.append(chunk["_embedding"])
            all_chroma_metas.append({
                "chunk_id": chunk_id,
                "file_path": file_path,
                "folder_tag": folder_tag,
            })

    @staticmethod
    def _append_summary_item(
        item: Dict[str, Any],
        file_id: int,
        file_path: str,
        folder_tag: str,
        summary_items: List[Dict[str, Any]],
    ) -> None:
        if not item.get("_summary_embedding"):
            return
        summary_items.append({
            "doc_id": f"file_{file_id}",
            "embedding": item["_summary_embedding"],
            "metadata": {
                "file_id": file_id,
                "file_path": file_path,
                "folder_tag": folder_tag,
            },
        })

    async def _flush_chroma_batches(
        self,
        all_chroma_ids: List[str],
        all_chroma_embs: List[List[float]],
        all_chroma_metas: List[Dict[str, Any]],
        summary_items: List[Dict[str, Any]],
    ) -> None:
        """Flush chunk & summary embeddings to ChromaDB.

        Uses asyncio.gather so chunk batches and summary upsert
        run concurrently when possible.
        """
        tasks = []
        chroma_batch = 5000
        for i in range(0, len(all_chroma_ids), chroma_batch):
            end = min(i + chroma_batch, len(all_chroma_ids))
            tasks.append(
                self.chroma_client.add_documents(
                    all_chroma_ids[i:end],
                    all_chroma_embs[i:end],
                    all_chroma_metas[i:end],
                )
            )

        if summary_items:
            tasks.append(self.chroma_client.add_summaries_batch(summary_items))

        if tasks:
            await asyncio.gather(*tasks)

    async def _generate_folder_profiles(
        self,
        all_files: List[Tuple[Path, str]],
        folders: List[Path],
    ) -> None:
        """Build and store profiles for all indexed folders **in parallel**.

        Profile construction (CPU + I/O) runs concurrently in threads,
        then all DB upserts happen in a single transaction.
        """
        logger.info("Generating folder profiles for %d folder(s) …", len(folders))
        profile_texts: List[str] = []
        profiles: List[Dict[str, Any]] = []

        loop = asyncio.get_running_loop()

        # Build all profiles in parallel threads
        max_workers = min(len(folders), (os.cpu_count() or 4) + 2)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [
                loop.run_in_executor(
                    pool, _build_folder_profile, folder, folder.name, all_files
                )
                for folder in folders
            ]
            results = await asyncio.gather(*futs, return_exceptions=True)

        for folder, result in zip(folders, results):
            if isinstance(result, (Exception, BaseException)):
                logger.error("Error generating profile for %s: %s", folder, result)
                continue
            profile: Dict[str, Any] = result
            profiles.append(profile)
            profile_texts.append(profile["profile_text"])

        # Batch DB upserts (single transaction)
        for profile in profiles:
            try:
                await self.db.upsert_folder_profile(profile, auto_commit=False)
            except Exception as e:
                logger.error("Error storing profile for %s: %s", profile["folder_path"], e)
        await self.db.commit()

        # Embed all profile texts and store in the summary collection
        if profile_texts:
            try:
                embeddings = await self.embedding_service.embed_texts(profile_texts)
                summary_items = []
                for prof, emb in zip(profiles, embeddings):
                    summary_items.append({
                        "doc_id": f"folder_profile_{prof['folder_tag']}",
                        "embedding": emb,
                        "metadata": {
                            "file_path": prof["folder_path"],
                            "folder_tag": prof["folder_tag"],
                            "project_type": prof["project_type"],
                            "is_folder_profile": "true",
                        },
                    })
                await self.chroma_client.add_summaries_batch(summary_items)
                logger.info(
                    "Stored %d folder profile(s): %s",
                    len(profiles),
                    [(p["folder_tag"], p["project_type"]) for p in profiles],
                )
            except Exception as e:
                logger.error("Error embedding folder profiles: %s", e)

    def _extract_and_prepare(
        self, path: Path, folder_tag: str
    ) -> Optional[Dict[str, Any]]:
        """Synchronous helper: extract text → chunk → build metadata.

        Designed to run inside a ThreadPoolExecutor.
        """
        try:
            stat = path.stat()
            if stat.st_size > self.max_file_size:
                logger.warning("Skipping oversized file (%d MB): %s",
                               stat.st_size // (1024 * 1024), path)
                return None

            text = self._extract_text(path)
            if not text:
                return None

            summary = self._generate_summary(text, path)
            chunks = self._create_chunks(text, file_path=str(path))
            if not chunks:
                return None

            file_data = {
                "path": str(path.absolute()),
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "type": path.suffix.lower(),
                "folder_tag": folder_tag,
                "summary": summary,
                "sha256": self._calculate_sha256(path),
            }

            return {
                "path": path,
                "folder_tag": folder_tag,
                "file_data": file_data,
                "chunks": chunks,
                "summary": summary,
            }
        except Exception as e:
            logger.error("Error preparing %s: %s", path, e)
            return None

    def _scan_all_folders(
        self, unique_folders: List[Path]
    ) -> Tuple[List[Tuple[Path, str]], str, float]:
        """Scan all folders **in parallel** and return deduplicated files.

        Each folder is scanned in its own thread so I/O-heavy MFT / scandir
        operations overlap instead of running serially.
        """
        all_files: List[Tuple[Path, str]] = []
        seen_paths: Set[str] = set()
        scan_method = ""
        scan_duration = 0.0

        def _scan_one(folder: Path):
            return folder, fast_scan(folder, self.supported_extensions)

        max_workers = min(len(unique_folders), (os.cpu_count() or 4) + 2)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_scan_one, p) for p in unique_folders]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    folder, scan_result = fut.result()
                except Exception as exc:
                    logger.error("Scan failed for a folder: %s", exc)
                    continue
                scan_method = scan_result.method
                scan_duration += scan_result.duration_ms

                for file_path in scan_result.files:
                    abs_key = str(file_path.resolve())
                    if abs_key not in seen_paths:
                        seen_paths.add(abs_key)
                        all_files.append((file_path, folder.name))

        return all_files, scan_method, scan_duration

    async def _detect_changes(
        self, all_files: List[Tuple[Path, str]]
    ) -> Tuple[List[Tuple[Path, str]], int, int, int]:
        """Compare scanned files against the DB and classify as new/changed/unchanged.

        stat() calls are offloaded to a thread-pool so they run in parallel
        instead of blocking the event loop one-by-one.

        Returns (files_to_index, skipped_count, new_count, changed_count).
        """
        file_paths_list = [str(fp.absolute()) for fp, _ in all_files]
        change_map = await self.db.get_files_change_map(file_paths_list)

        # ── Parallel stat() in thread-pool ────────────────────────────
        loop = asyncio.get_running_loop()

        def _get_file_info(file_path: Path) -> Tuple[Optional[str], int]:
            """Return (isoformat mtime, size) or (None, 0) on error."""
            try:
                stat = file_path.stat()
                return datetime.fromtimestamp(stat.st_mtime).isoformat(), stat.st_size
            except OSError:
                return None, 0

        max_workers = min(len(all_files), (os.cpu_count() or 4) * 4, 64)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            file_infos = await asyncio.gather(
                *[loop.run_in_executor(pool, _get_file_info, fp) for fp, _ in all_files]
            )

        # ── Classify ──────────────────────────────────────────────────
        files_to_index: List[Tuple[Path, str]] = []
        skipped = 0
        new_count = 0
        changed_count = 0

        for (file_path, folder_tag), (current_mtime, current_size) in zip(all_files, file_infos):
            status = await self._process_file_change(
                file_path=file_path,
                folder_tag=folder_tag,
                current_mtime=current_mtime,
                current_size=current_size,
                change_map=change_map,
                loop=loop,
            )
            
            if status == "skipped":
                skipped += 1
            elif status == "new":
                new_count += 1
                files_to_index.append((file_path, folder_tag))
            elif status == "changed":
                changed_count += 1
                files_to_index.append((file_path, folder_tag))

        logger.info(
            "Change detection: %d scanned → %d to index "
            "(%d new, %d changed, %d unchanged/skipped).",
            len(all_files),
            len(files_to_index),
            new_count,
            changed_count,
            skipped,
        )
        return files_to_index, skipped, new_count, changed_count

    def _calculate_sha256(self, path: Path) -> str:
        """Calculate SHA256 hash of a file's content."""
        hasher = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1_048_576), b""):  # 1 MB reads for SSD throughput
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            logger.warning("Could not calculate hash for %s: %s", path, e)
            return ""

    async def _process_file_change(
        self,
        file_path: Path,
        folder_tag: str,
        current_mtime: Optional[str],
        current_size: int,
        change_map: Dict[str, Any],
        loop: asyncio.AbstractEventLoop,
    ) -> str:
        if current_mtime is None:
            return "skipped"

        abs_path = str(file_path.absolute())
        stored_entry = change_map.get(abs_path)
        stored_mtime = stored_entry[0] if stored_entry else None
        stored_sha = stored_entry[1] if stored_entry else None

        if stored_mtime is not None and stored_mtime == current_mtime:
            return "skipped"
        
        if stored_mtime is not None:
            current_sha = await loop.run_in_executor(None, self._calculate_sha256, file_path)
            if stored_sha and stored_sha == current_sha:
                await self.db.execute_write(
                    "UPDATE files SET size=?, modified_at=?, type=?, folder_tag=?, sha256=? WHERE path=?",
                    (current_size, current_mtime, file_path.suffix.lower(), folder_tag, current_sha, abs_path),
                )
                return "skipped"
            return "changed"

        return "new"

    async def index_file(self, path: Path, folder_tag: str):
        """Extracts text, chunks it, generates embeddings, and stores in both DBs."""
        chunks_added = 0
        try:
            stat = path.stat()

            if stat.st_size > self.max_file_size:
                logger.warning(
                    "Skipping oversized file (%d MB): %s",
                    stat.st_size // (1024 * 1024), path,
                )
                progress.update(0, str(path.name))
                return

            file_data = {
                "path": str(path.absolute()),
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "type": path.suffix.lower(),
                "folder_tag": folder_tag
            }

            existing_file = await self.db.get_file_by_path(file_data["path"])
            if existing_file:
                file_id = existing_file["id"]
                old_chunks = await self.db.get_file_chunks(file_id)
                old_chunk_ids = [str(c["id"]) for c in old_chunks]
                await self.chroma_client.delete_documents(old_chunk_ids)
                await self.db.delete_file_chunks(file_id)

            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(None, self._extract_text, path)
            if not text:
                progress.update(0, str(path.name))
                return

            summary = self._generate_summary(text, path)

            file_data["summary"] = summary
            file_id = await self.db.insert_file(file_data)

            chunks_data = self._create_chunks(text, file_path=str(path))

            for chunk in chunks_data:
                chunk["file_id"] = file_id
            chunk_ids_int = await self.db.insert_chunks_bulk(chunks_data)
            chunk_ids = [str(cid) for cid in chunk_ids_int]
            chunk_texts = [c["text_preview"] for c in chunks_data]

            await self.db.commit()

            if chunk_texts:
                embeddings = await self.embedding_service.embed_texts(chunk_texts)
                metadatas = [
                    {"chunk_id": cid, "file_path": str(path), "folder_tag": folder_tag} 
                    for cid in chunk_ids
                ]
                await self.chroma_client.add_documents(chunk_ids, embeddings, metadatas)
                chunks_added = len(chunk_ids)

            logger.info("Indexed file: %s (%d chunks)", path, chunks_added)

            if summary:
                try:
                    summary_emb = await self.embedding_service.embed_texts([summary])
                    await self.chroma_client.add_summary(
                        doc_id=f"file_{file_id}",
                        embedding=summary_emb[0],
                        metadata={
                            "file_id": file_id,
                            "file_path": str(path),
                            "folder_tag": folder_tag,
                        },
                    )
                except Exception as e:
                    logger.warning("Failed to store summary embedding for %s: %s", path, e)

        except Exception as e:
            logger.error("Error indexing file %s: %s", path, e)
        finally:
            progress.update(chunks_added, str(path.name))

    _TEXT_EXTENSIONS = frozenset({
        ".txt", ".md", ".py", ".js", ".ts", ".java", ".c", ".cpp", ".rs",
        ".go", ".rb", ".html", ".css", ".xml", ".yaml", ".yml", ".toml",
        ".ini", ".cfg", ".sh", ".bat",
    })
    _UNREAL_BINARY_EXTENSIONS = frozenset({".uasset", ".umap"})
    _UNREAL_PROJECT_EXTENSIONS = frozenset({".uproject", ".uplugin"})

    def _extract_text(self, path: Path) -> str:
        """Text extraction for multiple file types with safety timeouts.

        Supports: .txt, .md, .pdf, .docx, .csv, .json, and source code files.
        """
        import concurrent.futures
        ext = path.suffix.lower()
        extractor = {
            ".pdf": self._extract_pdf,
            ".docx": self._extract_docx,
            ".csv": self._extract_csv,
            ".json": self._extract_json,
        }.get(ext)

        if not extractor:
            if ext in self._TEXT_EXTENSIONS or ext in self._UNREAL_PROJECT_EXTENSIONS:
                return self._extract_plain_text(path)
            if ext in self._UNREAL_BINARY_EXTENSIONS:
                return self._extract_unreal_asset_stub(path)
            return ""

        # Use a localized thread pool to enforce a timeout on potentially hung extractors (PDF/DOCX)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(extractor, path)
            try:
                return future.result(timeout=45.0) # 45s hard limit per file extraction
            except concurrent.futures.TimeoutError:
                logger.error("Extraction timed out (45s) for file: %s", path)
                return ""
            except Exception as e:
                logger.error("Extraction failed for %s: %s", path, e)
                return ""

    @staticmethod
    def _extract_unreal_asset_stub(path: Path) -> str:
        """Return a lightweight textual stub for Unreal binary assets.

        This keeps binary Unreal assets represented in the index so project-level
        reasoning can use path/name/class signals even without binary parsing.
        """
        lower = str(path).lower().replace("\\", "/")
        kind = "map" if path.suffix.lower() == ".umap" else "asset"
        hint = ""
        if any(seg in lower for seg in ["/maps/", "/levels/"]):
            hint = " Environment/Level content."
        elif any(seg in lower for seg in ["/characters/", "/player/", "/npc/"]):
            hint = " Character-related content."
        elif any(seg in lower for seg in ["/materials/", "/niagara/"]):
            hint = " Rendering/VFX content."
        return (
            f"Unreal Engine binary {kind}: {path.name}. "
            f"Path: {path}. Binary content not parsed directly.{hint}"
        )

    @staticmethod
    def _extract_plain_text(path: Path) -> str:
        """Read a plain-text or source-code file."""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.error("Error reading text file %s: %s", path, e)
            return ""

    @staticmethod
    def _extract_pdf(path: Path) -> str:
        """Extract text from a PDF file."""
        try:
            import pdfplumber
            text_content: list[str] = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_content.append(page_text)
            return "\n".join(text_content)
        except ImportError:
            logger.error("pdfplumber not installed. Cannot index PDF files.")
            return ""
        except Exception as e:
            logger.error("Error reading PDF file %s: %s", path, e)
            return ""

    @staticmethod
    def _extract_docx(path: Path) -> str:
        """Extract text from a DOCX file."""
        try:
            from docx import Document
            doc = Document(str(path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n".join(paragraphs)
        except ImportError:
            logger.error("python-docx not installed. Cannot index DOCX files.")
            return ""
        except Exception as e:
            logger.error("Error reading DOCX file %s: %s", path, e)
            return ""

    @staticmethod
    def _extract_csv(path: Path) -> str:
        """Extract text from a CSV file (caps at 5000 rows)."""
        try:
            import csv
            rows: list[str] = []
            with open(path, encoding="utf-8", errors="replace", newline="") as f:
                reader = csv.reader(f)
                for i, row in enumerate(reader):
                    if i > 5000:
                        break
                    rows.append(", ".join(row))
            return "\n".join(rows)
        except Exception as e:
            logger.error("Error reading CSV file %s: %s", path, e)
            return ""

    @staticmethod
    def _extract_json(path: Path) -> str:
        """Extract text from a JSON file.

        Handles common real-world quirks:
        - UTF-8 BOM (``utf-8-sig``)
        - Trailing commas, unquoted keys, single-quoted strings (via fallback)
        - JSONL / multi-object files (``Extra data`` errors)
        """
        import json
        import re

        try:
            # Try utf-8-sig first to handle BOM transparently
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except Exception as e:
            logger.error("Error reading JSON file %s: %s", path, e)
            return ""

        # --- Attempt 1: strict parse --------------------------------
        try:
            data = json.loads(text)
            return json.dumps(data, indent=2, ensure_ascii=False)[:200_000]
        except json.JSONDecodeError:
            pass

        # --- Attempt 2: strip trailing commas before } and ] --------
        try:
            cleaned = re.sub(r',\s*([}\]])', r'\1', text)
            data = json.loads(cleaned)
            return json.dumps(data, indent=2, ensure_ascii=False)[:200_000]
        except json.JSONDecodeError:
            pass

        # --- Attempt 3: JSONL (one JSON object per line) ------------
        try:
            parts: list[str] = []
            for line in text.splitlines():
                line = line.strip()
                if line:
                    parts.append(
                        json.dumps(json.loads(line), indent=2, ensure_ascii=False)
                    )
            if parts:
                return "\n".join(parts)[:200_000]
        except json.JSONDecodeError:
            pass

        # --- Fallback: treat as plain text so the file isn't skipped -
        logger.debug("JSON parse failed for %s — falling back to raw text.", path)
        return text[:200_000]

    def _generate_summary(self, text: str, path: Path, max_chars: int = 300) -> str:
        """Build a short extractive summary for document-level indexing.

        Format: ``[TYPE: filename] first N characters (sentence-snapped)``
        This is used as the document-level embedding in the summary
        collection so queries can first identify *which* files are relevant
        before drilling into individual chunks.
        """
        fname = path.name
        ftype = path.suffix.lstrip(".").upper() or "file"
        prefix = f"[{ftype}: {fname}] "

        raw = text[:max_chars + 80]  # overshoot to allow sentence snap
        boundary = self._find_sentence_boundary(raw, min(max_chars, len(raw)))
        if boundary <= 0:
            boundary = min(max_chars, len(raw))
        snippet = raw[:boundary].strip()
        return prefix + snippet

    @staticmethod
    def _find_sentence_boundary(text: str, pos: int, window: int = 80) -> int:
        """Find the nearest sentence-ending punctuation near *pos*.

        Looks backwards up to *window* chars for '.', '!', '?', or '\n\n'.
        Returns the index **after** the delimiter so the next chunk starts
        on a clean sentence.  Falls back to *pos* if nothing is found.
        """
        search_start = max(0, pos - window)
        region = text[search_start:pos]
        for delim in ["\n\n", ". ", "! ", "? ", ".\n", "!\n", "?\n"]:
            idx = region.rfind(delim)
            if idx != -1:
                return search_start + idx + len(delim)
        return pos

    def _create_chunks(self, text: str, file_path: str = "") -> List[Dict[str, Any]]:
        """Splits text into sentence-aware, overlapping chunks.

        For Markdown files, first splits on headings before falling back
        to character-based chunking.  A file-context prefix is prepended
        to every chunk so the embedding captures document-level context.
        """
        if not text:
            return []

        context_prefix = self._build_context_prefix(file_path)

        ext = Path(file_path).suffix.lower() if file_path else ""
        if ext == ".md":
            md_chunks = self._chunk_markdown(text, context_prefix)
            if md_chunks:
                return md_chunks

        return self._split_text(text, context_prefix, 0)

    @staticmethod
    def _build_context_prefix(file_path: str) -> str:
        """Build a ``[TYPE: filename]`` prefix for chunk embeddings."""
        if not file_path:
            return ""
        fname = Path(file_path).name
        ftype = Path(file_path).suffix.lstrip(".").upper() or "file"
        return f"[{ftype}: {fname}] "

    def _chunk_markdown(
        self, text: str, context_prefix: str
    ) -> List[Dict[str, Any]]:
        """Split Markdown text by headings into section-aware chunks."""
        import re
        sections = re.split(r'(?=^#{1,3}\s)', text, flags=re.MULTILINE)
        sections = [s for s in sections if s.strip()]

        chunks: List[Dict[str, Any]] = []
        offset = 0
        for section in sections:
            section_start = text.find(section, offset)
            if section_start == -1:
                section_start = offset
            if len(section) <= self.chunk_size:
                chunks.append({
                    "start_offset": section_start,
                    "end_offset": section_start + len(section),
                    "text_preview": context_prefix + section.strip(),
                })
            else:
                chunks.extend(self._split_text(section, context_prefix, section_start))
            offset = section_start + len(section)
        return chunks

    def _split_text(
        self, text: str, context_prefix: str, base_offset: int
    ) -> List[Dict[str, Any]]:
        """Character-based chunking with sentence-boundary snapping."""
        chunks: List[Dict[str, Any]] = []
        start = 0
        text_len = len(text)
        while start < text_len:
            raw_end = min(start + self.chunk_size, text_len)
            end = (
                self._find_sentence_boundary(text, raw_end)
                if raw_end < text_len
                else raw_end
            )
            if end <= start:
                end = raw_end

            chunk_text = context_prefix + text[start:end]
            chunks.append({
                "start_offset": base_offset + start,
                "end_offset": base_offset + end,
                "text_preview": chunk_text,
            })
            start = end - self.chunk_overlap if end < text_len else text_len
        return chunks
