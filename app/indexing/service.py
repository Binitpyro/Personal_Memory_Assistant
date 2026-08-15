import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import settings
from app.embeddings.service import EmbeddingService
from app.indexing.code_chunker import CodeChunker
from app.indexing.extractors import EXTRACTORS, ExtractMeta
from app.indexing.folder_profiler import (
    generate_folder_profiles_async,
)
from app.indexing.folder_profiler import (
    resolve_folder_overlaps as _resolve_folder_overlaps,
)
from app.indexing.summarizer import generate_deep_summary, summary_embedding_text
from app.project_constants import (
    TEXT_EXTENSIONS,
)
from app.scanner.scanner import scan_folder as fast_scan
from app.storage.db import DatabaseManager

try:
    import rust_core  # type: ignore

    RUST_CORE_AVAILABLE = True
except ImportError:
    RUST_CORE_AVAILABLE = False

logger = logging.getLogger(__name__)


def _ensure_nltk_data() -> None:
    """Download NLTK data at startup, not in the hot path."""
    import os

    if os.environ.get("PMA_SENTENCE_OFFSETS", "0") == "0":
        return
    try:
        import nltk

        nltk.data.find("tokenizers/punkt")
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        nltk.download("punkt", quiet=True)
        nltk.download("punkt_tab", quiet=True)


_ensure_nltk_data()
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _get_sentence_offsets(text: str) -> list[list[int]]:
    """Compute start and end offsets for sentences within a text string."""
    import os

    if os.environ.get("PMA_SENTENCE_OFFSETS", "0") == "0":
        return []
    if not text:
        return []

    try:
        import nltk

        sentences = nltk.tokenize.sent_tokenize(text)
        offsets = []
        curr = 0
        for s in sentences:
            start = text.find(s, curr)
            if start == -1:
                start = curr
            end = start + len(s)
            offsets.append([start, end])
            curr = end

        if curr < len(text):
            if offsets:
                offsets[-1][1] = len(text)
            else:
                offsets.append([0, len(text)])
        return offsets
    except Exception as e:
        logger.debug("Failed to use NLTK for sentence segmentation, using fallback: %s", e)
        offsets = []
        matches = list(_SENTENCE_SPLIT_RE.finditer(text))
        curr = 0
        for m in matches:
            end = m.start() + 1
            offsets.append([curr, end])
            curr = m.end()
        if curr < len(text):
            offsets.append([curr, len(text)])
        return offsets


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
        self.is_cancelled = False

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
            self.is_cancelled = False

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


@contextlib.asynccontextmanager
async def _progress_idle_guard():
    """Guarantee `progress.status` returns to "idle" however the run ends.

    The flag is not cosmetic: the OCR drain loop refuses to claim work while it
    is anything else (`app/ocr/manager.py:270`) and `index_ocr_pages` raises on
    it outright (:1012). `index_folders` set it to "running" with nothing
    covering the scan, change-detection and pipeline calls that follow, and its
    caller is an unhandled background task (`app/api/indexing.py:94`) - so a
    single exception in there starved OCR until some *later* run happened to
    complete. With `watcher_enabled` defaulting False, nothing guaranteed one.

    Deliberately not `progress.complete()`: that reports "Complete", which is a
    lie on the failure path.
    """
    try:
        yield
    finally:
        if progress.status != "idle":
            with progress._lock:
                progress.status = "idle"
                progress.current_file = "Interrupted"


# H-18: Dedicated pool for disk-heavy operations to avoid default pool starvation.
_DISK_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="pma-disk")
_EXTRACT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pma-extract")


