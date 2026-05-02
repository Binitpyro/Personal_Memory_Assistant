import asyncio
import hashlib
import logging
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.embeddings.service import EmbeddingService
from app.indexing.code_chunker import CodeChunker
from app.indexing.extractors import EXTRACTORS
from app.indexing.folder_profiler import (
    generate_folder_profiles_async,
)
from app.indexing.folder_profiler import (
    resolve_folder_overlaps as _resolve_folder_overlaps,
)
from app.indexing.summarizer import generate_deep_summary
from app.project_constants import (
    TEXT_EXTENSIONS,
    UNREAL_BINARY_EXTENSIONS,
    UNREAL_PROJECT_EXTENSIONS,
)
from app.scanner.scanner import scan_folder as fast_scan
from app.storage.db import DatabaseManager

try:
    import rust_core

    RUST_CORE_AVAILABLE = True
except ImportError:
    RUST_CORE_AVAILABLE = False

logger = logging.getLogger(__name__)


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


class StreamChunker:
    """Helper to process a stream of text fragments into properly sized chunks."""

    def __init__(self, chunk_size: int, chunk_overlap: int, prefix: str):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.prefix = prefix
        self.buffer = ""
        self.total_offset = 0

    def process(self, text_fragment: str) -> list[dict[str, Any]]:
        self.buffer += text_fragment
        chunks = []

        while len(self.buffer) > self.chunk_size:
            # Find a good split point in the current window
            raw_end = self.chunk_size
            # Use simple sentence snapping for streaming
            end = self._find_boundary(self.buffer, raw_end)

            chunk_text = self.buffer[:end]
            chunks.append(
                {
                    "start_offset": self.total_offset,
                    "end_offset": self.total_offset + end,
                    "text_preview": self.prefix + chunk_text,
                }
            )

            # Advance
            overlap_start = max(0, end - self.chunk_overlap)
            self.buffer = self.buffer[overlap_start:]
            self.total_offset += overlap_start

        return chunks

    def finalize(self) -> list[dict[str, Any]]:
        """Process any remaining text in the buffer."""
        chunks = []
        if self.buffer.strip():
            chunks.append(
                {
                    "start_offset": self.total_offset,
                    "end_offset": self.total_offset + len(self.buffer),
                    "text_preview": self.prefix + self.buffer,
                }
            )
        self.buffer = ""
        return chunks

    @staticmethod
    def _find_boundary(text: str, pos: int) -> int:
        # Simplified boundary finding for streaming
        search_start = max(0, pos - 100)
        region = text[search_start:pos]
        for delim in ["\n\n", ". ", "! ", "? ", ".\n", "!\n", "?\n"]:
            idx = region.rfind(delim)
            if idx != -1:
                return search_start + idx + len(delim)
        return pos


