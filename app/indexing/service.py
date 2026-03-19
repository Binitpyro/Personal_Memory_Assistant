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

try:
    import rust_core
    RUST_CORE_AVAILABLE = True
except ImportError:
    RUST_CORE_AVAILABLE = False

logger = logging.getLogger(__name__)

UNREAL_PROJECT_EXT = ".uproject"
UNITY_SCENE_EXT = ".unity"
NODE_PACKAGE_FILE = "package.json"
PYTHON_PROJECT_LABEL = "Python project"

# ── Project-type detection rules ────────────────────────────────────
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
    """
    Infer the project's type and provide a short human-readable description based on collected file markers.
    
    Parameters:
        files (List[Tuple[Path, str]]): Iterable of (file_path, folder_tag) pairs to analyze.
        folder (Path): Root folder used to derive relative directories and context for detection.
    
    Returns:
        Tuple[str, str]: (project_type, description) where `project_type` is a key identifying the inferred type
        (e.g., "python", "unity", "<ext> files", or "unknown") and `description` is a brief human-readable summary.
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
    """
    Construct a textual and metadata profile describing the contents and characteristics of a folder.
    
    Parameters:
        folder (Path): The folder being profiled.
        folder_tag (str): Short label used to identify the folder in summaries.
        files (List[Tuple[Path, str]]): All scanned files as (path, folder_tag) tuples; only entries under `folder` are considered.
    
    Returns:
        Dict[str, Any]: A dictionary containing:
            - `folder_path` (str): Absolute folder path.
            - `folder_tag` (str): The provided folder tag.
            - `profile_text` (str): Human-readable summary text describing project tag, inferred type, location, file count, total size, main extensions, and optional key files/top-level folders.
            - `project_type` (str): Inferred project kind (e.g., "Python project", "unknown", or "<ext> files").
            - `file_count` (int): Number of files found under `folder`.
            - `total_size_bytes` (int): Sum of file sizes in bytes (skips files that cannot be stat'ed).
            - `top_extensions` (str): Comma-separated top file extensions with counts (top 8).
            - `key_files` (str): Comma-separated list of identified key filenames (up to 15).
    """
    folder_files = [(fp, ft) for fp, ft in files if str(fp).startswith(str(folder))]

    ext_counts: Counter = Counter()
    total_size = 0
    key_files_list: List[str] = []

    _KEY_NAMES = {
        "readme.md", "readme.txt", "readme",
        "package.json", "pyproject.toml", "setup.py", "requirements.txt",
        "cargo.toml", "go.mod", "pom.xml", "build.gradle",
        "cmakelists.txt", "makefile", ".gitignore",
        "dockerfile", "docker-compose.yml",
    }
    _KEY_EXTS = {UNREAL_PROJECT_EXT, ".sln", ".csproj", UNITY_SCENE_EXT}

    for fp, _ in folder_files:
        ext = fp.suffix.lower()
        ext_counts[ext] += 1
        if fp.name.lower() in _KEY_NAMES or ext in _KEY_EXTS:
            key_files_list.append(fp.name)

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
    """
    Normalize and filter a list of folder path strings, keeping only existing absolute directories and removing any folder that is nested inside another kept folder.
    
    Parameters:
        folders (List[str]): Iterable of folder path strings; each entry may include surrounding quotes or extra whitespace.
    
    Returns:
        List[pathlib.Path]: Absolute Path objects for the remaining folders. Parent (shallower) directories are preferred over nested children, and nested folders found inside already-kept parents are omitted.
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
    def __init__(self):
        """
        Initialize an IndexingProgress instance and its thread-safe state counters.
        
        Creates an internal lock and initializes counters and status fields used to track indexing progress:
        - _lock: threading.Lock used to protect concurrent mutations.
        - total_files: total number of files expected to be scanned/processed.
        - processed_files: number of files processed so far.
        - total_chunks: total number of chunked text units produced.
        - skipped_files: count of files skipped during processing.
        - new_files: count of newly discovered files to index.
        - changed_files: count of files detected as changed since last index.
        - status: current state label (e.g., "idle", "running").
        - scan_method: identifier of the scan implementation used (e.g., "rust_jwalk", "python").
        - scan_duration_ms: elapsed scan duration in milliseconds.
        - current_file: human-readable description of the file currently being processed (defaults to "Ready").
        """
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
        self.current_file = "Ready"

    def reset(self, total_files: int, initial_status: str = "running"):
        """
        Reset the progress counters and initialize status for a new indexing run.
        
        Parameters:
            total_files (int): Total number of files expected to be processed in the upcoming run.
            initial_status (str): Initial status label to set (defaults to "running").
        """
        with self._lock:
            self.total_files = total_files
            self.processed_files = 0
            self.total_chunks = 0
            self.skipped_files = 0
            self.new_files = 0
            self.changed_files = 0
            self.status = initial_status
            self.scan_method = ""
            self.scan_duration_ms = 0.0
            self.current_file = "Starting…"

    def update(self, chunks_added: int, current_file: str = ""):
        """
        Increment progress counters for a processed file.
        
        Thread-safe: updates the processed file count and accumulates chunk totals; optionally updates the current file/status string.
        
        Parameters:
            chunks_added (int): Number of chunks produced for the processed file; this value is added to the running total of chunks.
            current_file (str, optional): If non-empty, replaces the current file/status message shown in progress.
        """
        with self._lock:
            self.processed_files += 1
            self.total_chunks += chunks_added
            if current_file:
                self.current_file = current_file

    def set_current_file(self, current_file: str):
        """
        Set the current file status string shown in indexing progress.
        
        Parameters:
            current_file (str): Human-readable name or message for the file currently being processed.
        """
        with self._lock:
            self.current_file = current_file

    def complete(self):
        """
        Mark the indexing progress as complete.
        
        Acquires the internal lock and sets `status` to "idle" and `current_file` to "Complete".
        """
        with self._lock:
            self.status = "idle"
            self.current_file = "Complete"

progress = IndexingProgress()
indexing_lock = asyncio.Lock()

class IndexingService:
    _TEXT_EXTENSIONS = frozenset({
        ".txt", ".md", ".py", ".js", ".ts", ".java", ".c", ".cpp", ".rs",
        ".go", ".rb", ".html", ".css", ".xml", ".yaml", ".yml", ".toml",
        ".ini", ".cfg", ".sh", ".bat", ".log", ""
    })
    _UNREAL_BINARY_EXTENSIONS = frozenset({".uasset", ".umap"})
    _UNREAL_PROJECT_EXTENSIONS = frozenset({".uproject", ".uplugin"})

    def __init__(
        self, 
        db: DatabaseManager, 
        embedding_service: EmbeddingService, 
        chroma_client: ChromaClient
    ):
        """
        Initializes the indexing service with required dependencies and loads indexing configuration.
        
        Parameters:
            db (DatabaseManager): Database manager used for file, chunk and profile persistence.
            embedding_service (EmbeddingService): Service used to generate embeddings for text and summaries.
            chroma_client (ChromaClient): Vector store client used to add, delete and manage vectors.
        
        The constructor stores the provided dependencies and reads runtime settings to populate:
            - supported_extensions: set of file extensions to consider for indexing
            - chunk_size: target chunk size in characters
            - chunk_overlap: overlap size between adjacent chunks
            - max_file_size: maximum bytes of file content to extract
            - _concurrency: concurrency limit used during extraction/embedding
        """
        self.db = db
        self.embedding_service = embedding_service
        self.chroma_client = chroma_client
        self.supported_extensions = settings.extensions_set
        self.chunk_size = settings.chunk_size
        self.chunk_overlap = settings.chunk_overlap
        self.max_file_size = settings.max_file_size_bytes
        self._concurrency = settings.index_concurrency

    async def index_folders(self, folders: List[str]):
        """
        Orchestrates scanning, change detection, chunking/embedding/storage, and folder-profile generation for the provided folders.
        
        This method prevents concurrent indexing runs, resolves overlapping or invalid folder inputs, scans each resolved folder for files, detects which files are new or changed, and indexes those files in batches. After indexing it generates and upserts folder profiles and clears the retrieval cache. Progress state is updated throughout the operation; if no files require indexing the method still generates folder profiles and completes progress.
        
        Parameters:
            folders (List[str]): Iterable of folder paths to index. Overlapping paths are resolved and non-existent folders are ignored.
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

            logger.info("Starting indexing for %d folder(s): %s", len(unique_folders), [str(f) for f in unique_folders])

            progress.reset(0)
            progress.status = "running"
            progress.current_file = "Scanning folders…"

            loop = asyncio.get_running_loop()
            all_files, scan_method, scan_duration = await loop.run_in_executor(
                None, self._scan_all_folders, unique_folders
            )

            if not all_files:
                logger.info("No files found.")
                progress.status = "idle"
                return

            files_to_index, skipped, new_count, changed_count = await self._detect_changes(all_files)

            progress.reset(len(files_to_index) * 2)
            progress.scan_method = scan_method
            progress.scan_duration_ms = scan_duration
            progress.skipped_files = skipped
            progress.new_files = new_count
            progress.changed_files = changed_count

            if not files_to_index:
                logger.info("All files are up-to-date.")
                await self._generate_folder_profiles(all_files, unique_folders)
                progress.complete()
                return

            BATCH_SIZE = 1500
            for i in range(0, len(files_to_index), BATCH_SIZE):
                batch = files_to_index[i:i + BATCH_SIZE]
                await self._batch_index_pipeline(batch, offset=i, total_to_index=len(files_to_index))
                import gc
                gc.collect()

            await self._generate_folder_profiles(all_files, unique_folders)

            from app.search.retrieval import clear_retrieval_cache
            clear_retrieval_cache()

            progress.complete()
            logger.info("Indexing completed: %d processed.", len(files_to_index))

    async def _batch_index_pipeline(self, files_to_index: List[Tuple[Path, str]], offset: int = 0, total_to_index: int = 0) -> None:
        """
        Run the three-stage indexing pipeline for a batch of files: extraction, embedding, and storage.
        
        Processes the provided files in three phases:
        1) Extracts and prepares text, metadata, chunks, and summaries for each file.
        2) Builds an embedding payload from prepared items and obtains embeddings from the embedding service.
        3) Assigns embeddings back to prepared items and stores files/chunks/summaries into the database and vector store.
        
        Parameters:
            files_to_index (List[Tuple[Path, str]]): Sequence of (file_path, folder_tag) pairs to process in this batch.
            offset (int): Numeric offset used for progress reporting (index of the first file in this batch within the overall run).
            total_to_index (int): Grand total number of files being indexed across all batches; used to compute overall progress reporting. If zero, the batch length is used as the total.
        """
        batch_total = len(files_to_index)
        total_so_far = offset
        grand_total = total_to_index or batch_total
        
        logger.info("Pipeline phase 1/3: extracting text from %d files … (Batch %d-%d)", batch_total, offset, offset + batch_total)
        progress.set_current_file(f"Phase 1/3: Extracting {batch_total} files (Batch {offset}/{grand_total})…")
        
        semaphore = asyncio.Semaphore(self._concurrency * 2)
        extracted_count = 0
        extracted_lock = asyncio.Lock()

        async def _safe_extract(path: Path, tag: str):
            """
            Run extraction and preparation for a single file under the concurrency semaphore and update indexing progress.
            
            Parameters:
                path (Path): Path of the file to extract and prepare.
                tag (str): Folder tag used to annotate the prepared result.
            
            Returns:
                dict or None: The prepared item dictionary on success, or `None` if extraction failed.
            """
            nonlocal extracted_count
            async with semaphore:
                res = await self._extract_and_prepare_async(path, tag)
                async with extracted_lock:
                    extracted_count += 1
                    overall = total_so_far + extracted_count
                    progress.set_current_file(f"Extracting: {path.name} ({overall}/{grand_total})")
                return res

        tasks = [_safe_extract(fp, ft) for fp, ft in files_to_index]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        prepared = self._collect_prepared_items(files_to_index, results)
        if not prepared:
            progress.set_current_file("No valid content extracted.")
            return

        all_texts, text_map = self._build_embedding_payload(prepared)
        logger.info("Pipeline phase 2/3: embedding %d texts …", len(all_texts))
        progress.set_current_file(f"Phase 2/3: Embedding {len(all_texts)} texts…")
        
        all_embeddings = await self.embedding_service.embed_texts(all_texts, batch_size=settings.embedding_batch_size)

        self._assign_embeddings(prepared, text_map, all_embeddings)
        logger.info("Pipeline phase 3/3: storing %d files …", len(prepared))
        progress.set_current_file(f"Phase 3/3: Storing {len(prepared)} files…")
        await self._store_prepared_items(prepared)

    def _collect_prepared_items(self, files_to_index: List[Tuple[Path, str]], results: List[Any]) -> List[Dict[str, Any]]:
        """
        Filter and collect successful extraction results paired with their input file entries.
        
        Parameters:
            files_to_index (List[Tuple[Path, str]]): Sequence of (file_path, folder_tag) entries that were submitted for extraction; order corresponds to `results`.
            results (List[Any]): Extraction results aligned with `files_to_index`; entries may be dicts for successful preparations or exception/other values for failures.
        
        Returns:
            List[Dict[str, Any]]: List of prepared item dictionaries extracted from `results` in the same order as their corresponding successful inputs.
        """
        prepared: List[Dict[str, Any]] = []
        for (file_path, _), result in zip(files_to_index, results):
            if isinstance(result, (Exception, BaseException)):
                logger.error("Error extracting %s: %s", file_path, result)
                continue
            if result and isinstance(result, dict):
                prepared.append(result)
            elif result:
                logger.error("Unexpected result type for %s: %s (%s)", file_path, type(result), str(result))
        return prepared

    @staticmethod
    def _build_embedding_payload(prepared: List[Dict[str, Any]]) -> Tuple[List[str], List[Tuple[int, str, int]]]:
        """
        Builds the list of texts to embed and a mapping from embedding positions back to source items.
        
        Parameters:
            prepared (List[Dict[str, Any]]): List of prepared file items. Each item should contain a "chunks" list where each chunk is a dict with a "text_preview" string, and may contain a "summary" string.
        
        Returns:
            Tuple[List[str], List[Tuple[int, str, int]]]:
                - all_texts: concatenated list of chunk previews followed by any summaries, in the order they will be sent to the embedding service.
                - text_map: list of tuples mapping each entry in `all_texts` back to its origin. Each tuple is (file_index, kind, sub_index) where `file_index` is the index of the prepared item, `kind` is either "chunk" or "summary", and `sub_index` is the chunk index for "chunk" entries or 0 for "summary".
        """
        all_texts: List[str] = []
        text_map: List[Tuple[int, str, int]] = []
        for file_idx, item in enumerate(prepared):
            try:
                for chunk_idx, chunk in enumerate(item["chunks"]):
                    all_texts.append(chunk["text_preview"])
                    text_map.append((file_idx, "chunk", chunk_idx))
                if item.get("summary"):
                    all_texts.append(item["summary"])
                    text_map.append((file_idx, "summary", 0))
            except (KeyError, TypeError) as e:
                logger.error("Malformed prepared item for %s: %s", item.get("path", "unknown"), e)
        return all_texts, text_map

    @staticmethod
    def _assign_embeddings(prepared: List[Dict[str, Any]], text_map: List[Tuple[int, str, int]], all_embeddings: List[List[float]]) -> None:
        """
        Assign embedding vectors to their corresponding prepared item chunks or summaries.
        
        Parameters:
            prepared (List[Dict[str, Any]]): List of prepared file items produced by extraction; each item contains a "chunks" list.
            text_map (List[Tuple[int, str, int]]): Mapping from embedding index to target location as (file_index, kind, sub_index). `kind` is either `"chunk"` or `"summary"`. For `"chunk"`, `sub_index` is the chunk index; for `"summary"`, `sub_index` is ignored.
            all_embeddings (List[List[float]]): Embedding vectors in the same order referenced by `text_map`.
        
        Side effects:
            Mutates `prepared` in place by setting either `prepared[file_index]["chunks"][chunk_index]["_embedding"]`
            for chunk embeddings or `prepared[file_index]["_summary_embedding"]` for summary embeddings.
        """
        for idx, (file_idx, kind, sub_idx) in enumerate(text_map):
            if kind == "chunk":
                prepared[file_idx]["chunks"][sub_idx]["_embedding"] = all_embeddings[idx]
            else:
                prepared[file_idx]["_summary_embedding"] = all_embeddings[idx]

    async def _store_prepared_items(self, prepared: List[Dict[str, Any]]) -> None:
        """
        Persist prepared file items into storage.
        
        Processes the given prepared items in batches: inserts/updates file and chunk records in the database, commits each batch, and writes corresponding embeddings and metadata to the vector store. Each prepared item is expected to include the extracted `file_data`, a `chunks` list where each chunk may carry an assigned embedding, and an optional summary embedding. This method performs durable writes and may perform network or I/O operations.
         
        Parameters:
            prepared (List[Dict[str, Any]]): List of prepared item dictionaries produced by the extraction pipeline. Each item should contain at minimum:
                - `file_data` (dict): file metadata including `path`.
                - `chunks` (List[dict]): chunk entries; embeddings should be attached to chunk entries prior to calling this method.
                - optional summary embedding field used to create folder/file summary documents for the vector store.
        """
        all_paths = [item["file_data"]["path"] for item in prepared]
        existing_map = await self.db.get_existing_file_ids(all_paths)

        STORE_BATCH = 100
        for batch_start in range(0, len(prepared), STORE_BATCH):
            all_chroma_ids, all_chroma_embs, all_chroma_metas, summary_items = [], [], [], []
            batch = prepared[batch_start : batch_start + STORE_BATCH]
            for item in batch:
                await self._store_single_prepared_item(item, all_chroma_ids, all_chroma_embs, all_chroma_metas, summary_items, existing_map)
            
            await self.db.commit()
            await self._flush_chroma_batches(all_chroma_ids, all_chroma_embs, all_chroma_metas, summary_items)

    async def _store_single_prepared_item(self, item: Dict[str, Any], all_chroma_ids, all_chroma_embs, all_chroma_metas, summary_items, existing_map) -> None:
        """
        Store a prepared file's DB records and stage its vectors/metadata for the vector store.
        
        Parameters:
            item (Dict[str, Any]): Prepared item containing `file_data`, `chunks` (each with `_embedding`), `folder_tag`, and optional `_summary_embedding`.
            all_chroma_ids (list): Mutable list to append new chunk document IDs (strings) for later vector upsert.
            all_chroma_embs (list): Mutable list to append corresponding embedding vectors for each chunk.
            all_chroma_metas (list): Mutable list to append metadata dicts for each chunk (`chunk_id`, `file_path`, `folder_tag`).
            summary_items (list): Mutable list to append a summary document dict when a summary embedding is present (contains `doc_id`, `embedding`, `metadata`).
            existing_map (Mapping[str, Any] | None): Map of file path -> existing file_id; if present, existing chunks for that file_id will be deleted before insert.
        
        Behavior:
            - Deletes existing chunk vectors for the file when an existing file id is found.
            - Inserts or updates the file row and its chunk rows in the database (batched commit expected by caller).
            - Appends chunk IDs, chunk embeddings, and chunk metadata to the provided chroma lists.
            - Appends a summary document entry to `summary_items` if the item contains a summary embedding.
            - Updates the global progress state; on exception logs an error and updates progress but does not re-raise.
        """
        try:
            file_data = item["file_data"]
            file_path = file_data["path"]
            folder_tag = item["folder_tag"]
            fname = item["path"].name

            progress.set_current_file(f"Storing: {fname}")

            existing_id = (existing_map or {}).get(file_path)
            if existing_id is not None:
                await self._delete_existing_chunks(existing_id)

            file_id = await self.db.insert_file(file_data, auto_commit=False)
            chunk_ids_int = await self.db.insert_chunks_bulk(self._build_chunk_rows(item["chunks"], file_id))

            for chunk_id_int, chunk in zip(chunk_ids_int, item["chunks"]):
                cid = str(chunk_id_int)
                all_chroma_ids.append(cid)
                all_chroma_embs.append(chunk["_embedding"])
                all_chroma_metas.append({"chunk_id": cid, "file_path": file_path, "folder_tag": folder_tag})

            if item.get("_summary_embedding"):
                summary_items.append({"doc_id": f"file_{file_id}", "embedding": item["_summary_embedding"], "metadata": {"file_id": file_id, "file_path": file_path, "folder_tag": folder_tag}})
            
            progress.update(0)
        except Exception as e:
            logger.error("Error storing %s: %s", item.get("path", "unknown"), e)
            progress.update(0)

    async def _delete_existing_chunks(self, file_id: int) -> None:
        """
        Remove all vector-store documents and database chunk rows associated with a file.
        
        Parameters:
            file_id (int): Database ID of the file whose chunk records should be removed.
        
        Description:
            If the file has stored chunk vector IDs, they are deleted from the Chroma vector store, then the corresponding chunk rows are removed from the database (performed with auto_commit=False).
        """
        old_chunks = await self.db.get_file_chunks(file_id)
        old_ids = [str(chunk["id"]) for chunk in old_chunks]
        if old_ids: await self.chroma_client.delete_documents(old_ids)
        await self.db.delete_file_chunks(file_id, auto_commit=False)

    @staticmethod
    def _build_chunk_rows(chunks: List[Dict[str, Any]], file_id: int) -> List[Dict[str, Any]]:
        """
        Create database-ready rows from prepared chunk dictionaries by removing in-memory embeddings and attaching the parent file ID.
        
        Parameters:
            chunks (List[Dict[str, Any]]): Prepared chunk dictionaries (may include an internal `_embedding` key).
            file_id (int): Database file identifier to assign to each chunk row.
        
        Returns:
            List[Dict[str, Any]]: A list of chunk row dictionaries suitable for DB insertion; each entry is the original chunk without the `_embedding` key and with `file_id` set to the provided value.
        """
        rows = [{k: v for k, v in c.items() if k != "_embedding"} for c in chunks]
        for r in rows: r["file_id"] = file_id
        return rows

    async def _flush_chroma_batches(self, ids, embs, metas, summaries) -> None:
        """
        Flushes pending vector documents and optional summary embeddings to the Chroma vector store.
        
        Documents are sent in batches of up to 5000 items and written concurrently; if `summaries` is provided it is sent as an additional summary batch. The function waits for all pending writes to complete.
        
        Parameters:
            ids (List[str]): List of Chroma document IDs corresponding to the embeddings.
            embs (List[List[float]]): List of embedding vectors matching `ids`.
            metas (List[Dict[str, Any]]): List of metadata dictionaries for each document.
            summaries (List[Dict[str, Any]]): Optional list of summary entries to upsert (may be empty).
        """
        tasks = []
        for i in range(0, len(ids), 5000):
            end = i + 5000
            tasks.append(self.chroma_client.add_documents(ids[i:end], embs[i:end], metas[i:end]))
        if summaries: tasks.append(self.chroma_client.add_summaries_batch(summaries))
        if tasks: await asyncio.gather(*tasks)

    async def _generate_folder_profiles(self, all_files, folders) -> None:
        """
        Generate profile summaries for the given folders and persist them to the database and vector store.
        
        Builds a textual profile for each folder (using collected file metadata), upserts each profile into the database, commits the transaction, and optionally embeds the profile texts and writes those embeddings to the vector store as folder-profile summaries. Folder-profile generation runs in a thread pool; failures for individual folders are skipped.
        
        Parameters:
            all_files (List[Tuple[Path, str]]): Collected files as (Path, folder_tag) pairs used to compute folder profiles.
            folders (List[Path]): Folder Path objects to profile; each profile will use the folder's name as its folder_tag.
        """
        logger.info("Generating folder profiles …")
        progress.set_current_file("Generating folder profiles…")
        profile_texts, profiles = [], []
        loop = asyncio.get_running_loop()
        max_workers = min(len(folders), (os.cpu_count() or 4) + 2)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [loop.run_in_executor(pool, _build_folder_profile, f, f.name, all_files) for f in folders]
            results = await asyncio.gather(*futs, return_exceptions=True)

        for folder, res in zip(folders, results):
            if isinstance(res, Exception): continue
            profiles.append(res)
            profile_texts.append(res["profile_text"])

        for p in profiles: await self.db.upsert_folder_profile(p, auto_commit=False)
        await self.db.commit()

        if profile_texts:
            embs = await self.embedding_service.embed_texts(profile_texts)
            summaries = [{"doc_id": f"folder_profile_{p['folder_tag']}", "embedding": e, "metadata": {"file_path": p["folder_path"], "folder_tag": p["folder_tag"], "project_type": p["project_type"], "is_folder_profile": "true"}} for p, e in zip(profiles, embs)]
            await self.chroma_client.add_summaries_batch(summaries)

    async def _extract_and_prepare_async(self, path: Path, folder_tag: str) -> Optional[Dict[str, Any]]:
        """
        Run the synchronous extraction-and-preparation routine for a file in a thread executor and return the prepared item.
        
        Parameters:
            path (Path): Path to the file to extract and prepare.
            folder_tag (str): Label identifying the folder the file belongs to.
        
        Returns:
            Optional[Dict[str, Any]]: A prepared item dictionary containing `file_data`, `chunks`, and `summary` on success; `None` if extraction/preparation failed (an error is logged and the global progress is updated).
        """
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._extract_and_prepare, path, folder_tag)
        except Exception as e:
            logger.error("Error preparing %s: %s", path, e)
            progress.update(0)
            return None

    def _extract_and_prepare(self, path: Path, folder_tag: str) -> Optional[Dict[str, Any]]:
        """
        Prepare extraction metadata, text chunks, and a short summary for a single file.
        
        Parameters:
        	path (Path): Path to the file to extract and prepare.
        	folder_tag (str): Label identifying the folder or collection the file belongs to.
        
        Returns:
        	Dict[str, Any]: A mapping containing:
        		- "path" (Path): the original Path object.
        		- "folder_tag" (str): the provided folder tag.
        		- "file_data" (dict): metadata with keys:
        			- "path" (str): absolute file path.
        			- "size" (int): file size in bytes.
        			- "modified_at" (str): ISO 8601 modification timestamp.
        			- "type" (str): lowercased file suffix (extension).
        			- "folder_tag" (str): same as the provided folder_tag.
        			- "summary" (str): short textual summary of the file.
        			- "sha256" (str): computed SHA256 (sampled for very large files).
        		- "chunks" (List[dict]): list of chunk dictionaries suitable for embedding/storage.
        		- "summary" (str): the same summary string included in file_data.
        	`None` if an error occurred while processing the file.
        """
        try:
            stat = path.stat()
            text = self._extract_text(path)
            summary = self._generate_summary(text, path)
            chunks = self._create_chunks(text, file_path=str(path))
            sha256 = self._calculate_sha256(path)

            res = {"path": path, "folder_tag": folder_tag, "file_data": {"path": str(path.absolute()), "size": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(), "type": path.suffix.lower(), "folder_tag": folder_tag, "summary": summary, "sha256": sha256}, "chunks": chunks, "summary": summary}
            progress.update(0)
            return res
        except Exception as e:
            logger.error("Error preparing %s: %s", path, e)
            progress.update(0)
            return None

    def _scan_all_folders(self, unique_folders: List[Path]) -> Tuple[List[Tuple[Path, str]], str, float]:
        """
        Scan the provided folders for matching files and return the discovered files, the scanner used, and elapsed time.
        
        Returns:
            tuple: (all_files, scan_method, elapsed_ms)
                - all_files: list of (Path, str) tuples where each Path is a discovered file and the str is the folder tag it belongs to.
                - scan_method: string identifying which scanning implementation was used (for example 'rust_jwalk' or a Python scanner).
                - elapsed_ms: total scan duration in milliseconds as a float.
        """
        if RUST_CORE_AVAILABLE:
            return self._scan_all_folders_rust(unique_folders)
        return self._scan_all_folders_python(unique_folders)

    def _scan_all_folders_rust(self, unique_folders: List[Path]) -> Tuple[List[Tuple[Path, str]], str, float]:
        """
        Scan the given folders for matching files using the Rust-backed scanner and map each discovered file to its containing folder name.
        
        Parameters:
            unique_folders (List[Path]): Folders to scan.
        
        Returns:
            Tuple[List[Tuple[Path, str]], str, float]: A tuple containing:
                - a list of (Path, folder_name) pairs for discovered files (deduplicated, case-insensitively),
                - a string identifying the scan method ("rust_jwalk" on success or the python scan method on fallback),
                - elapsed time in milliseconds for the scan. On Rust scanner failure, the function falls back to the Python scanner and returns that result.
        """
        import time
        t0 = time.perf_counter()
        folder_strs = [str(f) for f in unique_folders]
        ext_strs = list(self.supported_extensions)
        all_files, seen_paths = [], set()
        
        # Pre-resolve folders once to avoid overhead in the loop
        resolved_folders = [(f.resolve(), f.name) for f in unique_folders]

        try:
            # rust_core.scan_folders returns canonicalized strings
            rust_paths = rust_core.scan_folders(folder_strs, ext_strs)
            for path_str in rust_paths:
                path_obj = Path(path_str)
                # On Windows, rust_core returns lowercase or normalized paths, but 
                # path_str is already absolute from canonicalize().
                # String matching is much faster than Path.resolve()
                abs_p_str = str(path_obj).lower()
                if abs_p_str in seen_paths: continue
                seen_paths.add(abs_p_str)
                
                matched_folder_name = "Unknown"
                for f_resolved, f_name in resolved_folders:
                    if abs_p_str.startswith(str(f_resolved).lower()):
                        matched_folder_name = f_name
                        break
                all_files.append((path_obj, matched_folder_name))
            
            return all_files, "rust_jwalk", (time.perf_counter() - t0) * 1000
        except Exception as e:
            logger.error("Rust scan failed: %s", e)
            return self._scan_all_folders_python(unique_folders)

    def _scan_all_folders_python(self, unique_folders: List[Path]) -> Tuple[List[Tuple[Path, str]], str, float]:
        """
        Scan the provided folders for matching files, deduplicate results, and report the scan method and total duration.
        
        Parameters:
            unique_folders (List[Path]): Folders to scan for supported files.
        
        Returns:
            all_files (List[Tuple[Path, str]]): List of (file_path, folder_name) tuples for discovered files; each file appears at most once (deduplicated by resolved absolute path).
            scan_method (str): Identifier of the scan implementation used (e.g., the underlying scan method name).
            scan_duration (float): Aggregate scanning duration in milliseconds across all folders.
        """
        all_files, seen_paths = [], set()
        scan_method, scan_duration = "", 0.0
        def _scan_one(folder: Path): """
