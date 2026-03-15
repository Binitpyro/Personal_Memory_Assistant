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
    """Infer the project type from file extensions, filenames, and directories."""
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
        with self._lock:
            self.processed_files += 1
            self.total_chunks += chunks_added
            if current_file:
                self.current_file = current_file

    def set_current_file(self, current_file: str):
        with self._lock:
            self.current_file = current_file

    def complete(self):
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
        self.db = db
        self.embedding_service = embedding_service
        self.chroma_client = chroma_client
        self.supported_extensions = settings.extensions_set
        self.chunk_size = settings.chunk_size
        self.chunk_overlap = settings.chunk_overlap
        self.max_file_size = settings.max_file_size_bytes
        self._concurrency = settings.index_concurrency

    async def index_folders(self, folders: List[str]):
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
        batch_total = len(files_to_index)
        total_so_far = offset
        grand_total = total_to_index or batch_total
        
        logger.info("Pipeline phase 1/3: extracting text from %d files … (Batch %d-%d)", batch_total, offset, offset + batch_total)
        progress.set_current_file(f"Phase 1/3: Extracting {batch_total} files (Batch {offset}/{grand_total})…")
        
        semaphore = asyncio.Semaphore(self._concurrency * 2)
        extracted_count = 0
        extracted_lock = asyncio.Lock()

        async def _safe_extract(path: Path, tag: str):
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
        for idx, (file_idx, kind, sub_idx) in enumerate(text_map):
            if kind == "chunk":
                prepared[file_idx]["chunks"][sub_idx]["_embedding"] = all_embeddings[idx]
            else:
                prepared[file_idx]["_summary_embedding"] = all_embeddings[idx]

    async def _store_prepared_items(self, prepared: List[Dict[str, Any]]) -> None:
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
        old_chunks = await self.db.get_file_chunks(file_id)
        old_ids = [str(chunk["id"]) for chunk in old_chunks]
        if old_ids: await self.chroma_client.delete_documents(old_ids)
        await self.db.delete_file_chunks(file_id, auto_commit=False)

    @staticmethod
    def _build_chunk_rows(chunks: List[Dict[str, Any]], file_id: int) -> List[Dict[str, Any]]:
        rows = [{k: v for k, v in c.items() if k != "_embedding"} for c in chunks]
        for r in rows: r["file_id"] = file_id
        return rows

    async def _flush_chroma_batches(self, ids, embs, metas, summaries) -> None:
        tasks = []
        for i in range(0, len(ids), 5000):
            end = i + 5000
            tasks.append(self.chroma_client.add_documents(ids[i:end], embs[i:end], metas[i:end]))
        if summaries: tasks.append(self.chroma_client.add_summaries_batch(summaries))
        if tasks: await asyncio.gather(*tasks)

    async def _generate_folder_profiles(self, all_files, folders) -> None:
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
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._extract_and_prepare, path, folder_tag)
        except Exception as e:
            logger.error("Error preparing %s: %s", path, e)
            progress.update(0)
            return None

    def _extract_and_prepare(self, path: Path, folder_tag: str) -> Optional[Dict[str, Any]]:
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
        if RUST_CORE_AVAILABLE:
            return self._scan_all_folders_rust(unique_folders)
        return self._scan_all_folders_python(unique_folders)

    def _scan_all_folders_rust(self, unique_folders: List[Path]) -> Tuple[List[Tuple[Path, str]], str, float]:
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
        all_files, seen_paths = [], set()
        scan_method, scan_duration = "", 0.0
        def _scan_one(folder: Path): return folder, fast_scan(folder, self.supported_extensions)
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
        file_paths = [str(fp.absolute()) for fp, _ in all_files]
        change_map = await self.db.get_files_change_map(file_paths)
        loop = asyncio.get_running_loop()

        def _get_info(fp: Path):
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
        """Calculate SHA256 hash. Uses sampled hashing for files > 100MB to prevent hangs."""
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
        """Heuristic to check if a file is binary. Higher sample rate for reliability."""
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
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f: return f.read(self.max_file_size)
        except Exception: return ""

    def _extract_pdf(self, path: Path) -> str:
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
        lower = str(path).lower().replace("\\", "/")
        kind = "map" if path.suffix.lower() == ".umap" else "asset"
        hint = ""
        if any(s in lower for s in ["/maps/", "/levels/"]): hint = " Environment/Level content."
        elif any(s in lower for s in ["/characters/", "/player/", "/npc/"]): hint = " Character-related content."
        elif any(s in lower for s in ["/materials/", "/niagara/"]): hint = " Rendering/VFX content."
        return f"Unreal Engine binary {kind}: {path.name}. Path: {path}. Binary content not parsed directly.{hint}"

    def _generate_summary(self, text: str, path: Path, max_chars: int = 300) -> str:
        fname = path.name
        ftype = path.suffix.lstrip(".").upper() or "file"
        raw = text[:max_chars + 80]
        boundary = self._find_sentence_boundary(raw, min(max_chars, len(raw)))
        return f"[{ftype}: {fname}] " + raw[:boundary].strip()

    @staticmethod
    def _find_sentence_boundary(text: str, pos: int, window: int = 80) -> int:
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
        if not text: return []
        prefix = self._build_context_prefix(file_path)
        if Path(file_path).suffix.lower() == ".md":
            chunks = self._chunk_markdown(text, prefix)
            if chunks: return chunks
        return self._split_text(text, prefix, 0)

    @staticmethod
    def _build_context_prefix(file_path: str) -> str:
        p = Path(file_path)
        return f"[{p.suffix.lstrip('.').upper() or 'file'}: {p.name}] "

    def _chunk_markdown(self, text: str, prefix: str) -> List[Dict[str, Any]]:
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
        """Character-based chunking with sentence-boundary snapping."""
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