class IndexingService:
    def __init__(
        self,
        db: DatabaseManager,
        embedding_service: EmbeddingService,
        lancedb_client: Any,
    ):
        self.db = db
        self.embedding_service = embedding_service
        self.lancedb_client = lancedb_client
        self.supported_extensions = settings.extensions_set
        self.chunk_size = settings.chunk_size
        self.chunk_overlap = settings.chunk_overlap
        self.max_file_size = settings.max_file_size_bytes
        self._concurrency = settings.index_concurrency
        self.code_chunker = CodeChunker(max_tokens=512)

    async def index_folders(self, folders: list[str]):
        if indexing_lock.locked():
            logger.warning("Indexing already in progress.")
            return

        async with indexing_lock:
            unique_folders = _resolve_folder_overlaps(folders)
            if not unique_folders:
                progress.status = "idle"
                return

            progress.reset(0)
            progress.status = "running"
            progress.current_file = "Scanning folders…"

            loop = asyncio.get_running_loop()
            all_files, scan_method, scan_duration = await loop.run_in_executor(
                None, self._scan_all_folders, unique_folders
            )

            if not all_files:
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
                await self._generate_folder_profiles(all_files, unique_folders)
                progress.complete()
                return

            batch_size = 1500
            for i in range(0, len(files_to_index), batch_size):
                batch = files_to_index[i : i + batch_size]
                await self._batch_index_pipeline(
                    batch, offset=i, total_to_index=len(files_to_index)
                )
                import gc

                gc.collect()

            await self._generate_folder_profiles(all_files, unique_folders)
            from app.search.retrieval import clear_retrieval_cache

            clear_retrieval_cache()

            task = asyncio.create_task(self.db.wal_checkpoint())
            from app import state

            state.bg_tasks.add(task)
            task.add_done_callback(state.bg_tasks.discard)

            progress.complete()

    async def _batch_index_pipeline(
        self, files_to_index: list[tuple[Path, str]], offset: int = 0, total_to_index: int = 0
    ) -> None:
        batch_total = len(files_to_index)
        total_so_far = offset
        grand_total = total_to_index or batch_total

        progress.set_current_file(
            f"Pipelined Indexing: {batch_total} files (Batch {offset}/{grand_total})…"
        )

        pre_extracted = await self._rust_pre_extract(files_to_index)

        embed_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=1000)
        store_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=1000)

        await asyncio.gather(
            self._extractor_worker(
                files_to_index, pre_extracted, embed_queue, total_so_far, grand_total
            ),
            self._embedder_worker(embed_queue, store_queue),
            self._storer_worker(store_queue),
        )

    async def _rust_pre_extract(self, files_to_index: list[tuple[Path, str]]) -> dict[str, str]:
        pre_extracted: dict[str, str] = {}
        if not RUST_CORE_AVAILABLE:
            return pre_extracted

        rust_paths = []
        for fp, _ in files_to_index:
            ext = fp.suffix.lower()
            if ext in TEXT_EXTENSIONS and ext not in [".json", ".csv"]:
                rust_paths.append(str(fp.absolute()))

        if rust_paths:
            loop = asyncio.get_running_loop()
            try:
                rust_results = await loop.run_in_executor(
                    None, rust_core.extract_text_files, rust_paths, self.max_file_size
                )
                for p_str, txt in rust_results:
                    pre_extracted[p_str] = txt
            except Exception as e:
                logger.warning("Rust bulk extraction failed: %s", e)
        return pre_extracted

    async def _extractor_worker(
        self, files_to_index, pre_extracted, embed_queue, total_so_far, grand_total
    ):
        extracted_count = 0
        extracted_lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(self._concurrency * 2)

        async def _safe_stream_extract(path: Path, tag: str):
            nonlocal extracted_count
            async with semaphore:
                cached_text = pre_extracted.get(str(path.absolute()))
                async with extracted_lock:
                    extracted_count += 1
                    overall = total_so_far + extracted_count
                    progress.set_current_file(f"Extracting: {path.name} ({overall}/{grand_total})")

                await self._stream_extract_and_prepare(path, tag, cached_text, embed_queue)

        tasks = [_safe_stream_extract(fp, ft) for fp, ft in files_to_index]
        await asyncio.gather(*tasks)
        await embed_queue.put(None)

    async def _stream_extract_and_prepare(
        self, path: Path, folder_tag: str, pre_text: str | None, queue: asyncio.Queue
    ) -> None:
        loop = asyncio.get_running_loop()
        try:
            stat = await loop.run_in_executor(None, path.stat)
            sha256 = await loop.run_in_executor(None, self._calculate_sha256, path)

            header = {
                "type": "header",
                "path": path,
                "folder_tag": folder_tag,
                "file_data": {
                    "path": str(path.absolute()),
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "type": path.suffix.lower(),
                    "folder_tag": folder_tag,
                    "sha256": sha256,
                },
            }
            await queue.put(header)

            prefix = self._build_context_prefix(str(path))
            is_structured = any(ex.can_handle(path) for ex in EXTRACTORS)
            is_large_log = path.suffix.lower() == ".log" and stat.st_size > 5 * 1024 * 1024

            full_text_for_summary = ""

            if is_structured or is_large_log:
                chunker = StreamChunker(self.chunk_size, self.chunk_overlap, prefix)

                def _get_stream():
                    for ex in EXTRACTORS:
                        if ex.can_handle(path):
                            return ex.extract_stream(path, self.max_file_size)
                    return self._extract_plain_text_stream(path)

                stream = await loop.run_in_executor(None, _get_stream)
                for fragment in stream:
                    chunks = chunker.process(fragment)
                    for c in chunks:
                        await queue.put({"type": "chunk", "path": path, "chunk": c})
                    if len(full_text_for_summary) < 2000:
                        full_text_for_summary += fragment
                for c in chunker.finalize():
                    await queue.put({"type": "chunk", "path": path, "chunk": c})
            else:
                text = (
                    pre_text
                    if pre_text is not None
                    else await loop.run_in_executor(None, self._extract_text_monolithic, path)
                )
                chunks = self._create_chunks(text, file_path=str(path))
                for c in chunks:
                    await queue.put({"type": "chunk", "path": path, "chunk": c})
                full_text_for_summary = text

            summary = self._generate_summary(full_text_for_summary, path)
            await queue.put({"type": "footer", "path": path, "summary": summary})

        except Exception as e:
            logger.error("Streaming extraction failed for %s: %s", path, e)

    def _extract_plain_text_stream(self, path: Path) -> Iterator[str]:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                while True:
                    chunk = f.read(128 * 1024)
                    if not chunk:
                        break
                    yield chunk
        except Exception:
            return

    async def _embedder_worker(
        self,
        embed_queue: asyncio.Queue[dict[str, Any] | None],
        store_queue: asyncio.Queue[dict[str, Any] | None],
    ):
        chunk_batch: list[dict[str, Any]] = []
        while True:
            item = await embed_queue.get()
            if item is None:
                if chunk_batch:
                    await self._process_embed_stream_batch(chunk_batch)
                    for c in chunk_batch:
                        await store_queue.put(c)
                await store_queue.put(None)
                break

            if item["type"] == "chunk":
                chunk_batch.append(item)
                if len(chunk_batch) >= 100:
                    await self._process_embed_stream_batch(chunk_batch)
                    for c in chunk_batch:
                        await store_queue.put(c)
                    chunk_batch.clear()
            else:
                # Header/Footer: Flush batch first to preserve order
                if chunk_batch:
                    await self._process_embed_stream_batch(chunk_batch)
                    for c in chunk_batch:
                        await store_queue.put(c)
                    chunk_batch.clear()
                await store_queue.put(item)

    async def _process_embed_stream_batch(self, batch_items: list[dict[str, Any]]):
        texts = [item["chunk"]["text_preview"] for item in batch_items]
        if not texts:
            return

        def report_progress(batch_num, total_batches):
            progress.set_current_file(f"Phase 2/3: Embedding chunks ({batch_num}/{total_batches})…")

        all_embeddings = await self.embedding_service.embed_texts(
            texts, progress_callback=report_progress
        )
        for idx, item in enumerate(batch_items):
            item["chunk"]["_embedding"] = all_embeddings[idx]

    async def _storer_worker(self, store_queue: asyncio.Queue[dict[str, Any] | None]):
        active_files: dict[str, dict[str, Any]] = {}
        pending_chunks: list[dict[str, Any]] = []

        while True:
            item = await store_queue.get()
            if item is None:
                if pending_chunks:
                    await self._flush_pending_chunks(pending_chunks, active_files)
                break

            ptype, path_str = item["type"], str(item["path"].absolute())

            if ptype == "header":
                file_id = await self.db.batch_insert_files([item["file_data"]])
                active_files[path_str] = {
                    "id": file_id[0],
                    "data": item["file_data"],
                    "chunk_count": 0,
                }
                await self._delete_existing_chunks(file_id[0])
            elif ptype == "chunk":
                file_info = active_files.get(path_str)
                if file_info:
                    item["file_id"] = file_info["id"]
                    pending_chunks.append(item)
                    file_info["chunk_count"] += 1
                    if len(pending_chunks) >= 200:
                        await self._flush_pending_chunks(pending_chunks, active_files)
                        pending_chunks.clear()
            elif ptype == "footer":
                file_info = active_files.pop(path_str, None)
                if file_info:
                    await self.db.execute_write(
                        "UPDATE files SET summary = ? WHERE id = ?",
                        (item["summary"], file_info["id"]),
                    )
                    progress.update(file_info["chunk_count"], current_file=item["path"].name)

    async def _flush_pending_chunks(self, chunks: list[dict[str, Any]], active_files: dict):
        if not chunks:
            return
        import numpy as np

        chunk_rows = []
        for item in chunks:
            row = {k: v for k, v in item["chunk"].items() if k != "_embedding"}
            row["file_id"] = item["file_id"]
            chunk_rows.append(row)

        chunk_ids_int = await self.db.insert_chunks_bulk(chunk_rows)

        l_ids, l_embs, l_metas, emb_blobs = [], [], [], []
        for chunk_id, item in zip(chunk_ids_int, chunks, strict=False):
            cid_str = str(chunk_id)
            l_ids.append(cid_str)
            l_embs.append(item["chunk"]["_embedding"])
            l_metas.append(
                {
                    "chunk_id": cid_str,
                    "file_path": item["path"].absolute().as_posix(),
                    "folder_tag": active_files.get(str(item["path"].absolute()), {})
                    .get("data", {})
                    .get("folder_tag", ""),
                }
            )
            emb_blobs.append(
                (chunk_id, np.array(item["chunk"]["_embedding"], dtype=np.float16).tobytes())
            )

        await self.db.insert_chunk_embeddings_bulk(emb_blobs)
        await self.lancedb_client.add_documents(l_ids, l_embs, l_metas)
        await self.db.commit()

    async def _delete_existing_chunks(self, file_id: int) -> None:
        old_chunks = await self.db.get_file_chunks(file_id)
        old_ids = [str(chunk["id"]) for chunk in old_chunks]
        if old_ids:
            await self.lancedb_client.delete_documents(old_ids)
        await self.db.delete_file_chunks(file_id, auto_commit=False)

    async def _generate_folder_profiles(self, all_files, folders) -> None:
        profiles = await generate_folder_profiles_async(all_files, folders)
        for p in profiles:
            await self.db.upsert_folder_profile(p, auto_commit=False)
        await self.db.commit()
        profile_texts = [p["profile_text"] for p in profiles]
        if profile_texts:
            embs = await self.embedding_service.embed_texts(profile_texts)
            summaries = [
                {
                    "doc_id": f"folder_profile_{p['folder_tag']}",
                    "embedding": e,
                    "metadata": {
                        "file_path": p["folder_path"],
                        "folder_tag": p["folder_tag"],
                        "is_folder_profile": "true",
                    },
                }
                for p, e in zip(profiles, embs, strict=False)
            ]
            await self.lancedb_client.add_summaries_batch(summaries)

    def _extract_text_monolithic(self, path: Path) -> str:
        ext = path.suffix.lower()
        if ext in TEXT_EXTENSIONS or ext in UNREAL_PROJECT_EXTENSIONS:
            if self._is_binary(path):
                return f"[BINARY: {path.name}] content not indexed."
            return self._extract_plain_text(path)
        if ext in UNREAL_BINARY_EXTENSIONS:
            return self._extract_unreal_asset_stub(path)
        return self._extract_plain_text(path)

    def _extract_plain_text(self, path: Path) -> str:
        limit = (
            5 * 1024 * 1024
            if path.suffix.lower() in {".py", ".ts", ".js", ".rs", ".go"}
            else self.max_file_size
        )
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read(limit)
        except Exception:
            return ""

    def _scan_all_folders(
        self, unique_folders: list[Path]
    ) -> tuple[list[tuple[Path, str]], str, float]:
        if RUST_CORE_AVAILABLE:
            return self._scan_all_folders_rust(unique_folders)
        return self._scan_all_folders_python(unique_folders)

    def _scan_all_folders_rust(
        self, unique_folders: list[Path]
    ) -> tuple[list[tuple[Path, str]], str, float]:
        import time

        t0 = time.perf_counter()
        folder_strs = [str(f) for f in unique_folders]
        resolved_folders = [(f.resolve(), f.name) for f in unique_folders]
        try:
            rust_paths = rust_core.scan_folders(folder_strs, list(self.supported_extensions))
            all_files = []
            for path_str in rust_paths:
                p_obj = Path(path_str)
                tag = "Unknown"
                for f_res, f_name in resolved_folders:
                    try:
                        p_obj.relative_to(f_res)
                        tag = f_name
                        break
                    except ValueError:
                        pass
                all_files.append((p_obj, tag))
            return all_files, "rust_jwalk", (time.perf_counter() - t0) * 1000
        except Exception:
            return self._scan_all_folders_python(unique_folders)

    def _scan_all_folders_python(
        self, unique_folders: list[Path]
    ) -> tuple[list[tuple[Path, str]], str, float]:
        all_files, seen_paths = [], set()
        scan_dur = 0.0
        for f in unique_folders:
            res = fast_scan(f, self.supported_extensions)
            scan_dur += res.duration_ms
            for fp in res.files:
                abs_p = str(fp.resolve())
                if abs_p not in seen_paths:
                    seen_paths.add(abs_p)
                    all_files.append((fp, f.name))
        return all_files, "scandir", scan_dur

    async def _detect_changes(
        self, all_files: list[tuple[Path, str]]
    ) -> tuple[list[tuple[Path, str]], int, int, int]:
        file_paths = [str(fp.absolute()) for fp, _ in all_files]
        change_map = await self.db.get_files_change_map(file_paths)
        to_index, skipped, new_c, changed_c = [], 0, 0, 0
        for fp, tag in all_files:
            try:
                stat = fp.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
                stored = change_map.get(str(fp.absolute()))
                if stored and stored[0] == mtime:
                    skipped += 1
                elif stored:
                    changed_c += 1
                    to_index.append((fp, tag))
                else:
                    new_c += 1
                    to_index.append((fp, tag))
            except OSError:
                skipped += 1
        return to_index, skipped, new_c, changed_c

    def _calculate_sha256(self, path: Path) -> str:
        try:
            stat = path.stat()
            if stat.st_size > 100 * 1024 * 1024:
                return f"sampled_{stat.st_size}"
            hasher = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1048576), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return ""

    def _is_binary(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                chunk = f.read(8192)
                return b"\x00" in chunk
        except Exception:
            return True

    @staticmethod
    def _extract_unreal_asset_stub(path: Path) -> str:
        return f"Unreal Engine binary asset: {path.name}."

    def _generate_summary(self, text: str, path: Path, max_chars: int = 300) -> str:
        return generate_deep_summary(text, path, max_chars)

    def _create_chunks(self, text: str, file_path: str = "") -> list[dict[str, Any]]:
        if not text:
            return []
        prefix = self._build_context_prefix(file_path)
        return self._split_text(text, prefix, 0)

    @staticmethod
    def _build_context_prefix(file_path: str) -> str:
        p = Path(file_path)
        return f"[{p.suffix.lstrip('.').upper() or 'file'}: {p.name}] "

    def _split_text(self, text: str, prefix: str, base_offset: int) -> list[dict[str, Any]]:
        chunks, start, text_len = [], 0, len(text)
        while start < text_len:
            end = min(start + self.chunk_size, text_len)
            chunks.append(
                {
                    "start_offset": base_offset + start,
                    "end_offset": base_offset + end,
                    "text_preview": prefix + text[start:end],
                }
            )
            start = end - self.chunk_overlap if end < text_len else text_len
            if start < 0:
                start = 0
            if end >= text_len:
                break
        return chunks
