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
    build_context_prefix,
    chunk_embedding_text,
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
        # Distinct from skipped_files, which means "unchanged, nothing to do".
        # A file counted here was attempted and produced nothing indexable.
        self.failed_files = 0
        # Selected for indexing because the mtime moved, then found to be
        # byte-identical. Proves the content-hash early-out is working.
        self.unchanged_files = 0
        self.status = "idle"
        self.scan_method = ""
        self.scan_duration_ms = 0.0
        self.current_file = "Ready"
        self.is_cancelled = False
        self.last_error = ""
        self.run_failed = False

    def reset(self, total_files: int, initial_status: str = "running"):
        with self._lock:
            self.total_files = total_files
            self.processed_files = 0
            self.total_chunks = 0
            self.skipped_files = 0
            self.new_files = 0
            self.changed_files = 0
            self.failed_files = 0
            self.unchanged_files = 0
            self.status = initial_status
            self.scan_method = ""
            self.scan_duration_ms = 0.0
            self.current_file = "Starting…"
            self.is_cancelled = False
            self.last_error = ""
            self.run_failed = False

    def update(self, chunks_added: int, current_file: str = ""):
        with self._lock:
            self.processed_files += 1
            self.total_chunks += chunks_added
            if current_file:
                self.current_file = current_file

    def set_current_file(self, current_file: str):
        with self._lock:
            self.current_file = current_file

    def record_failure(self, message: str = "") -> None:
        """A single file was attempted and yielded nothing indexable."""
        with self._lock:
            self.failed_files += 1
            if message:
                self.last_error = message[:300]

    def record_unchanged(self) -> None:
        """Re-scanned because its mtime moved, but its bytes were identical."""
        with self._lock:
            self.unchanged_files += 1

    def fail(self, message: str) -> None:
        """The run itself did not finish.

        Deliberately does not park `status` on a non-idle value: the OCR drain
        loop treats any non-"idle" status as "indexer busy" and refunds its claim
        (`app/ocr/manager.py:480`), so a sticky failure state here would stall OCR
        until the next index run. The failure is carried by `run_failed` instead,
        which `complete()` and the idle guard both honour.
        """
        with self._lock:
            self.run_failed = True
            self.current_file = "Failed"
            self.last_error = message[:300]

    def complete(self):
        with self._lock:
            self.status = "idle"
            # A crashed run must not be able to report success. Before this,
            # _batch_index_pipeline swallowed its TaskGroup's ExceptionGroup and
            # the run still finished on "Complete".
            if self.run_failed:
                return
            self.current_file = (
                "Complete"
                if not self.failed_files
                else f"Complete — {self.failed_files} file(s) failed"
            )


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
                # Do not overwrite a recorded failure with the generic
                # "Interrupted" - fail() already wrote something specific.
                if not progress.run_failed:
                    progress.current_file = "Interrupted"


# Extractor stubs that describe a failure rather than carrying file content.
# rust_core returns "[UNREADABLE: <path>]" *as* the text when a read fails
# (app/scanner/rust_core/src/lib.rs:515), and the PDF/DOCX extractors yield an
# "[ENCRYPTED …]" notice. Only "[BINARY:" was ever filtered, so the other two
# were chunked, embedded and stored as though they were document content.
_STUB_PREFIXES = ("[BINARY:", "[UNREADABLE:", "[ENCRYPTED")

# The subset of the above that means "try again later" rather than "deliberately
# not indexed". rust_core emits "[UNREADABLE:" when File::open or read_to_end
# fails (lib.rs:513, :530, :533) - a transient condition. Recording such a file
# as complete would retire it on an antivirus lock.
_TRANSIENT_STUB_PREFIXES = ("[UNREADABLE:",)

# Why a file produced no chunks, recorded on `files.extract_status`.
#
# `files.sha256` was carrying this as well as the digest, via the sentinels
# below, and it could not carry all of it: a deliberately-skipped binary and a
# scanned page awaiting OCR both keep their *real* digest with zero chunks, so
# they were indistinguishable in the database and nothing said which had
# happened. "" means the file produced content normally.
_STUB_STATUS = {
    "[BINARY:": "binary",
    "[UNREADABLE:": "unreadable",
    "[ENCRYPTED": "encrypted",
}

# sha256 values that mean "this file is not successfully indexed", so
# _detect_changes must re-attempt it rather than treating it as up to date.
#   ""          - the header's placeholder (service.py:534). The storer's commit
#                 window can persist it before the footer writes the real digest,
#                 so an interrupted run leaves rows here. Also the pre-migration
#                 default from db.py:700/:749, which re-indexes once and settles.
#   NOCONTENT   - extraction produced no chunks from a non-empty file.
_INCOMPLETE_SHA_STATES = ("", "ERROR", "CANCELLED", "NOCONTENT")


#: One scanned file: its path, the basename of the indexed folder it came from,
#: and that folder's full path. The root is carried separately because
#: `folder_tag` is only the basename and so cannot be turned back into a path,
#: nor tell two like-named folders apart. Empty root means the scanner could not
#: attribute the file to any requested root.
ScannedFile = tuple[Path, str, str]