Perform a fast scan of a single folder and return the folder paired with its scan result.

Parameters:
	folder (Path): Directory to scan.

Returns:
	tuple: (folder, scan_result) where `scan_result` is an object containing discovered files, the scanning method used, and `duration_ms` (elapsed milliseconds).
"""
return folder, fast_scan(folder, self.supported_extensions)
        max_workers = min(len(unique_folders), (os.cpu_count() or 4) + 2)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_scan_one, p) for p in unique_folders]
            for fut in concurrent.futures.as_completed(futures):
                folder, res = fut.result()
                scan_method, scan_duration = res.method, scan_duration + res.duration_ms
                for fp in res.files:
                    abs_key = str(fp.resolve())
                    if abs_key not in seen_paths:
                        seen_paths.add(abs_key)
                        all_files.append((fp, folder.name))
        return all_files, scan_method, scan_duration

    async def _detect_changes(self, all_files: List[Tuple[Path, str]]) -> Tuple[List[Tuple[Path, str]], int, int, int]:
        """
        Determine which files have been added or changed compared to the database and return the list to index along with counts.
        
        Parameters:
            all_files (List[Tuple[Path, str]]): List of (file_path, folder_tag) pairs to check.
        
        Returns:
            Tuple containing:
              - to_index (List[Tuple[Path, str]]): Files that should be indexed (new or changed).
              - skipped (int): Number of files skipped because they are unchanged or inaccessible.
              - new_count (int): Number of files identified as new.
              - changed_count (int): Number of files identified as changed.
        """
        file_paths = [str(fp.absolute()) for fp, _ in all_files]
        change_map = await self.db.get_files_change_map(file_paths)
        loop = asyncio.get_running_loop()

        def _get_info(fp: Path):
            """
            Return the file's modification time (ISO 8601) and size in bytes.
            
            Parameters:
                fp (Path): Path to the file.
            
            Returns:
                tuple: `(iso_mtime, size)` where `iso_mtime` is the file's modification time as an ISO 8601 string or `None` if the timestamp cannot be read, and `size` is the file size in bytes (returns `0` on error).
            """
            try:
                stat = fp.stat()
                return datetime.fromtimestamp(stat.st_mtime).isoformat(), stat.st_size
            except OSError: return None, 0

        max_workers = min(len(all_files), (os.cpu_count() or 4) * 4, 64)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            infos = await asyncio.gather(*[loop.run_in_executor(pool, _get_info, fp) for fp, _ in all_files])

        to_index, skipped, new_c, changed_c = [], 0, 0, 0
        for (fp, tag), (mtime, size) in zip(all_files, infos):
            status = await self._process_file_change(fp, tag, mtime, size, change_map, loop)
            if status == "skipped": skipped += 1
            elif status == "new": new_c += 1; to_index.append((fp, tag))
            elif status == "changed": changed_c += 1; to_index.append((fp, tag))
        
        logger.info("Change detection: %d scanned -> %d to index.", len(all_files), len(to_index))
        return to_index, skipped, new_c, changed_c

    async def _process_file_change(self, fp, tag, mtime, size, change_map, loop) -> str:
        """
        Determine whether a file should be indexed as `new`, `changed`, or `skipped`. If the file has a stored record with a different modification time but identical SHA256, update the stored metadata (size, modified time, type, folder tag, sha256) and treat it as `skipped`.
        
        Parameters:
            fp (Path): Path to the file being evaluated.
            tag (str): Folder tag associated with the file.
            mtime (str | None): Current file modification timestamp (ISO format); `None` indicates unreadable file.
            size (int): Current file size in bytes.
            change_map (Dict[str, Tuple[str, str]]): Mapping from absolute file path to a tuple `(stored_mtime, stored_sha256)`.
            loop (asyncio.AbstractEventLoop): Event loop used to run blocking SHA256 calculation in an executor.
        
        Returns:
            str: `"new"` if the file is not in `change_map`, `"changed"` if the file exists but content SHA differs, or `"skipped"` if no indexing is needed (including the case where stored SHA matches and DB metadata is updated).
        """
        if mtime is None: return "skipped"
        abs_p = str(fp.absolute())
        stored = change_map.get(abs_p)
        if stored and stored[0] == mtime: return "skipped"
        if stored:
            curr_sha = await loop.run_in_executor(None, self._calculate_sha256, fp)
            if stored[1] == curr_sha:
                await self.db.execute_write("UPDATE files SET size=?, modified_at=?, type=?, folder_tag=?, sha256=? WHERE path=?", (size, mtime, fp.suffix.lower(), tag, curr_sha, abs_p))
                return "skipped"
            return "changed"
        return "new"

    def _calculate_sha256(self, path: Path) -> str:
        """
        Compute the SHA256 digest for a file, using a sampled strategy for very large files.
        
        Parameters:
            path (Path): Path to the file to hash.
        
        Returns:
            str: The hexadecimal SHA256 digest. For files larger than 100 MB the digest is prefixed with
            "sampled_" (sampling uses head, middle, and tail regions). Returns an empty string on error.
        """
        try:
            stat = path.stat()
            # If it's a huge binary/data file, don't read the whole thing for a hash.
            # Sampled hash: first 1MB + middle 1MB + last 1MB.
            if stat.st_size > 100 * 1024 * 1024:
                hasher = hashlib.sha256()
                with open(path, "rb") as f:
                    # Head
                    hasher.update(f.read(1024 * 1024))
                    # Mid
                    f.seek(stat.st_size // 2)
                    hasher.update(f.read(1024 * 1024))
                    # Tail
                    f.seek(max(0, stat.st_size - 1024 * 1024))
                    hasher.update(f.read(1024 * 1024))
                return "sampled_" + hasher.hexdigest()

            if RUST_CORE_AVAILABLE:
                try: return rust_core.calculate_sha256(str(path))
                except Exception: pass
            
            hasher = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1048576), b""): hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return ""

    def _is_binary(self, path: Path) -> bool:
        """
        Detects whether a file should be treated as binary by sampling its bytes.
        
        Reads up to the first 8192 bytes of the file, treats the presence of a null byte as binary,
        and classifies the file as binary when more than 30% of sampled bytes are non-text.
        On any read error or exception, the function conservatively returns `True`.
        
        Returns:
            bool: `True` if the file is likely binary, `false` otherwise.
        """
        try:
            with open(path, "rb") as f:
                # Check first 8KB for nulls or high density of non-ASCII
                chunk = f.read(8192)
                if not chunk: return False
                if b"\x00" in chunk: return True
                
                # If more than 30% of the sample is non-printable, it's likely binary
                text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)))
                non_text = sum(1 for b in chunk if b not in text_chars)
                return (non_text / len(chunk)) > 0.3
        except Exception:
            return True

    def _extract_text(self, path: Path) -> str:
        """
        Extract text from the file at `path` using a specialized extractor when available, otherwise fall back to plain-text extraction or a short binary/asset stub.
        
        When a specialized extractor (PDF, DOCX, CSV, JSON) is available this function runs it with a timeout; on timeout or extractor failure it returns an empty string. For known text-like extensions it returns the file's plain-text contents unless the file is detected as binary, in which case it returns a short "[BINARY: <name>]" stub including size. For known Unreal binary asset extensions it returns a descriptive asset stub. For unknown extensions the function returns either a "[UNKNOWN BINARY: <name>]" stub for binary files or the plain-text contents for non-binary files.
        
        Returns:
            str: Extracted text, a short human-readable stub for binary or unsupported asset files, or an empty string if extraction failed or timed out.
        """
        ext = path.suffix.lower()
        extractor = {".pdf": self._extract_pdf, ".docx": self._extract_docx, ".csv": self._extract_csv, ".json": self._extract_json}.get(ext)
        
        if not extractor:
            if ext in IndexingService._TEXT_EXTENSIONS or ext in IndexingService._UNREAL_PROJECT_EXTENSIONS:
                if self._is_binary(path):
                    size_mb = path.stat().st_size / (1024 * 1024)
                    return f"[BINARY: {path.name}] Size: {size_mb:.2f} MB. Binary content not indexed."
                return self._extract_plain_text(path)
            if ext in IndexingService._UNREAL_BINARY_EXTENSIONS: return self._extract_unreal_asset_stub(path)
            
            # Fallback for unknown extensions: check if binary
            if self._is_binary(path):
                return f"[UNKNOWN BINARY: {path.name}] Binary content not indexed."
            return self._extract_plain_text(path)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(extractor, path)
            try: return fut.result(timeout=settings.gemini_timeout)
            except Exception: return ""

    def _extract_plain_text(self, path: Path) -> str:
        """
        Read a file as UTF-8 and return its contents truncated to the instance's max_file_size.
        
        Returns:
            The file text truncated to `self.max_file_size` characters, or an empty string if the file cannot be read.
        """
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f: return f.read(self.max_file_size)
        except Exception: return ""

    def _extract_pdf(self, path: Path) -> str:
        """
        Extract readable text from a PDF file and return up to self.max_file_size characters.
        
        Parameters:
            path (Path): Path to the PDF file to extract.
        
        Returns:
            str: Extracted text truncated to self.max_file_size. If the PDF is password-protected, returns a short message indicating encryption. If extraction fails for any reason (including missing or failing `fitz`), returns an empty string.
        """
        try:
            import fitz
            content, total = [], 0
            with fitz.open(path) as doc:
                if doc.is_encrypted:
                    return f"[ENCRYPTED PDF: {path.name}] Cannot extract text from password-protected file."
                for page in doc:
                    txt = page.get_text()
                    if txt:
                        content.append(txt); total += len(txt)
                        if total > self.max_file_size: break
            return "\n".join(content)[:self.max_file_size]
        except Exception: return ""

    def _extract_docx(self, path: Path) -> str:
        """
        Extracts plain text from a .docx file, truncated to the service's max_file_size.
        
        Parameters:
            path (Path): Path to the .docx file.
        
        Returns:
            str: The extracted text truncated to self.max_file_size. If the file is password-protected or encrypted, returns a message in the form "[ENCRYPTED DOCX: <filename>] Cannot extract text from password-protected file." On other extraction failures, returns an empty string.
        """
        try:
            from docx import Document
            # docx might throw exceptions for encrypted files
            doc = Document(str(path))
            paras, total = [], 0
            for p in doc.paragraphs:
                if p.text.strip():
                    paras.append(p.text); total += len(p.text)
                    if total > self.max_file_size: break
            return "\n".join(paras)[:self.max_file_size]
        except Exception as e:
            err_msg = str(e).lower()
            if "encrypted" in err_msg or "password" in err_msg:
                return f"[ENCRYPTED DOCX: {path.name}] Cannot extract text from password-protected file."
            return ""

    def _extract_csv(self, path: Path) -> str:
        """
        Extract plain text from a CSV file by reading its rows and joining them.
        
        Parameters:
            path (Path): Path to the CSV file to read.
        
        Returns:
            csv_text (str): The first up to 5,001 rows of the file joined by newline characters; each row is represented as its columns joined with ", ". Returns an empty string if reading or parsing fails.
        """
        try:
            import csv
            rows = []
            with open(path, encoding="utf-8", errors="replace", newline="") as f:
                reader = csv.reader(f)
                for i, row in enumerate(reader):
                    if i > 5000: break
                    rows.append(", ".join(row))
            return "\n".join(rows)
        except Exception: return ""

    def _extract_json(self, path: Path) -> str:
        """
        Extract JSON content from a file and return a pretty-printed representation or a safe fallback.
        
        Attempts to read the file using UTF-8 with BOM support and returns a prettified JSON string (indent=2, ensure_ascii=False) truncated to 200000 characters. If parsing fails, it makes a best-effort fix by removing trailing commas before `}` or `]` and retries parsing. If parsing still fails, returns the raw file text truncated to 200000 characters. If the file cannot be read, returns an empty string.
        
        Parameters:
            path (Path): Path to the JSON file to read.
        
        Returns:
            str: Pretty-printed JSON when parsing succeeds, the raw truncated file text if parsing fails, or an empty string on read error.
        """
        import json, re
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f: text = f.read(self.max_file_size)
            try: return json.dumps(json.loads(text), indent=2, ensure_ascii=False)[:200000]
            except Exception: pass
            try: return json.dumps(json.loads(re.sub(r',\s*([}\]])', r'\1', text)), indent=2, ensure_ascii=False)[:200000]
            except Exception: pass
            return text[:200000]
        except Exception: return ""

    @staticmethod
    def _extract_unreal_asset_stub(path: Path) -> str:
        """
        Create a human-readable stub message for Unreal Engine binary asset files.
        
        Returns:
            str: A message indicating the file is an Unreal Engine binary (either "asset" or "map"), including the filename, full path, a note that binary content is not parsed, and an additional contextual hint when the path suggests level/environment, character, or rendering/VFX content.
        """
        lower = str(path).lower().replace("\\", "/")
        kind = "map" if path.suffix.lower() == ".umap" else "asset"
        hint = ""
        if any(s in lower for s in ["/maps/", "/levels/"]): hint = " Environment/Level content."
        elif any(s in lower for s in ["/characters/", "/player/", "/npc/"]): hint = " Character-related content."
        elif any(s in lower for s in ["/materials/", "/niagara/"]): hint = " Rendering/VFX content."
        return f"Unreal Engine binary {kind}: {path.name}. Path: {path}. Binary content not parsed directly.{hint}"

    def _generate_summary(self, text: str, path: Path, max_chars: int = 300) -> str:
        """
        Create a short summary for a file that begins with a file-type tag and an initial text snippet.
        
        Returns:
            str: A string beginning with `[EXT: filename] ` where `EXT` is the file extension (or `file` if none), followed by the input text trimmed to at most `max_chars` characters and cut at the nearest sentence boundary when possible.
        """
        fname = path.name
        ftype = path.suffix.lstrip(".").upper() or "file"
        raw = text[:max_chars + 80]
        boundary = self._find_sentence_boundary(raw, min(max_chars, len(raw)))
        return f"[{ftype}: {fname}] " + raw[:boundary].strip()

    @staticmethod
    def _find_sentence_boundary(text: str, pos: int, window: int = 80) -> int:
        """
        Finds a sentence boundary at or before a given position within a limited backward window.
        
        Parameters:
            text (str): Source text to search.
            pos (int): Character index in `text` where a boundary search is centered (searches backward from this position).
            window (int): Maximum number of characters to look backward from `pos` when searching for a sentence boundary.
        
        Returns:
            int: Index in `text` at which a sentence boundary ends (the split point). If no suitable boundary is found within `window`, returns `pos`.
        """
        if RUST_CORE_AVAILABLE:
            try: return rust_core.find_sentence_boundary(text, pos, window)
            except Exception: pass
        search_start = max(0, pos - window)
        region = text[search_start:pos]
        for delim in ["\n\n", ". ", "! ", "? ", ".\n", "!\n", "?\n"]:
            idx = region.rfind(delim)
            if idx != -1: return search_start + idx + len(delim)
        return pos

    def _create_chunks(self, text: str, file_path: str = "") -> List[Dict[str, Any]]:
        """
        Split extracted file text into indexing chunks, using Markdown-aware sectioning for Markdown files and falling back to general text chunking.
        
        Parameters:
            text: The extracted full text of the file to split into chunks.
            file_path: The original file path used to build a context prefix included in each chunk's `text_preview`.
        
        Returns:
            A list of chunk dictionaries. Each chunk contains at least `start_offset`, `end_offset`, and `text_preview` keys describing the slice of text and a preview string that includes a context prefix.
        """
        if not text: return []
        prefix = self._build_context_prefix(file_path)
        if Path(file_path).suffix.lower() == ".md":
            chunks = self._chunk_markdown(text, prefix)
            if chunks: return chunks
        return self._split_text(text, prefix, 0)

    @staticmethod
    def _build_context_prefix(file_path: str) -> str:
        """
        Builds a short context prefix identifying the file by extension and name.
        
        Returns:
            A string in the format "[EXT: filename] " where EXT is the file suffix (no leading dot) in uppercase, or "file" when the path has no suffix.
        """
        p = Path(file_path)
        return f"[{p.suffix.lstrip('.').upper() or 'file'}: {p.name}] "

    def _chunk_markdown(self, text: str, prefix: str) -> List[Dict[str, Any]]:
        """
        Split a Markdown document into chunks guided by top-level headings and produce preview metadata for each chunk.
        
        Parameters:
            text (str): Full Markdown text to split.
            prefix (str): Context prefix to prepend to each chunk's `text_preview`.
        
        Returns:
            List[Dict[str, Any]]: Ordered list of chunk dictionaries. Each chunk contains:
                - `start_offset` (int): character index in `text` where the chunk starts.
                - `end_offset` (int): character index in `text` where the chunk ends.
                - `text_preview` (str): `prefix` followed by the chunk text (trimmed).
        Behavior:
            Splits `text` at Markdown headings of level 1–3. For each section whose length is
            less than or equal to `self.chunk_size` a single chunk is produced; longer sections
            are further split using `self._split_text(section, prefix, start)`.
        """
        import re
        sections = [s for s in re.split(r'(?=^#{1,3}\s)', text, flags=re.MULTILINE) if s.strip()]
        chunks, offset = [], 0
        for sec in sections:
            start = text.find(sec, offset)
            if start == -1: start = offset
            if len(sec) <= self.chunk_size: chunks.append({"start_offset": start, "end_offset": start + len(sec), "text_preview": prefix + sec.strip()})
            else: chunks.extend(self._split_text(sec, prefix, start))
            offset = start + len(sec)
        return chunks

    def _split_text(self, text: str, prefix: str, base_offset: int) -> List[Dict[str, Any]]:
        """
        Split the input text into overlapping chunks, preferring cuts at sentence boundaries.
        
        Parameters:
            text (str): The full text to split.
            prefix (str): A short string prefixed to each chunk's `text_preview` (typically context like file/tag).
            base_offset (int): Value added to each chunk's start/end offsets to produce absolute positions.
        
        Returns:
            List[Dict[str, Any]]: A list of chunk dictionaries with keys:
                - `start_offset` (int): Absolute start character index of the chunk.
                - `end_offset` (int): Absolute end character index of the chunk.
                - `text_preview` (str): The chunk text prefixed with `prefix`.
        """
        if RUST_CORE_AVAILABLE:
            try:
                # The Rust implementation returns a list of dicts directly
                return rust_core.create_chunks(
                    text, 
                    self.chunk_size, 
                    self.chunk_overlap, 
                    prefix, 
                    base_offset
                )
            except (Exception, BaseException) as e:
                logger.warning("Rust create_chunks failed, falling back to python: %s", e)
                
        chunks, start, text_len = [], 0, len(text)
        while start < text_len:
            raw_end = min(start + self.chunk_size, text_len)
            end = self._find_sentence_boundary(text, raw_end) if raw_end < text_len else raw_end
            if end <= start: end = raw_end
            chunks.append({
                "start_offset": base_offset + start, 
                "end_offset": base_offset + end, 
                "text_preview": prefix + text[start:end]
            })
            
            next_start = end - self.chunk_overlap if end < text_len else text_len
            # Ensure we always advance by at least 1 character to avoid infinite loops
            start = max(start + 1, next_start)
        return chunks