class StreamChunker:
    """Helper to process a stream of text fragments into properly sized chunks."""

    def __init__(self, chunk_size: int, chunk_overlap: int, prefix: str):
        self.chunk_size = chunk_size if chunk_size > 0 else 500
        self.chunk_overlap = min(max(0, chunk_overlap), self.chunk_size - 1)
        self.prefix = prefix
        self.buffer = ""
        self.total_offset = 0

    def process(self, text_fragment: str) -> list[dict[str, Any]]:
        self.buffer += text_fragment
        chunks = []

        max_iters = len(self.buffer) + 2
        iters = 0
        while len(self.buffer) > self.chunk_size:
            iters += 1
            if iters > max_iters:
                logger.error(
                    "Infinite loop guard triggered in StreamChunker.process! Forcing exit."
                )
                break
            prev_len = len(self.buffer)

            # Find a good split point in the current window
            raw_end = self.chunk_size
            # Use simple sentence snapping for streaming
            end = self._find_boundary(self.buffer, raw_end)

            chunk_text = self.buffer[:end]
            preview = self.prefix + chunk_text
            chunks.append(
                {
                    "start_offset": self.total_offset,
                    "end_offset": self.total_offset + end,
                    "text_preview": preview,
                    "sentence_offsets": "[]"
                    if os.environ.get("PMA_SENTENCE_OFFSETS", "0") == "0"
                    else json.dumps(_get_sentence_offsets(preview)),
                    "segmenter_version": "py_v1",
                }
            )

            # Advance
            overlap_start = max(0, end - self.chunk_overlap)
            if overlap_start <= 0:
                overlap_start = 1
            self.buffer = self.buffer[overlap_start:]
            self.total_offset += overlap_start

            if len(self.buffer) >= prev_len:
                # Force shrink buffer to guarantee progress and break infinite loops
                self.buffer = self.buffer[1:]
                self.total_offset += 1
                if len(self.buffer) == 0:
                    break

        return chunks

    def finalize(self) -> list[dict[str, Any]]:
        """Process any remaining text in the buffer."""
        chunks = []
        if self.buffer.strip():
            preview = self.prefix + self.buffer
            chunks.append(
                {
                    "start_offset": self.total_offset,
                    "end_offset": self.total_offset + len(self.buffer),
                    "text_preview": preview,
                    "sentence_offsets": "[]"
                    if os.environ.get("PMA_SENTENCE_OFFSETS", "0") == "0"
                    else json.dumps(_get_sentence_offsets(preview)),
                    "segmenter_version": "py_v1",
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
        # How many chunks accumulate before the embedder flushes. A multiple of
        # embedding_batch_size, not a literal: embed_texts re-batches internally
        # at embedding_batch_size (embeddings/service.py:406), so a wider buffer
        # does NOT raise peak ONNX memory - that bound is still set by
        # embedding_batch_size alone. What it buys is a wider pool for
        # _length_sorted_batches to sort over, which is where batch-longest
        # padding actually collapses, plus far fewer per-call round trips.
        # Read here rather than at import time: the eval harness mutates the
        # global settings object.
        self._embed_flush_threshold = max(1, settings.embedding_batch_size * 4)
        self.code_chunker = CodeChunker(max_tokens=512)
        # File ids whose summary changed during the current run. Flushed to the
        # LanceDB summary index once the pipeline drains - re-embedding only what
        # this run touched, rather than the whole corpus.
        self._summary_dirty_file_ids: set[int] = set()

    def cancel_indexing(self):
        with progress._lock:
            progress.is_cancelled = True
            progress.status = "cancelling"
            logger.info("Cancellation requested, will gracefully abort the batch.")

    async def index_folders(self, folders: list[str]):
        if indexing_lock.locked():
            logger.warning("Indexing already in progress.")
            return

        async with indexing_lock, _progress_idle_guard():
            unique_folders = _resolve_folder_overlaps(folders)
            if not unique_folders:
                with progress._lock:
                    progress.status = "idle"
                return

            progress.reset(0)
            with progress._lock:
                progress.status = "running"
                progress.current_file = "Scanning folders…"

            loop = asyncio.get_running_loop()
            all_files, scan_method, scan_duration = await loop.run_in_executor(
                None, self._scan_all_folders, unique_folders
            )

            if not all_files:
                with progress._lock:
                    progress.status = "idle"
                return

            # Create a dedicated reader connection for the scanner / change detection
            import aiosqlite

            reader_conn = await aiosqlite.connect(self.db.db_path)
            try:
                await self.db._configure_conn(reader_conn)
                files_to_index, skipped, new_count, changed_count = await self._detect_changes(
                    all_files, reader_conn
                )
            finally:
                await reader_conn.close()

            progress.reset(len(files_to_index))
            with progress._lock:
                progress.scan_method = scan_method
                progress.scan_duration_ms = scan_duration
                progress.skipped_files = skipped
                progress.new_files = new_count
                progress.changed_files = changed_count

            if not files_to_index:
                await self._generate_folder_profiles(all_files, unique_folders)
                progress.complete()
                return

            use_bulk_mode = len(files_to_index) > 100
            if use_bulk_mode:
                await self.db.enter_ingest_mode()

            try:
                if not progress.is_cancelled:
                    await self._batch_index_pipeline(
                        files_to_index, offset=0, total_to_index=len(files_to_index)
                    )

                if progress.is_cancelled:
                    progress.complete()
                    return

                # Phase 1: Resolve pending GraphRAG edges
                progress.set_current_file("Resolving code graph edges…")
                await self.db.resolve_pending_graph_edges()
            finally:
                if use_bulk_mode:
                    await self.db.exit_ingest_mode()

            try:
                await self._flush_file_summaries()
            except Exception as e:
                logger.error("Failed to index file summaries: %s", e, exc_info=True)

            await self._generate_folder_profiles(all_files, unique_folders)
            from app.search.retrieval import clear_retrieval_cache

            clear_retrieval_cache()

            # Create/update HNSW index at the end of the ingestion run.
            # pma_summaries is searched once per query (the document-routing
            # leg) and was never indexed, so it ran an exhaustive scan whose
            # cost grows with the number of indexed files.
            for _table in ("pma_chunks", "pma_summaries"):
                try:
                    await self.lancedb_client.create_hnsw_index(_table)
                except Exception as e:
                    logger.error("Failed to create HNSW index for %s: %s", _table, e)

            task = asyncio.create_task(self.db.wal_checkpoint())
            from app import state

            state.bg_tasks.add(task)
            task.add_done_callback(state.bg_tasks.discard)

            # Removed post-index incremental vacuum to avoid database locks (H-15)

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

        # Deep enough that the extractor can stay ahead of a full embed batch.
        # At maxsize=32 the embedder drained the queue dry before reaching its
        # threshold and flushed short every time, which defeats the batching. A
        # queued item is a ~512-character preview, so 256 of them is ~130 KB.
        embed_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=max(64, self._embed_flush_threshold)
        )
        store_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=4)

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(
                    self._extractor_worker(files_to_index, embed_queue, total_so_far, grand_total)
                )
                tg.create_task(self._embedder_worker(embed_queue, store_queue))
                tg.create_task(self._storer_worker(store_queue))
        except ExceptionGroup as eg:
            for exc in eg.exceptions:
                logger.error("Pipeline stage sub-exception:", exc_info=exc)
            logger.error("Pipeline stage failed or cancelled due to TaskGroup exceptions.")
        except Exception as e:
            logger.error("Pipeline stage failed or cancelled:", exc_info=e)

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

    async def _extractor_worker(self, files_to_index, embed_queue, total_so_far, grand_total):
        extracted_count = 0
        extracted_lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(self._concurrency * 2)

        async def _safe_stream_extract(path: Path, tag: str, cached_text: str | None):
            if progress.is_cancelled:
                return
            nonlocal extracted_count
            async with semaphore:
                async with extracted_lock:
                    extracted_count += 1
                    overall = total_so_far + extracted_count
                progress.set_current_file(f"Extracting: {path.name} ({overall}/{grand_total})")

                await self._stream_extract_and_prepare(path, tag, cached_text, embed_queue)

        # Process files_to_index in small chunks of 16 to enforce O(1) memory boundary
        chunk_size = 16
        for i in range(0, len(files_to_index), chunk_size):
            if progress.is_cancelled:
                break
            chunk = files_to_index[i : i + chunk_size]
            chunk_pre_extracted = await self._rust_pre_extract(chunk)

            tasks = []
            for fp, ft in chunk:
                cached_text = chunk_pre_extracted.pop(str(fp.absolute()), None)
                tasks.append(_safe_stream_extract(fp, ft, cached_text))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    logger.error("File extraction failed: %s", res)

            chunk_pre_extracted.clear()

        # Send sentinel
        await embed_queue.put(None)

    async def _stream_extract_and_prepare(
        self, path: Path, folder_tag: str, pre_text: str | None, queue: asyncio.Queue
    ) -> None:
        import queue as stdlib_queue

        loop = asyncio.get_running_loop()
        header_sent = False
        try:
            stat = await loop.run_in_executor(_DISK_EXECUTOR, path.stat)

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
                    "sha256": "",  # placeholder, updated in footer
                },
            }
            await queue.put(header)
            header_sent = True

            prefix = self._build_context_prefix(str(path))
            logger.info("Started extracting and chunking for file: %s", path)

            bridge: stdlib_queue.Queue[Any] = stdlib_queue.Queue(maxsize=64)
            sentinel = object()

            def _extract_and_chunk():
                chunker = StreamChunker(self.chunk_size, self.chunk_overlap, prefix)
                hasher = hashlib.sha256()
                ft_summary = ""
                meta = None

                hash_failed = False
                # 1. Consistent raw-byte hashing pass (OS page cache makes extraction pass cheap)
                try:
                    with open(path, "rb") as f:
                        while True:
                            b = f.read(128 * 1024)
                            if not b:
                                break
                            hasher.update(b)
                except Exception as e:
                    logger.debug("Failed to hash %s: %s", path, e)
                    hash_failed = True

                def _get_stream():
                    if pre_text is not None:
                        yield pre_text
                        return
                    for ex in EXTRACTORS:
                        if ex.can_handle(path):
                            yield from ex.extract_stream(path, self.max_file_size)
                            return
                    yield from self._extract_plain_text_stream(path)

                try:
                    for fragment in _get_stream():
                        if progress.is_cancelled:
                            return "CANCELLED", "", None

                        # Out-of-band extractor signal (which pages need OCR).
                        # Checked before the [BINARY: test because ExtractMeta
                        # has no .startswith.
                        if isinstance(fragment, ExtractMeta):
                            meta = fragment
                            continue

                        # Skip binary stubs — they pollute the vector index with useless noise
                        if isinstance(fragment, str) and fragment.startswith("[BINARY:"):
                            logger.debug("Skipping binary stub for %s — no indexable text.", path)
                            ft_summary = ""
                            break

                        for c in chunker.process(fragment):
                            if progress.is_cancelled:
                                return "CANCELLED", "", None
                            while True:
                                try:
                                    bridge.put(c, timeout=1.0)
                                    break
                                except stdlib_queue.Full:
                                    if progress.is_cancelled:
                                        return "CANCELLED", "", None
                        if len(ft_summary) < 2000:
                            ft_summary += (
                                fragment
                                if isinstance(fragment, str)
                                else fragment.decode("utf-8", errors="replace")
                            )

                    for c in chunker.finalize():
                        if progress.is_cancelled:
                            return "CANCELLED", "", None
                        while True:
                            try:
                                bridge.put(c, timeout=1.0)
                                break
                            except stdlib_queue.Full:
                                if progress.is_cancelled:
                                    return "CANCELLED", "", None
                    sha256_result = "ERROR" if hash_failed else hasher.hexdigest()
                    return sha256_result, ft_summary, meta
                finally:
                    bridge.put(sentinel)

            async def _pump():
                while True:
                    item = await loop.run_in_executor(_DISK_EXECUTOR, bridge.get)
                    if item is sentinel:
                        break
                    await queue.put({"type": "chunk", "path": path, "chunk": item})

            extract_future = loop.run_in_executor(_EXTRACT_EXECUTOR, _extract_and_chunk)
            await _pump()
            sha256, full_text_for_summary, extract_meta = await extract_future

            summary = await loop.run_in_executor(
                None, self._generate_summary, full_text_for_summary, path
            )
            # Send footer with CANCELLED if extraction was cancelled, otherwise actual sha256
            if progress.is_cancelled and sha256 != "CANCELLED":
                sha256 = "CANCELLED"
            await queue.put(
                {
                    "type": "footer",
                    "path": path,
                    "summary": summary,
                    "sha256": sha256,
                    "extract_meta": extract_meta,
                }
            )

        except Exception as e:
            logger.error("Streaming extraction failed for %s: %s", path, e)
            if not header_sent:
                try:
                    dummy_header = {
                        "type": "header",
                        "path": path,
                        "folder_tag": folder_tag,
                        "file_data": {
                            "path": str(path.absolute()),
                            "size": 0,
                            "modified_at": datetime.now().isoformat(),
                            "type": path.suffix.lower(),
                            "folder_tag": folder_tag,
                            "sha256": "ERROR",
                        },
                    }
                    await queue.put(dummy_header)
                    header_sent = True
                except Exception as inner_h:
                    logger.error("Failed to send dummy header for %s: %s", path, inner_h)
            if header_sent:
                await queue.put(
                    {
                        "type": "footer",
                        "path": path,
                        "summary": f"[ERROR: {e!s}]",
                        "sha256": "ERROR",
                        "extract_meta": None,
                    }
                )

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
        # Headers and footers ride in the same buffer as chunks rather than
        # forcing a flush. _storer_worker's invariant is per-path relative order
        # - header(P) sets active_files[P], chunks read it, footer(P) pops it and
        # a chunk arriving after its own footer is silently dropped - and
        # draining this buffer in arrival order preserves that exactly. Flushing
        # on every header/footer was strictly stronger than the invariant needs,
        # and it cut every batch at a file boundary: a five-chunk file embedded
        # as a batch of five.
        pending: list[dict[str, Any]] = []
        n_chunks = 0

        async def _flush() -> None:
            nonlocal n_chunks
            if not pending:
                return
            chunk_items = [i for i in pending if i["type"] == "chunk"]
            if chunk_items:
                await self._process_embed_stream_batch(chunk_items)
            for item in pending:
                await store_queue.put(item)
            pending.clear()
            n_chunks = 0

        try:
            while True:
                item = await embed_queue.get()
                if item is None:
                    await _flush()
                    break

                pending.append(item)
                if item["type"] == "chunk":
                    n_chunks += 1

                # Greedily take whatever else is already queued, so a batch can
                # span files under load. Stopping as soon as the queue is empty
                # bounds latency when it is not: progress.update() only fires
                # from _storer_worker's footer branch, so footers must not sit
                # here waiting for a batch that will not arrive.
                drained_dry = False
                while n_chunks < self._embed_flush_threshold:
                    try:
                        nxt = embed_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        drained_dry = True
                        break
                    if nxt is None:
                        await _flush()
                        return
                    pending.append(nxt)
                    if nxt["type"] == "chunk":
                        n_chunks += 1

                if drained_dry or n_chunks >= self._embed_flush_threshold:
                    await _flush()
        finally:
            # C-03: Always send sentinel so _storer_worker drains even on embedder failure.
            await store_queue.put(None)

    async def _process_embed_stream_batch(
        self, batch_items: list[dict[str, Any]], update_progress: bool = True
    ):
        texts = [item["chunk"]["text_preview"] for item in batch_items]
        if not texts:
            return

        unique_paths = list(set(str(item["path"].name) for item in batch_items))
        logger.info(
            "Embedding batch of %d chunks for files: %s", len(texts), ", ".join(unique_paths)
        )

        def report_progress(batch_num, total_batches):
            if update_progress and progress.status != "idle":
                progress.set_current_file(f"Phase 2/3: Embedding chunks ({batch_num}/{total_batches})…")

        all_embeddings = await self.embedding_service.embed_texts(
            texts, progress_callback=report_progress if update_progress else None
        )
        for idx, item in enumerate(batch_items):
            item["chunk"]["_embedding"] = all_embeddings[idx]

    async def _storer_worker(self, store_queue: asyncio.Queue[dict[str, Any] | None]):
        import time

        active_files: dict[str, dict[str, Any]] = {}
        pending_chunks: list[dict[str, Any]] = []
        use_tx = hasattr(self.db, "begin_transaction")

        commit_chunk_threshold = 2000
        commit_time_limit = 10.0  # seconds
        chunks_since_commit = 0
        last_commit_time = time.monotonic()
        tx_open = False

        async def _commit_window():
            nonlocal chunks_since_commit, last_commit_time, tx_open
            l_ids, l_embs, l_metas = [], [], []
            if pending_chunks:
                res = await self._flush_pending_chunks_sqlite(pending_chunks, active_files)
                if res:
                    l_ids, l_embs, l_metas = res
            if tx_open and use_tx:
                if hasattr(self.db, "commit"):
                    await self.db.commit()
                tx_open = False
            if l_ids:
                await self._flush_pending_chunks_lancedb(l_ids, l_embs, l_metas)
            pending_chunks.clear()
            chunks_since_commit = 0
            last_commit_time = time.monotonic()

        try:
            while True:
                item = await store_queue.get()
                if item is None:
                    await _commit_window()
                    break

                if not tx_open and use_tx:
                    await self.db.begin_transaction()
                    tx_open = True

                ptype, path_str = item["type"], str(item["path"].absolute())

                if ptype == "header":
                    if use_tx:
                        file_id = await self.db.batch_insert_files(
                            [item["file_data"]], auto_commit=False
                        )
                    else:
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
                        chunks_since_commit += 1
                elif ptype == "footer":
                    file_info = active_files.pop(path_str, None)
                    if file_info:
                        await self.db.execute_write(
                            "UPDATE files SET summary = ?, sha256 = ? WHERE id = ?",
                            (item["summary"], item.get("sha256", ""), file_info["id"]),
                        )
                        # The summary is the document-level retrieval signal; it
                        # has to reach the vector index, not just SQLite.
                        self._summary_dirty_file_ids.add(file_info["id"])
                        await self._maybe_enqueue_ocr(path_str, item)
                        progress.update(file_info["chunk_count"], current_file=item["path"].name)
                    else:
                        progress.update(0, current_file=item["path"].name)

                # Check thresholds
                if (
                    chunks_since_commit >= commit_chunk_threshold
                    or time.monotonic() - last_commit_time >= commit_time_limit
                ):
                    await _commit_window()

        except Exception as e:
            logger.error("Storer worker failed: %s", e)
            if tx_open and hasattr(self.db, "rollback_transaction"):
                try:
                    await self.db.rollback_transaction()
                    logger.info("Rolled back active transaction due to storer error.")
                except Exception as rollback_err:
                    logger.error("Failed to rollback transaction: %s", rollback_err)
            raise

    async def _maybe_enqueue_ocr(self, path_str: str, footer: dict[str, Any]) -> None:
        """Queue a file's scanned pages for OCR, if it has any.

        Called from the footer branch rather than the header because that is
        where `files.sha256` finally gets written - the header inserts an empty
        placeholder. The sentinels the hashing pass writes on failure
        ("ERROR") or interruption ("CANCELLED") must never become a cache key,
        so they are excluded here; that single check is also what keeps
        garbage out of `ocr_cache`.

        Runs on the same connection and commits immediately, matching the
        sha256 UPDATE just above it. Failures are logged and swallowed - a
        queue write must never be able to fail an index run.
        """
        meta = footer.get("extract_meta")
        if not meta or not getattr(meta, "ocr_pages", None):
            return
        sha = footer.get("sha256") or ""
        if sha in ("", "ERROR", "CANCELLED"):
            return

        from app.config import settings
        from app.ocr.settings import load_persisted_state

        if not settings.ocr_enabled or settings.ocr_tier == "none":
            load_persisted_state()
        if not settings.ocr_enabled or settings.ocr_tier == "none":
            return

        try:
            from app.ocr.queue import enqueue_document

            await enqueue_document(
                self.db,
                path_str,
                list(meta.ocr_pages),
                meta.page_count,
                tier=settings.ocr_tier,
            )
            with contextlib.suppress(Exception):
                from app.api.deps import get_ocr

                ocr = await get_ocr()
                if ocr:
                    await ocr.kick()
        except Exception as exc:
            logger.warning("Failed to enqueue OCR for %s: %s", path_str, exc)

    async def _flush_pending_chunks_sqlite(self, chunks: list[dict[str, Any]], active_files: dict):
        if not chunks:
            return None
        import json

        import numpy as np

        chunk_rows = []
        for item in chunks:
            row = {
                k: v
                for k, v in item["chunk"].items()
                if k not in ("_embedding", "kg_nodes", "kg_edges")
            }
            row["file_id"] = item["file_id"]
            chunk_rows.append(row)

        use_tx = hasattr(self.db, "begin_transaction")
        if use_tx:
            chunk_ids_int = await self.db.insert_chunks_bulk(chunk_rows, auto_commit=False)
        else:
            chunk_ids_int = await self.db.insert_chunks_bulk(chunk_rows)

        backup_enabled = settings.lancedb_mode == "split_brain" or settings.sqlite_embedding_backup
        l_ids, l_embs, l_metas = [], [], []
        emb_blobs = []
        kg_nodes_data = []
        kg_edges_data = []

        for chunk_id, item in zip(chunk_ids_int, chunks, strict=True):
            chunk = item["chunk"]
            cid_str = str(chunk_id)
            l_ids.append(cid_str)
            emb = chunk.pop("_embedding")
            l_embs.append(emb)
            l_metas.append(
                {
                    "chunk_id": cid_str,
                    "file_path": item["path"].absolute().as_posix(),
                    "folder_tag": active_files.get(str(item["path"].absolute()), {})
                    .get("data", {})
                    .get("folder_tag", ""),
                }
            )
            if backup_enabled:
                emb_blobs.append((chunk_id, np.array(emb, dtype=np.float16).tobytes()))

            for node in chunk.get("kg_nodes", []):
                props = json.dumps(
                    {
                        "chunk_id": chunk_id,
                        "start_line": node.get("start_line"),
                        "end_line": node.get("end_line"),
                    }
                )
                kg_nodes_data.append((node["id"], "entity", node["label"], props, chunk_id))

            for edge in chunk.get("kg_edges", []):
                props = json.dumps({"chunk_id": chunk_id})
                kg_edges_data.append((edge["src_id"], edge["dst_id"], edge["rel_type"], 1.0, props))

            # The SQLite row and KG payloads are ready; release the large source fields.
            chunk.pop("kg_nodes", None)
            chunk.pop("kg_edges", None)
            chunk.pop("text_preview", None)

        if use_tx:
            if backup_enabled:
                await self.db.insert_chunk_embeddings_bulk(emb_blobs, auto_commit=False)
            if kg_nodes_data:
                await self.db.insert_kg_nodes_bulk(kg_nodes_data, auto_commit=False)  # type: ignore
            if kg_edges_data:
                await self.db.insert_kg_edges_bulk(kg_edges_data, auto_commit=False)
        else:
            if backup_enabled:
                await self.db.insert_chunk_embeddings_bulk(emb_blobs)
            if kg_nodes_data:
                await self.db.insert_kg_nodes_bulk(kg_nodes_data)  # type: ignore
            if kg_edges_data:
                await self.db.insert_kg_edges_bulk(kg_edges_data)

        return l_ids, l_embs, l_metas

    async def _flush_pending_chunks_lancedb(self, l_ids, l_embs, l_metas):
        if l_ids:
            await self.lancedb_client.add_documents(l_ids, l_embs, l_metas)

            import gc

            gc.collect()

    async def _delete_existing_chunks(self, file_id: int) -> None:
        old_chunks = await self.db.get_file_chunks(file_id)
        old_ids = [str(chunk["id"]) for chunk in old_chunks]
        if old_ids:
            await self.lancedb_client.delete_documents(old_ids)
        await self.db.delete_file_chunks(file_id, auto_commit=False)

    async def index_ocr_pages(
        self,
        path: Path,
        pages: list[Any],
        *,
        replace_existing_ocr: bool = True,
    ) -> int:
        """Chunk, embed and store OCR results for an already-indexed file.

        OCR results arrive long after the indexing run that queued them, so
        they cannot ride the three-stage TaskGroup pipeline. This walks the
        same machinery by hand - same chunker, same embed batch method, same
        flush helpers - so OCR chunks are indistinguishable from native ones
        downstream.

        Only `OcrPage.indexable_text` is stored, which excludes lines below the
        confidence floor. Since FTS is populated by an INSERT trigger on
        `chunks`, that exclusion propagates to search for free while the full
        text stays in `ocr_cache`.

        Safe to run concurrently with an index run: DatabaseManager handles
        transaction isolation and write serialization, ensuring OCR chunk
        inserts and LanceDB updates do not corrupt in-flight indexing batches.

        Returns the number of chunks written.
        """
        path_str = str(path.absolute())
        row = await self.db.get_file_by_path(path_str)
        if row is None:
            logger.info("OCR results discarded - %s is no longer indexed.", path.name)
            return 0

        file_id = row["id"]
        try:
            folder_tag = row["folder_tag"] or ""
        except (KeyError, IndexError):
            folder_tag = ""

        # Replace only our own chunks. A mixed PDF (native body, scanned
        # appendix) must keep its natively extracted text.
        #
        # The ids are captured here but nothing is deleted yet. Both deletes
        # used to run at this point - the LanceDB one is irreversible and
        # delete_ocr_chunks committed - which put two unrecoverable holes ahead
        # of the insert: the rollback below could not undo them, and the
        # `not raw_chunks` return further down exits before any replacement is
        # written. A scan whose every line fell under the confidence floor
        # therefore destroyed the text of the previous, better run.
        old_ids: list[int] = []
        if replace_existing_ocr:
            old_ids = await self.db.get_ocr_chunk_ids(file_id)

        prefix = self._build_context_prefix(path_str)
        chunker = StreamChunker(self.chunk_size, self.chunk_overlap, prefix)
        raw_chunks: list[dict[str, Any]] = []
        for page in sorted(pages, key=lambda p: p.page_num):
            text = page.indexable_text
            if text:
                raw_chunks.extend(chunker.process(text + "\n"))
        raw_chunks.extend(chunker.finalize())

        if not raw_chunks:
            # Returns before the delete, so a run that produced nothing leaves
            # the previous run's text in place. Every line falling under
            # ocr_conf_floor is the normal output of a bad scan, not a signal
            # that the existing text should be thrown away.
            logger.info("OCR produced no indexable text for %s", path.name)
            return 0

        for chunk in raw_chunks:
            chunk["source"] = "ocr"

        items = [
            {"type": "chunk", "path": path, "chunk": chunk, "file_id": file_id}
            for chunk in raw_chunks
        ]
        # _flush_pending_chunks_sqlite reads folder_tag out of this shape.
        active_files = {
            path_str: {
                "id": file_id,
                "data": {"folder_tag": folder_tag},
                "chunk_count": len(items),
            }
        }

        step = self._embed_flush_threshold
        for i in range(0, len(items), step):
            await self._process_embed_stream_batch(items[i : i + step], update_progress=False)

        # _flush_pending_chunks_sqlite always defers its commit to the caller.
        # The delete joins that transaction (auto_commit=False) so the rollback
        # below genuinely restores the previous OCR text rather than leaving the
        # file with nothing.
        await self.db.begin_transaction()
        try:
            if replace_existing_ocr:
                await self.db.delete_ocr_chunks(file_id, auto_commit=False)
            result = await self._flush_pending_chunks_sqlite(items, active_files)
            await self.db.commit()
        except Exception:
            try:
                await self.db.rollback_transaction()
            except Exception as rollback_err:
                logger.error("OCR chunk rollback failed for %s: %s", path.name, rollback_err)
            raise

        # Only now is the old row set genuinely gone from SQLite, so the
        # irreversible vector delete is safe to run. Before the insert rather
        # than after it: if the add fails, the stale vectors are already gone
        # instead of being left behind pointing at deleted chunk rows.
        if old_ids:
            try:
                await self.lancedb_client.delete_documents([str(i) for i in old_ids])
            except Exception as exc:
                logger.warning("Could not drop old OCR vectors for %s: %s", path.name, exc)

        if result:
            l_ids, l_embs, l_metas = result
            await self._flush_pending_chunks_lancedb(l_ids, l_embs, l_metas)

        try:
            from app.search.retrieval import clear_retrieval_cache

            clear_retrieval_cache()
        except Exception as exc:
            logger.debug("Could not clear retrieval cache after OCR: %s", exc)

        try:
            from app import state as app_state

            app_state.file_tree_cache["data"] = None
            app_state.insights_cache["data"] = None
        except Exception as exc:
            logger.debug("Could not invalidate UI caches after OCR: %s", exc)

        logger.info("OCR indexed %d chunk(s) for %s", len(items), path.name)
        return len(items)

    async def _flush_file_summaries(self) -> int:
        """Embed the summaries of files touched this run into the LanceDB summary index.

        `pma_summaries` backs the document-routing signal in hybrid retrieval:
        it ranks whole files by summary similarity before chunk budget is spent.
        Folder profiles live in the same table under `is_folder_profile="true"`;
        per-file rows are tagged `"false"` so the two can be queried apart. The
        metadata key set must match the folder-profile rows exactly - LanceDB
        appends require a stable schema, so the file id rides in `doc_id`.
        """
        file_ids = list(self._summary_dirty_file_ids)
        self._summary_dirty_file_ids.clear()
        if not file_ids:
            return 0

        written = 0
        batch_size = 500
        for start in range(0, len(file_ids), batch_size):
            batch = file_ids[start : start + batch_size]
            placeholders = ",".join("?" for _ in batch)
            rows = await self.db.execute_query(
                f"SELECT id, path, folder_tag, summary FROM files WHERE id IN ({placeholders})",  # nosec B608 # noqa: S608
                tuple(batch),
            )
            usable = [r for r in rows if r[3] and not str(r[3]).startswith("[ERROR:")]
            if not usable:
                continue

            doc_ids = [f"file_{r[0]}" for r in usable]
            # Replace rather than append - re-indexing a file must not leave its
            # previous summary vector behind to compete with the new one.
            try:
                await self.lancedb_client.delete_summaries_by_ids(doc_ids)
            except Exception as e:
                logger.warning("Could not clear stale file summaries: %s", e)

            # Embed the de-scaffolded form, not the display string. The
            # "[MD: x.md] Structure:" prefix is identical corpus-wide, so
            # embedding it verbatim ranked documents largely on what they share.
            embs = await self.embedding_service.embed_texts(
                [summary_embedding_text(r[1], str(r[3])) for r in usable]
            )
            summaries = [
                {
                    "doc_id": doc_id,
                    "embedding": emb,
                    "metadata": {
                        "file_path": r[1],
                        "folder_tag": r[2] or "",
                        "is_folder_profile": "false",
                    },
                }
                for doc_id, r, emb in zip(doc_ids, usable, embs, strict=False)
            ]
            await self.lancedb_client.add_summaries_batch(summaries)
            written += len(summaries)

        logger.info("Indexed %d file summaries into the document-routing index.", written)
        return written

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
        if ext in TEXT_EXTENSIONS:
            if self._is_binary(path):
                return f"[BINARY: {path.name}] content not indexed."
            return self._extract_plain_text(path)
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
                        p_obj.resolve().relative_to(f_res)
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
        self, all_files: list[tuple[Path, str]], reader_conn: aiosqlite.Connection
    ) -> tuple[list[tuple[Path, str]], int, int, int]:
        file_paths = [str(fp.absolute()) for fp, _ in all_files]
        change_map = await self.db.get_files_change_map(file_paths, conn=reader_conn)
        to_index, skipped, new_c, changed_c = [], 0, 0, 0

        # H-03: fp.stat() is a blocking syscall; gather all stats concurrently
        # via dedicated disk executor to avoid serializing calls.
        async def _stat_file(fp: Path) -> tuple[Path, str, str | None]:
            try:
                # H-18: Use dedicated disk executor
                stat = await loop.run_in_executor(_DISK_EXECUTOR, fp.stat)
                mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
                return fp, mtime, None
            except OSError:
                return fp, "", None

        loop = asyncio.get_running_loop()
        stat_results = []
        batch_size = 1000
        for i in range(0, len(all_files), batch_size):
            batch = all_files[i : i + batch_size]
            stat_tasks = [_stat_file(fp) for fp, _ in batch]
            res = await asyncio.gather(*stat_tasks, return_exceptions=False)
            stat_results.extend(res)

        stat_map: dict[str, str] = {}
        failed_paths: set[str] = set()
        for fp, mtime, _ in stat_results:
            key = str(fp.absolute())
            if mtime:
                stat_map[key] = mtime
            else:
                failed_paths.add(key)

        for fp, tag in all_files:
            key = str(fp.absolute())
            if key in failed_paths:
                skipped += 1
                continue
            mtime = stat_map.get(key, "")
            stored = change_map.get(key)
            if stored and stored[0] == mtime and stored[1] not in ("ERROR", "CANCELLED"):
                skipped += 1
            elif stored:
                changed_c += 1
                to_index.append((fp, tag))
            else:
                new_c += 1
                to_index.append((fp, tag))
        return to_index, skipped, new_c, changed_c

    def _is_binary(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                chunk = f.read(8192)
                return b"\x00" in chunk
        except Exception:
            return True

    def _generate_summary(self, text: str, path: Path, max_chars: int = 300) -> str:
        return generate_deep_summary(text, path, max_chars)

    def _create_chunks(self, text: str, file_path: str = "") -> list[dict[str, Any]]:
        if not text:
            return []
        prefix = self._build_context_prefix(file_path)
        ext = Path(file_path).suffix.lower() if file_path else ""
        if RUST_CORE_AVAILABLE and ext in (".txt", ".md", ".markdown", ".log"):
            try:
                # Offload to create_chunks PyO3 binding
                chunks = rust_core.create_chunks(
                    text, self.chunk_size, self.chunk_overlap, prefix, 0
                )
                if os.environ.get("PMA_SENTENCE_OFFSETS", "0") == "0":
                    for c in chunks:
                        c["sentence_offsets"] = "[]"
                return chunks  # type: ignore[no-any-return]
            except Exception as e:
                logger.warning(
                    "Rust create_chunks failed for %s (%s), falling back to Python.", file_path, e
                )
        chunks = self.code_chunker.chunk_code(text, file_path=file_path, prefix=prefix)
        for c in chunks:
            if "start_offset" not in c:
                c["start_offset"] = 0
            if "end_offset" not in c:
                c["end_offset"] = len(c["text_preview"])
            if os.environ.get("PMA_SENTENCE_OFFSETS", "0") == "0":
                c["sentence_offsets"] = "[]"
            else:
                c["sentence_offsets"] = json.dumps(_get_sentence_offsets(c["text_preview"]))
            c["segmenter_version"] = "py_v1"
        return chunks

    @staticmethod
    def _build_context_prefix(file_path: str) -> str:
        p = Path(file_path)
        return f"[{p.suffix.lstrip('.').upper() or 'file'}: {p.name}] "