#: UTF-16/32 byte-order marks. Text in these encodings is full of NUL bytes, so
#: a plain NUL test calls it binary. On Windows that is not an edge case:
#: PowerShell's `>`, `Out-File` and `Export-Csv` wrote UTF-16LE by default
#: through 5.1, so ordinary .csv/.json/.sql/.log files land here routinely.
_TEXT_BOMS = (
    b"\xff\xfe\x00\x00",  # UTF-32 LE
    b"\x00\x00\xfe\xff",  # UTF-32 BE
    b"\xff\xfe",  # UTF-16 LE
    b"\xfe\xff",  # UTF-16 BE
    b"\xef\xbb\xbf",  # UTF-8
)


def _encoding_for(head: bytes) -> str:
    """Codec implied by a leading BOM, else UTF-8.

    The "utf-16"/"utf-32" codecs read the endianness from the BOM and strip it;
    "utf-8-sig" strips a UTF-8 BOM and is identical to "utf-8" without one.
    """
    if head.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return "utf-32"
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    return "utf-8-sig"


def _looks_binary(head: bytes) -> bool:
    """Mirror of rust_core's _is_binary_buffer (lib.rs:481-489), plus a BOM check.

    Any NUL, or more than 30% control bytes, in the first 8 KB - except that a
    recognised text BOM settles it first. rust_core only ever sees
    TEXT_EXTENSIONS files, which are overwhelmingly UTF-8; this fallback sees
    everything else in `supported_extensions`, so it needs the wider test.
    """
    if not head:
        return False
    if head.startswith(_TEXT_BOMS):
        return False
    if b"\x00" in head:
        return True
    non_text = sum(1 for b in head if b < 32 and b not in (9, 10, 13))
    return non_text / len(head) > 0.30


def _hash_file(path: Path) -> tuple[str, bool]:
    """Raw-byte sha256 of a file. Returns (digest, hash_failed)."""
    hasher = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(128 * 1024)
                if not b:
                    break
                hasher.update(b)
    except Exception as e:
        logger.debug("Failed to hash %s: %s", path, e)
        return "", True
    return hasher.hexdigest(), False


# H-18: Dedicated pool for disk-heavy operations to avoid default pool starvation.
_DISK_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="pma-disk")
_EXTRACT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pma-extract")


def shutdown_executors() -> None:
    """Retire the module-level pools.

    **This is not what fixes the teardown hang.** `shutdown()` cannot retire a
    worker already parked inside an untimed blocking call, and
    `concurrent.futures.thread`'s atexit hook joins every pool's threads with no
    timeout, so one such worker hangs interpreter exit forever at zero CPU. What
    prevents that is bounding the calls themselves - see `_offer` and
    `_bridge_get` in `_stream_extract_and_prepare`.

    This exists so a clean shutdown does not sit waiting on idle workers, and so
    the pools do not outlive the process that owns them.

    The retired pools are *replaced*, not just closed. The FastAPI lifespan,
    `EvalIndex.close()` and a test session may all call this, and leaving the
    module globals pointing at shut-down executors would make every later
    indexing run in the same process die on "cannot schedule new futures after
    shutdown". ThreadPoolExecutor spawns its threads lazily, so the replacements
    cost nothing until something submits work.
    """
    global _DISK_EXECUTOR, _EXTRACT_EXECUTOR

    for pool in (_DISK_EXECUTOR, _EXTRACT_EXECUTOR):
        with contextlib.suppress(Exception):
            pool.shutdown(wait=False, cancel_futures=True)

    _DISK_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="pma-disk")
    _EXTRACT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pma-extract")


# Extensions whose chunker needs the WHOLE file rather than a stream.
#
# Reviving the dispatcher that CLAUDE.md 8.7 A2/A3 found dead. Deliberately not
# "everything": routing all types through `_create_chunks` would send PDFs and
# office documents to `CodeChunker._chunk_fallback`, a blind character split
# with no sentence snapping at all, which is strictly worse than the
# StreamChunker they use today. Only the types with a genuinely better chunker
# move:
#
#   code     -> CodeChunker, syntax-aware, and the only producer of kg_nodes /
#               kg_edges, so this is also what finally populates the knowledge
#               graph (A4)
# .txt/.log stay streamed: rust_core.create_chunks is the same sliding window
# StreamChunker already runs, so switching them would trade streaming for
# nothing.
#
# **Markdown was tried here twice and measured worse both times, so it is NOT
# routed.** `rust_core.chunk_markdown` is section-aware, which sounds like a
# clear win for prose and is not on this corpus.
#
# First attempt: it applied `chunk_size` only as a MAXIMUM, so a section shorter
# than the budget became a chunk that short and chunk size followed heading
# density. 589 chunks -> 1060, document nDCG 0.891 -> 0.80, answer coverage
# 0.597 -> 0.542.
#
# That defect is now fixed in Rust - adjacent sections merge up to the budget,
# and the stored span finally matches the trimmed text it describes. Re-measured
# at chunk_size=2048 it is no longer catastrophic and is still worse:
#
#     chunks           140 -> 137   (fine)
#     answer coverage  1.000 -> 1.000   (tied, saturated)
#     chunk precision  0.163 -> 0.100   (clearly worse)
#     document nDCG    0.94 -> 0.91 with the reranker on
#
# Merged runs join unrelated subsections - a parameter entry with a worked
# example - so each chunk is less topically focused than the sliding window's
# uniform, sentence-snapped output. Coverage cannot show this because it is
# already saturated; precision can, and does.
#
# So A3 stays open deliberately. The function is better than it was and is
# exercised by tests; routing prose to it needs a corpus where section
# boundaries carry more signal than they do here.
_CODE_EXTENSIONS = frozenset({".py", ".js", ".ts", ".jsx", ".tsx", ".rs"})
_WHOLE_TEXT_EXTENSIONS = _CODE_EXTENSIONS


class StreamChunker:
    """Helper to process a stream of text fragments into properly sized chunks.

    The split-point search is a separator cascade modelled on LangChain's
    ``RecursiveCharacterTextSplitter`` (langchain_text_splitters 1.1.2,
    ``character.py::_split_text``), read as a reference and reimplemented rather
    than depended on - section 6's dependency policy wants a measured bottleneck
    before a library, and this is one function.

    **One deliberate divergence, and it is the reason this is not a port.**
    LangChain chooses ONE separator for the entire document - the first rung its
    ``re.search`` finds anywhere - and then ``_merge_splits`` packs pieces up to
    the budget. Every boundary therefore lands on that separator even when doing
    so badly under-fills a chunk: with 600-character paragraphs against a 1024
    budget only one paragraph fits, so chunks run at 59% and chunk size tracks
    document structure instead of the budget. **That is precisely the failure
    CLAUDE.md 8.7b measured and rejected for ``chunk_markdown``** - chunk
    precision 0.163 -> 0.100 when section boundaries drove the size.

    Searching only ``[pos - lookback, pos]`` avoids it for free: the lookback IS
    a minimum-fill floor, 75% of the budget at the shipped share of 0.25. A
    structural boundary wins when one is conveniently placed, and a weaker
    boundary is accepted rather than shrinking the chunk.
    """

    # Priority ladder for `_find_boundary`, highest first. The top and bottom
    # rungs are LangChain's defaults; the sentence and clause rungs between them
    # are this codebase's, kept because prose splits better on a sentence end
    # than on an arbitrary space.
    #
    # The bottom rungs are what this list was missing, and they are not
    # cosmetic. Measured on tests/eval/corpus_squad, chunk_size=1024,
    # lookback=256:
    #
    #      7 rungs (before)   blind cuts 6.13%   MID-WORD 3.67%  (70 chunks)
    #     11 rungs (now)      blind cuts 0.63%   MID-WORD 0.00%  (0)
    #
    # With no word rung a failed search cuts at `pos`, which lands INSIDE A WORD
    # and hands the embedder a split token. LangChain's ladder ends with a space
    # and then the empty string for exactly this reason.
    #
    # No Markdown rung on purpose. Heading-first chunking was built, measured
    # worse twice and reverted (8.7b, A3); adding it here needs its own
    # evidence, not a reference implementation's say-so.
    _SEPARATORS: tuple[str, ...] = (
        "\n\n",
        "\n",
        ". ",
        "! ",
        "? ",
        ".\n",
        "!\n",
        "?\n",
        "; ",
        ", ",
        " ",
    )

    def __init__(self, chunk_size: int, chunk_overlap: int, prefix: str):
        self.chunk_size = chunk_size if chunk_size > 0 else 500
        self.chunk_overlap = min(max(0, chunk_overlap), self.chunk_size - 1)
        # Scaled off chunk_size rather than the 100-character literal this
        # used to carry. That constant left ~40% of boundaries on real prose
        # falling mid-sentence at EVERY chunk size, because 100 characters is
        # often less than one sentence of Wikipedia-grade text. See
        # settings.chunk_boundary_lookback_share.
        self.boundary_lookback = max(
            1, int(self.chunk_size * settings.chunk_boundary_lookback_share)
        )
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
            end = self._find_boundary(self.buffer, raw_end, self.boundary_lookback)

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
    def _find_boundary(text: str, pos: int, lookback: int = 100) -> int:
        """Back up from `pos` to the nearest sentence or paragraph end.

        Returns `pos` UNCHANGED when the window holds no boundary, which is a
        blind mid-sentence cut. That is not a rare case on real prose: at the
        100-character literal this used to hardcode, it returned unchanged for
        ~40% of splits on `tests/eval/corpus_squad`, and the rate barely moves
        with chunk_size (39.8% at 512, 39.4% at 1024, 40.5% at 2048) because
        the window is absolute while a sentence is ~100 characters. Callers
        pass `chunk_size * settings.chunk_boundary_lookback_share`; the
        default only keeps the old two-argument signature working.

        Rungs are tried by TYPE in priority order and the LAST occurrence of the
        winning type is taken, so a paragraph break beats a sentence end sitting
        closer to `pos`. See `_SEPARATORS` for the ladder and its provenance.
        """
        floor = max(0, pos - lookback)
        for delim in StreamChunker._SEPARATORS:
            # rfind over a range rather than slicing: same answer, no copy, and
            # a match must fit entirely inside [floor, pos), so the boundary
            # returned can never exceed pos.
            idx = text.rfind(delim, floor, pos)
            if idx != -1:
                return idx + len(delim)
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
        # Derived, not 512. `CodeChunker` computes max_chars = max_tokens * 4,
        # so a literal 512 pinned code chunks at 2048 characters regardless of
        # chunk_size. CLAUDE.md 8.7 A6 recorded the two chunkers disagreeing 4x
        # as a defect and treated chunk_size=2048 as the thing that settled it -
        # which made the agreement a coincidence of one value rather than a
        # rule. At chunk_size=1024 the literal would reopen it at 2x.
        self.code_chunker = CodeChunker(max_tokens=max(1, self.chunk_size // 4))
        # File ids whose summary changed during the current run. Flushed to the
        # LanceDB summary index once the pipeline drains - re-embedding only what
        # this run touched, rather than the whole corpus.
        self._summary_dirty_file_ids: set[int] = set()
        # {absolute path: stored sha256} for the files selected by the current
        # run, so _stream_extract_and_prepare can early-out on unchanged content.
        # Populated by _detect_changes and released with the service instance -
        # every caller builds a throwaway IndexingService per run
        # (app/api/indexing.py, app/indexing/watcher.py), so it does not
        # accumulate. It is O(changed files) resident for the length of a run.
        self._known_hashes: dict[str, str] = {}

    async def shutdown(self) -> None:
        """Release the thread pools this service's pipeline uses.

        Awaitable so the FastAPI lifespan can call it alongside the other
        shutdown steps; the work itself is non-blocking.
        """
        shutdown_executors()

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
            except Exception as exc:
                # index_folders runs as an unhandled FastAPI background task, so
                # this is the only place the failure can be recorded anywhere the
                # caller can see it.
                #
                # Recorded, but NOT returned on. Files whose footers did commit
                # already hold a valid sha256 and matching mtime, so
                # _detect_changes will skip them forever - returning here would
                # skip _flush_file_summaries and drop their summary vectors
                # permanently, and would leave the retrieval cache serving
                # pre-run results. Finishing the tail is what makes the partial
                # work usable.
                logger.error("Indexing run failed: %s", exc, exc_info=True)
                progress.fail(f"{type(exc).__name__}: {exc}")
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

            # Awaited, not backgrounded. `state.bg_tasks` is drained only by the
            # FastAPI lifespan, and it *cancels* rather than awaits - so the
            # checkpoint this run exists to perform was either cancelled at
            # shutdown or raced `DatabaseManager.close()`, logging "WAL
            # checkpoint failed: Cannot operate on a closed database" and
            # leaving the WAL un-truncated. Reclaiming that space is the whole
            # point of the call, and no non-FastAPI entry point drains the set
            # at all.
            #
            # Awaiting matches the rest of the post-run maintenance: fts_optimize
            # in api/indexing.py's _index_then_compact, and both of them in
            # ocr/manager.py's drain. TRUNCATE does wait on readers, but is
            # bounded by PRAGMA busy_timeout (5s, _configure_conn).
            progress.set_current_file("Checkpointing the write-ahead log…")
            await self.db.wal_checkpoint()

            # Removed post-index incremental vacuum to avoid database locks (H-15)

            progress.complete()

    async def _batch_index_pipeline(
        self, files_to_index: list[ScannedFile], offset: int = 0, total_to_index: int = 0
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
            # Must propagate. Swallowing this let index_folders carry on to
            # progress.complete() and report a successful run whose extractor,
            # embedder or storer had died.
            raise
        except Exception as e:
            logger.error("Pipeline stage failed or cancelled:", exc_info=e)
            raise

    async def _rust_pre_extract(self, files_to_index: list[ScannedFile]) -> dict[str, str]:
        pre_extracted: dict[str, str] = {}
        if not RUST_CORE_AVAILABLE:
            return pre_extracted

        rust_paths = []
        for fp, _, _ in files_to_index:
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

        async def _safe_stream_extract(path: Path, tag: str, root: str, cached_text: str | None):
            if progress.is_cancelled:
                return
            nonlocal extracted_count
            async with semaphore:
                async with extracted_lock:
                    extracted_count += 1
                    overall = total_so_far + extracted_count
                progress.set_current_file(f"Extracting: {path.name} ({overall}/{grand_total})")

                await self._stream_extract_and_prepare(path, tag, root, cached_text, embed_queue)

        # Process files_to_index in small chunks of 16 to enforce O(1) memory boundary
        chunk_size = 16
        for i in range(0, len(files_to_index), chunk_size):
            if progress.is_cancelled:
                break
            chunk = files_to_index[i : i + chunk_size]
            chunk_pre_extracted = await self._rust_pre_extract(chunk)

            tasks = []
            for fp, ft, fr in chunk:
                cached_text = chunk_pre_extracted.pop(str(fp.absolute()), None)
                tasks.append(_safe_stream_extract(fp, ft, fr, cached_text))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    logger.error("File extraction failed: %s", res)

            chunk_pre_extracted.clear()

        # Send sentinel
        await embed_queue.put(None)

    async def _stream_extract_and_prepare(
        self,
        path: Path,
        folder_tag: str,
        root_path: str,
        pre_text: str | None,
        queue: asyncio.Queue,
    ) -> None:
        import queue as stdlib_queue

        loop = asyncio.get_running_loop()
        header_sent = False
        try:
            stat = await loop.run_in_executor(_DISK_EXECUTOR, path.stat)
            mtime_iso = datetime.fromtimestamp(stat.st_mtime).isoformat()

            # The hashing pass runs *before* the header is queued, not inside
            # _extract_and_chunk as it used to. The header is what makes the
            # storer call _delete_existing_chunks, so anything that wants to keep
            # this file's existing chunks has to decide before it is sent.
            digest, hash_failed = await loop.run_in_executor(_DISK_EXECUTOR, _hash_file, path)

            # Change detection is mtime-only, so a touch, a git checkout or a
            # sync client rewriting a file re-extracted, re-chunked, re-embedded
            # and re-wrote every chunk of it for no gain. The content hash
            # settles that: identical bytes, nothing to do but record the mtime.
            prior_sha = self._known_hashes.get(str(path.absolute()), "")
            if digest and prior_sha == digest:
                await queue.put({"type": "touch", "path": path, "modified_at": mtime_iso})
                return

            header = {
                "type": "header",
                "path": path,
                "folder_tag": folder_tag,
                "file_data": {
                    "path": str(path.absolute()),
                    "size": stat.st_size,
                    "modified_at": mtime_iso,
                    "type": path.suffix.lower(),
                    "folder_tag": folder_tag,
                    "root_path": root_path,
                    "sha256": "",  # placeholder, updated in footer
                },
            }
            await queue.put(header)
            header_sent = True

            prefix = self._build_context_prefix(str(path))
            logger.info("Started extracting and chunking for file: %s", path)

            bridge: stdlib_queue.Queue[Any] = stdlib_queue.Queue(maxsize=64)
            sentinel = object()
            # Cleared once _pump stops consuming. Without it, the puts below
            # block forever on a full bridge whenever the pump goes away, and a
            # thread already inside an untimed blocking call cannot be
            # cancelled - it parks at zero CPU and concurrent.futures' atexit
            # hook then joins it forever, hanging interpreter exit after all the
            # real work has been committed. progress.is_cancelled does not cover
            # this: the *task* can be cancelled (TaskGroup teardown, shutdown)
            # while the run itself is not.
            pump_alive = threading.Event()
            pump_alive.set()

            def _offer(item: Any) -> bool:
                """Hand one item to the pump.

                False means stop producing: either the run was cancelled, or the
                pump is gone and nothing will ever drain the bridge again.
                """
                while True:
                    try:
                        bridge.put(item, timeout=1.0)
                        return True
                    except stdlib_queue.Full:
                        if progress.is_cancelled or not pump_alive.is_set():
                            return False

            def _extract_and_chunk():
                chunker = StreamChunker(self.chunk_size, self.chunk_overlap, prefix)
                ft_summary = ""
                meta = None

                # CODE ONLY is chunked from the whole file, because an AST needs
                # the complete source and cannot work off a stream. Everything
                # else - markdown included - keeps streaming.
                #
                # This comment used to say "code AND markdown", which was false
                # and had been false since the routing landed: markdown is not in
                # `_WHOLE_TEXT_EXTENSIONS`, so it never buffers and never reaches
                # `_create_chunks`. Section-aware markdown chunking was built,
                # measured worse twice and reverted (CLAUDE.md 8.7b, A3); only
                # the comments were left describing the version that lost.
                #
                # `buffered_chars` against settings.chunk_buffer_max_chars is
                # what keeps the section 6 boundedness invariant true - peak
                # stays a function of the tunables, never of how large a
                # document happens to be. A file over the cap gives up the
                # syntax-aware chunker and streams instead, which is a quality
                # tradeoff rather than a failure.
                buffering = path.suffix.lower() in _WHOLE_TEXT_EXTENSIONS
                buffered: list[str] = []
                buffered_chars = 0

                chunks_emitted = 0
                stub_skipped = False
                stub_kind = ""
                transient_stub = False
                source_size = stat.st_size

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
                            return "CANCELLED", "", None, "cancelled"

                        # Out-of-band extractor signal (which pages need OCR).
                        # Checked before the stub test because ExtractMeta
                        # has no .startswith.
                        if isinstance(fragment, ExtractMeta):
                            meta = fragment
                            continue

                        # Skip extractor stubs — they pollute the vector index
                        # with useless noise. [BINARY: was filtered here already;
                        # [UNREADABLE: (rust_core returns it *as* the file's text
                        # on a read failure) and [ENCRYPTED …: were not, so an
                        # unreadable or password-protected document was indexed
                        # with the error message as its content.
                        if isinstance(fragment, str) and fragment.startswith(_STUB_PREFIXES):
                            logger.debug("Skipping stub for %s — no indexable text.", path)
                            ft_summary = ""
                            stub_skipped = True
                            stub_kind = next(
                                (v for k, v in _STUB_STATUS.items() if fragment.startswith(k)),
                                "skipped",
                            )
                            # "[UNREADABLE:" is rust_core's *transient* I/O
                            # failure stub - an AV lock, a network share blip, a
                            # file held open elsewhere. Treating it as a
                            # deliberate skip would hand the file a real digest
                            # and retire it permanently on a temporary error.
                            transient_stub = fragment.startswith(_TRANSIENT_STUB_PREFIXES)
                            break

                        if buffering:
                            as_text = (
                                fragment
                                if isinstance(fragment, str)
                                else fragment.decode("utf-8", errors="replace")
                            )
                            buffered.append(as_text)
                            buffered_chars += len(as_text)
                            if buffered_chars > settings.chunk_buffer_max_chars:
                                # Over the cap. Give up the whole-text chunker
                                # and replay what has been held through the
                                # streaming one, so nothing read so far is lost.
                                logger.info(
                                    "%s exceeded chunk_buffer_max_chars (%d); "
                                    "streaming it instead of syntax-aware chunking.",
                                    path.name,
                                    settings.chunk_buffer_max_chars,
                                )
                                buffering = False
                                for part in buffered:
                                    for c in chunker.process(part):
                                        if progress.is_cancelled:
                                            return "CANCELLED", "", None, "cancelled"
                                        chunks_emitted += 1
                                        if not _offer(c):
                                            return "CANCELLED", "", None, "cancelled"
                                buffered.clear()
                        else:
                            for c in chunker.process(fragment):
                                if progress.is_cancelled:
                                    return "CANCELLED", "", None, "cancelled"
                                chunks_emitted += 1
                                if not _offer(c):
                                    return "CANCELLED", "", None, "cancelled"
                        if len(ft_summary) < 2000:
                            ft_summary += (
                                fragment
                                if isinstance(fragment, str)
                                else fragment.decode("utf-8", errors="replace")
                            )

                    if buffering:
                        # The syntax-aware path, and in production it is reached
                        # ONLY for code - `_WHOLE_TEXT_EXTENSIONS` is exactly
                        # `_CODE_EXTENSIONS`, so `_create_chunks` always lands on
                        # its CodeChunker branch from here. Its markdown and
                        # .txt branches are reachable from tests only.
                        # This is the call CLAUDE.md 8.7 A2/A3 found had no
                        # production caller at all, and A4's kg_nodes / kg_edges
                        # ride out on the chunks it returns.
                        final_chunks = self._create_chunks("".join(buffered), file_path=str(path))
                        buffered.clear()
                    else:
                        final_chunks = chunker.finalize()

                    for c in final_chunks:
                        if progress.is_cancelled:
                            return "CANCELLED", "", None, "cancelled"
                        chunks_emitted += 1
                        if not _offer(c):
                            return "CANCELLED", "", None, "cancelled"

                    # A scanned document legitimately yields no *native* text:
                    # that is the OCR case, not a failure. It must keep its real
                    # digest, because `files.sha256` is the OCR cache's
                    # content_key (app/ocr/manager.py:407 -> ocr/cache.py). A
                    # sentinel there would be shared by every scanned document in
                    # the corpus and the cache PK would collide across them.
                    ocr_pending = bool(meta is not None and getattr(meta, "ocr_pages", ()))

                    if hash_failed or transient_stub:
                        sha256_result = "ERROR"
                    elif (
                        chunks_emitted == 0
                        and not stub_skipped
                        and not ocr_pending
                        and source_size > 0
                    ):
                        # The completeness invariant: a file is never recorded
                        # with a content hash unless it actually produced
                        # content. Hashing succeeding says nothing about whether
                        # extraction did, so this used to return a valid digest
                        # for a file that yielded nothing - and _detect_changes
                        # then skipped it on every subsequent run, permanently.
                        logger.warning(
                            "No indexable content extracted from %s (%d bytes); "
                            "will retry on the next run.",
                            path,
                            source_size,
                        )
                        sha256_result = "NOCONTENT"
                    else:
                        sha256_result = digest

                    if hash_failed or transient_stub:
                        status = "unreadable" if transient_stub else "error"
                    elif stub_skipped:
                        status = stub_kind
                    elif ocr_pending and chunks_emitted == 0:
                        status = "ocr_pending"
                    elif sha256_result == "NOCONTENT":
                        status = "nocontent"
                    elif chunks_emitted == 0 and source_size == 0:
                        status = "empty"
                    else:
                        status = ""
                    return sha256_result, ft_summary, meta, status
                finally:
                    _offer(sentinel)

            def _bridge_get():
                # Bounded: an untimed get() strands this worker thread the
                # moment _pump is cancelled mid-await, because cancelling the
                # asyncio future does nothing to a thread already inside the
                # call. See the pump_alive comment above.
                return bridge.get(timeout=1.0)

            async def _pump():
                try:
                    while True:
                        try:
                            item = await loop.run_in_executor(_DISK_EXECUTOR, _bridge_get)
                        except stdlib_queue.Empty:
                            # The sentinel is published from a finally, so it
                            # only goes missing if the extract worker died or
                            # its bounded put gave up. Exiting on the future
                            # instead means the pump no longer depends on it.
                            if extract_future.done() and bridge.empty():
                                break
                            continue
                        if item is sentinel:
                            break
                        await queue.put({"type": "chunk", "path": path, "chunk": item})
                finally:
                    pump_alive.clear()

            extract_future = loop.run_in_executor(_EXTRACT_EXECUTOR, _extract_and_chunk)
            await _pump()
            sha256, full_text_for_summary, extract_meta, extract_status = await extract_future
            if sha256 in ("ERROR", "NOCONTENT"):
                progress.record_failure(f"{path.name}: {sha256}")

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
                    "extract_status": "cancelled" if sha256 == "CANCELLED" else extract_status,
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
                            "root_path": root_path,
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
                        "extract_status": "error",
                        "extract_meta": None,
                    }
                )

    def _extract_plain_text_stream(self, path: Path) -> Iterator[str]:
        """Last-resort reader for any extension no extractor claimed.

        Gated on a binary sniff. Without it this opened *anything* as
        utf-8/errors="replace" - and `settings.supported_extensions` contains
        types that are not in TEXT_EXTENSIONS (.rtf, .odt, .ipynb), so they never
        met rust_core's binary check either. An .odt is a zip: it was chunked
        into U+FFFD noise, embedded, and stored under a valid content hash.

        Emits the same "[BINARY:" stub rust_core uses rather than yielding
        nothing, so the file is recorded as deliberately skipped instead of
        being re-attempted on every subsequent run.
        """
        try:
            with open(path, "rb") as fb:
                head = fb.read(8192)
            if _looks_binary(head):
                logger.debug("Plain-text fallback: %s looks binary, not indexing.", path)
                yield f"[BINARY: {path}] Binary content not indexed."
                return

            # Passing the binary gate is not enough: UTF-16 read as UTF-8 still
            # decodes to U+FFFD noise, which is the same defect one layer down.
            with open(path, encoding=_encoding_for(head), errors="replace") as f:
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
        texts = [
            chunk_embedding_text(
                item["chunk"]["text_preview"], str(item["path"]), settings.embed_chunk_prefix
            )
            for item in batch_items
        ]
        if not texts:
            return

        unique_paths = list(set(str(item["path"].name) for item in batch_items))
        logger.info(
            "Embedding batch of %d chunks for files: %s", len(texts), ", ".join(unique_paths)
        )

        def report_progress(batch_num, total_batches):
            if update_progress and progress.status != "idle":
                progress.set_current_file(
                    f"Phase 2/3: Embedding chunks ({batch_num}/{total_batches})…"
                )

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
                elif ptype == "touch":
                    # Content is byte-identical; only the mtime moved. Recording
                    # it stops the next run re-hashing this file forever.
                    await self.db.execute_write(
                        "UPDATE files SET modified_at = ? WHERE path = ?",
                        (item["modified_at"], path_str),
                    )
                    progress.record_unchanged()
                    progress.update(0, current_file=item["path"].name)
                elif ptype == "footer":
                    file_info = active_files.pop(path_str, None)
                    if file_info:
                        await self.db.execute_write(
                            "UPDATE files SET summary = ?, sha256 = ?, extract_status = ? "
                            "WHERE id = ?",
                            (
                                item["summary"],
                                item.get("sha256", ""),
                                item.get("extract_status", ""),
                                file_info["id"],
                            ),
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
                # get_ocr_if_ready, never get_ocr: constructing the manager
                # here would build the API layer's module-global
                # DatabaseManager - a second one on this same file - which no
                # non-FastAPI entry point closes. aiosqlite's worker threads
                # are not daemons, so that leak hung the process at
                # threading._shutdown after a full, committed indexing run.
                # The app builds the manager during lifespan startup, so it is
                # present there; elsewhere there is no worker to kick and the
                # durable ocr_queue row is already written.
                from app.api.deps import get_ocr_if_ready

                ocr = get_ocr_if_ready()
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
                # CodeGraphExtractor's contract is {"id", "label", "name", ...}
                # where `label` is the KIND ("class"/"function") and `name` is
                # the identifier. The row is (id, type, label, ...), so the kind
                # belongs in `type` and the name in `label`.
                #
                # This used to store a constant "entity" as the type and the
                # kind as the label, discarding the name altogether - which also
                # broke edge resolution silently: resolve_pending_graph_edges
                # matches `kg_nodes.label = substr(target, 10)`, i.e. against the
                # pending *name*, so with the kind in that column nothing could
                # ever match and every PENDING:: edge was deleted by its cleanup
                # step. Only same-file CONTAINS edges survived. Invisible until
                # now because nothing wrote to these tables at all (8.7 A4).
                kg_nodes_data.append(
                    (node["id"], node.get("label", "entity"), node.get("name", ""), props, chunk_id)
                )

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
        old_ids = [str(cid) for cid in await self.db.get_file_chunk_ids(file_id)]
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

        Runs concurrently with an index run by writing inside a SAVEPOINT, so
        it can neither commit nor roll back a transaction the indexer holds
        open on the same connection. See the savepoint comment below for what
        that does and does not guarantee.

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

        # A SAVEPOINT, not begin_transaction()/commit().
        #
        # OCR results arrive long after the run that queued them, so this can
        # execute while an index run holds a transaction open on the same write
        # connection. begin_transaction() no-ops when _in_external_transaction
        # is already set, and the commit() that used to follow then committed
        # *the indexer's* transaction and cleared the flag - or, on the failure
        # path, rollback_transaction() discarded the indexer's uncommitted
        # chunks. Both are cross-writer corruption, and the docstring's claim
        # that this is "safe to run concurrently" rested on a guard that did not
        # exist.
        #
        # A savepoint nests: RELEASE and ROLLBACK TO leave any enclosing
        # transaction exactly as they found it, and behave like COMMIT/ROLLBACK
        # when there is none.
        #
        # Residual, deliberately not solved here: the write lock is released
        # between the statements below, so an interleaved indexer commit can
        # still make this savepoint's partial work durable. That is a property
        # of sharing one write connection, not of the savepoint - but it is a
        # far narrower failure than committing or discarding another writer's
        # transaction outright.
        #
        # _flush_pending_chunks_sqlite always defers its commit to the caller,
        # and the delete joins the same scope (auto_commit=False), so the
        # rollback restores the previous OCR text rather than leaving the file
        # with nothing.
        savepoint = await self.db.begin_savepoint()
        try:
            if replace_existing_ocr:
                await self.db.delete_ocr_chunks(file_id, auto_commit=False)
            result = await self._flush_pending_chunks_sqlite(items, active_files)
            await self.db.release_savepoint(savepoint)
        except Exception:
            try:
                await self.db.rollback_savepoint(savepoint)
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

    def _scan_all_folders(self, unique_folders: list[Path]) -> tuple[list[ScannedFile], str, float]:
        if RUST_CORE_AVAILABLE:
            return self._scan_all_folders_rust(unique_folders)
        return self._scan_all_folders_python(unique_folders)

    def _scan_all_folders_rust(
        self, unique_folders: list[Path]
    ) -> tuple[list[ScannedFile], str, float]:
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
                root = ""
                for f_res, f_name in resolved_folders:
                    try:
                        p_obj.resolve().relative_to(f_res)
                        tag = f_name
                        # `relative_to` succeeded, so this is a genuine string
                        # prefix of the path we are about to store. That is what
                        # `delete_files_by_folder_prefix` matches on.
                        root = str(f_res)
                        break
                    except ValueError:
                        pass
                all_files.append((p_obj, tag, root))
            return all_files, "rust_jwalk", (time.perf_counter() - t0) * 1000
        except Exception:
            return self._scan_all_folders_python(unique_folders)

    def _scan_all_folders_python(
        self, unique_folders: list[Path]
    ) -> tuple[list[ScannedFile], str, float]:
        all_files, seen_paths = [], set()
        scan_dur = 0.0
        for f in unique_folders:
            res = fast_scan(f, self.supported_extensions)
            scan_dur += res.duration_ms
            for fp in res.files:
                abs_p = str(fp.resolve())
                if abs_p not in seen_paths:
                    seen_paths.add(abs_p)
                    all_files.append((fp, f.name, str(f)))
        return all_files, "scandir", scan_dur

    async def _detect_changes(
        self, all_files: list[ScannedFile], reader_conn: aiosqlite.Connection
    ) -> tuple[list[ScannedFile], int, int, int]:
        file_paths = [str(fp.absolute()) for fp, _, _ in all_files]
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
            stat_tasks = [_stat_file(fp) for fp, _, _ in batch]
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

        self._known_hashes = {}
        for fp, tag, root in all_files:
            key = str(fp.absolute())
            if key in failed_paths:
                skipped += 1
                continue
            mtime = stat_map.get(key, "")
            stored = change_map.get(key)
            if stored and stored[0] == mtime and stored[1] not in _INCOMPLETE_SHA_STATES:
                skipped += 1
            elif stored:
                changed_c += 1
                to_index.append((fp, tag, root))
                # Only a complete prior hash can authorise the early-out.
                if stored[1] not in _INCOMPLETE_SHA_STATES:
                    self._known_hashes[key] = stored[1]
            else:
                new_c += 1
                to_index.append((fp, tag, root))
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
                # NOT REACHED IN PRODUCTION - tests only. Both branches below
                # need this function to be called with a .md/.txt/.log path, and
                # the only production caller is the buffering branch of
                # `_extract_and_chunk`, which is gated on
                # `_WHOLE_TEXT_EXTENSIONS = _CODE_EXTENSIONS`. Prose streams
                # through StreamChunker instead and never arrives here.
                #
                # Kept rather than deleted because `chunk_markdown` is exercised
                # by tests/test_chunker.py and is the thing A3 would re-route if
                # a corpus is ever found where section boundaries carry more
                # signal than they do here - it was measured worse twice
                # (CLAUDE.md 8.7b) and is open by choice, not by oversight.
                #
                # What it does when a test calls it: splits on `^#{1,3}\s`, emits
                # a whole section as one chunk when it fits, merges adjacent
                # sections up to the budget, and falls back to the sliding window
                # for oversized ones. .txt/.log have no section structure to
                # exploit and take the plain window.
                if ext in (".md", ".markdown"):
                    chunks = rust_core.chunk_markdown(
                        text, self.chunk_size, self.chunk_overlap, prefix
                    )
                else:
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
        return build_context_prefix(file_path)
