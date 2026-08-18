"""Supervises the OCR worker subprocess and drains the queue.

Design constraints that shaped this, all learned from the tree rather than
invented:

* **No `asyncio.create_subprocess_exec`.** `tests/conftest.py` forces
  `WindowsSelectorEventLoopPolicy`, and the Selector loop cannot spawn
  subprocesses on Windows. Everything goes through blocking `Popen` calls
  offloaded to a dedicated thread pool.
* **stderr must be drained continuously.** An undrained stderr pipe deadlocks
  the child once the OS buffer fills. `frontend/src-tauri/src/lib.rs` learned
  this the hard way; we do not repeat it.
* **Concurrent Subprocess & Database Safety:** OCR transcription runs
  concurrently with the indexing pipeline. `DatabaseManager` write serialization
  and transaction boundaries ensure database writes (queue claims, cache
  updates, and chunk indexing) do not interfere with indexing transactions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import subprocess  # nosec B404 - argv built from module constants and resolved paths
import sys
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.config import settings
from app.ocr import cache as ocr_cache
from app.ocr import protocol as proto
from app.ocr import queue as ocr_queue
from app.ocr import registry
from app.ocr.settings import (
    GPU_TIER,
    VLM_TIER,
    ensure_dirs,
    is_tier_installed,
    load_persisted_state,
    ocr_models_dir,
    ocr_python,
    ocr_scratch_dir,
    ocr_tier_models_dir,
    ocr_worker_dir,
)
from app.ocr.types import OcrPage, OcrQueueRow

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000

# Single-threaded on purpose: one worker, one reader. A pool would let two
# readline() calls race for the same pipe.
_OCR_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pma-ocr")
_OCR_ERR_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pma-ocr-err")


def _format_page_ranges(pages: list[int], max_items: int = 5) -> str:
    """Turn a list of page integers into compact ranges like '41-200, 205'.

    Keeps output concise to prevent log or database column blowups.
    """
    if not pages:
        return ""
    sorted_pages = sorted(set(pages))
    ranges: list[str] = []
    start = sorted_pages[0]
    end = start

    for p in sorted_pages[1:]:
        if p == end + 1:
            end = p
        else:
            ranges.append(f"{start}-{end}" if start != end else f"{start}")
            start = end = p
    ranges.append(f"{start}-{end}" if start != end else f"{start}")

    if len(ranges) > max_items:
        return ", ".join(ranges[:max_items]) + f" (+{len(ranges) - max_items} more)"
    return ", ".join(ranges)


_READY_TIMEOUT_S = 60
_IDLE_POLL_S = 30.0
_BUSY_POLL_S = 2.0
_DISABLED_POLL_S = 30.0

#: How often the read loop wakes to re-check deadlines. `readline()` blocks
#: until a line arrives, so a silent worker would otherwise never be timed out
#: - the checks at the top of the loop simply never run.
_READ_TICK_S = 0.5


class OcrManager:
    def __init__(self, db, embedding_service, lancedb_client) -> None:
        self.db = db
        self.embedding_service = embedding_service
        self.lancedb_client = lancedb_client

        self._proc: subprocess.Popen | None = None
        # A readline() already submitted to the executor. Held across loop
        # iterations so a tick that times out does not queue a second read
        # behind the first - the pool has exactly one thread.
        self._pending_read: Any = None
        self._stderr_tail: deque[str] = deque(maxlen=200)
        #: When the queue last drained with a worker still up, for the
        #: ocr_worker_idle_timeout_s linger. None when not idling.
        self._idle_since: float | None = None
        # Three distinct lifetimes, previously collapsed into two counters that
        # reset on unrelated schedules - which silently disabled recycling.
        #   _docs_done         : this queue pass, for _finalize_indexes + status
        #   _pages_since_evict : rolling 100, drives ocr_cache LRU eviction
        #   _*_since_spawn     : this worker process, drives recycling only
        self._docs_done = 0
        self._pages_since_evict = 0
        self._docs_since_spawn = 0
        self._pages_since_spawn = 0
        self._fatal: str = ""
        self._stopping = False
        self._task: asyncio.Task | None = None
        # Created lazily rather than here: asyncio primitives bind to the loop
        # they are first used on, and this manager is a process-wide singleton
        # that can outlive a loop (notably across tests).
        self._kick: asyncio.Event | None = None
        self._kick_loop: Any = None
        self._indexing: Any = None
        self._current_file: str = ""
        self._last_error: str = ""
        #: Engine identity as reported by the live worker's `ready` message.
        #: Empty until a worker has handshaked in this session, at which point
        #: it supersedes the install stamp for both cache reads and writes.
        self._engine_id: str = ""
        #: True once a reported identity has disagreed with the install stamp.
        #: Surfaced through runtime_state so a silently degraded tier is visible.
        self._engine_mismatch: str = ""

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Begin draining. Never raises - a broken OCR install must not block boot."""
        try:
            # A previous loop's drain task (e.g. an earlier lifespan in the
            # same process) must not be left running alongside a new one.
            if self._task is not None and not self._task.done():
                await self.stop()

            ensure_dirs()
            # A new session re-derives the engine from the stamp, then adopts
            # whatever the first handshake actually reports.
            self.reset_engine_identity()
            registry.sweep_stale_installs()
            # An installed venv outranks whatever PMA_OCR_TIER says.
            load_persisted_state()
            if is_tier_installed() and not registry.sync_worker_files():
                # The return value used to be discarded. sync_worker_files is
                # what keeps the venv's worker in step with this build after an
                # upgrade; if the copy fails (locked file, AV, read-only dir)
                # the worker left in place speaks an older protocol. Dispatching
                # to it produces wrong results rather than an error, so refuse
                # to drain at all and let /ocr/resume re-arm once it is fixed.
                self._fatal = proto.E_TIER_NOT_INSTALLED
                logger.error("OCR worker files could not be synced; draining disabled.")
            recovered = await ocr_queue.reset_running(self.db)
            if recovered:
                logger.info("OCR: re-armed %d interrupted document(s).", recovered)

            self._stopping = False
            self._task = asyncio.create_task(self._drain_loop())

            from app import state as app_state

            app_state.bg_tasks.add(self._task)
            self._task.add_done_callback(app_state.bg_tasks.discard)
            logger.info("OCR manager started (tier=%s).", settings.ocr_tier)
        except Exception as exc:
            logger.error("OCR manager failed to start: %s", exc)

    async def stop(self) -> None:
        """Shut down gracefully. Must run before bg_tasks are cancelled.

        A bare task cancel would leave the worker process orphaned - it needs
        an explicit shutdown message and a wait().
        """
        self._stopping = True
        self._kick_event().set()
        if self._task is not None:
            self._task.cancel()
            # CancelledError derives from BaseException, so suppressing only
            # Exception would let it escape and fail shutdown.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        # force=True: cancelling the drain loop can leave a readline() blocked
        # on the pool's only thread, and a graceful shutdown would queue behind it.
        await self._retire_worker("shutdown", force=True)
        logger.info("OCR manager stopped.")

    def _kick_event(self) -> asyncio.Event:
        """The wake-up Event, rebuilt if the running loop has changed."""
        loop = asyncio.get_running_loop()
        if self._kick is None or self._kick_loop is not loop:
            self._kick = asyncio.Event()
            self._kick_loop = loop
        return self._kick

    async def kick(self) -> None:
        """Wake the drain loop immediately (called when an index run finishes)."""
        self._kick_event().set()

    def _record_engine(self, model_version: str | None, ep: str | None) -> None:
        """Adopt the engine identity the worker reports, and flag disagreement.

        The stamp says what the installer verified; this says what actually
        loaded. They diverge when the engine falls back - e.g. a user-supplied
        model in the models dir fails to load and rapidocr quietly uses the
        weights bundled in the wheel instead. Trusting the stamp there would
        file that output under the wrong key, so the report wins.
        """
        from app.ocr.settings import engine_identity, expected_engine_identity

        reported = engine_identity(model_version, ep)
        expected = expected_engine_identity()
        self._engine_id = reported
        if reported != expected:
            self._engine_mismatch = f"expected {expected}, worker reported {reported}"
            logger.warning(
                "OCR engine mismatch: %s. Caching under the reported identity; "
                "the installed tier is not running what its stamp claims.",
                self._engine_mismatch,
            )
        else:
            self._engine_mismatch = ""

    def _active_engine_id(self) -> str:
        """Identity to key the cache on: the live report if we have one.

        Before the first handshake there is no report, and the cache still has
        to be consulted, so fall back to what the install stamp promises.
        """
        from app.ocr.settings import expected_engine_identity

        return self._engine_id or expected_engine_identity()

    def reset_engine_identity(self) -> None:
        """Forget the reported engine. Call when the *install* changes.

        Deliberately not called when a worker is merely recycled: the identity
        describes the installed engine, not the process. Clearing it per-process
        made writes land under the reported id while the next read fell back to
        the stamp, so a worker that disagreed with its stamp never got a cache
        hit again and re-OCR'd the corpus indefinitely.
        """
        self._engine_id = ""
        self._engine_mismatch = ""

    def runtime_state(self) -> dict[str, Any]:
        """Live process state. No database access, so HTTP handlers can compose
        this with counts read through their own injected DB session."""
        return {
            "worker_running": self._proc is not None and self._proc.poll() is None,
            "current_file": self._current_file,
            "docs_this_session": self._docs_done,
            "unhealthy": bool(self._fatal),
            "fatal": self._fatal,
            "last_error": self._last_error,
            # Non-empty when the running engine is not the one the install stamp
            # promises. Without this a degraded tier looks identical to a healthy
            # one in the UI while producing different text.
            "engine_mismatch": self._engine_mismatch,
            "stderr_tail": list(self._stderr_tail)[-20:],
        }

    async def status(self) -> dict[str, Any]:
        counts = await ocr_queue.counts(self.db)
        cache_bytes = await ocr_cache.total_bytes(self.db)
        return {
            **registry.tier_status(),
            **self.runtime_state(),
            "queue": counts,
            "pages_pending": counts.get("pages_pending", 0),
            "cache_mb": round(cache_bytes / (1024 * 1024), 2),
            "cache_max_mb": settings.ocr_cache_max_mb,
        }

    def clear_fatal(self) -> None:
        """Re-arm after the user fixes whatever made the tier unhealthy."""
        self._fatal = ""

    # ── drain loop ───────────────────────────────────────────────────────

    async def _drain_loop(self) -> None:

        while not self._stopping:
            try:
                if self._fatal:
                    await self._sleep_or_kick(_DISABLED_POLL_S)
                    continue

                if not settings.ocr_enabled or not is_tier_installed():
                    await self._retire_worker("disabled")
                    await self._sleep_or_kick(_DISABLED_POLL_S)
                    continue

                row = await ocr_queue.claim_next(
                    self.db,
                    max_attempts=settings.ocr_max_attempts,
                    stale_after_s=self._stale_claim_seconds(),
                )
                if row is None:
                    idle_wait = await self._retire_if_idle_long_enough()
                    await self._finalize_indexes()
                    await self._sleep_or_kick(idle_wait)
                    continue

                self._idle_since = None
                await self._process_doc(row)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("OCR drain loop error: %s", exc, exc_info=True)
                self._last_error = str(exc)[:300]
                await asyncio.sleep(_BUSY_POLL_S)

    async def _sleep_or_kick(self, timeout: float) -> None:
        event = self._kick_event()
        event.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=timeout)

    async def _finalize_indexes(self) -> None:
        """Once per drained queue, not once per document."""
        if not self._docs_done:
            return
        done = self._docs_done
        self._docs_done = 0
        try:
            await self.lancedb_client.create_hnsw_index("pma_chunks")
        except Exception as exc:
            logger.debug("HNSW index build after OCR skipped: %s", exc)
        for coro_name in ("fts_optimize", "wal_checkpoint"):
            fn = getattr(self.db, coro_name, None)
            if fn is None:
                continue
            try:
                await fn()
            except Exception as exc:
                logger.debug("%s after OCR skipped: %s", coro_name, exc)
        logger.info("OCR queue drained (%d document(s) this pass).", done)

    # ── per-document ─────────────────────────────────────────────────────

    async def _run_document_vlm(self, path: Path, pages: list[int]) -> tuple[list[OcrPage], str]:
        """Transcribe pages with the selected vision model. Returns (pages, error).

        Sequential on purpose. The model is the user's own, usually the same one
        serving chat, and on a 4 GB card it is already spilling layers to CPU -
        issuing concurrent requests would contend for the GPU it is running on
        and slow the whole machine down rather than speed this up.

        Partial results are kept on timeout, matching the worker contract: what
        has been transcribed is worth indexing even if the document as a whole
        ran out of budget.
        """
        from app.ocr.raster_png import RasterError, open_pdf
        from app.ocr.vlm_engine import VlmNotConfiguredError, recognize_page

        capped = pages[: settings.ocr_vlm_max_pages_per_doc]
        if len(capped) < len(pages):
            logger.info(
                "OCR (VLM): %s has %d pages, transcribing the first %d "
                "(ocr_vlm_max_pages_per_doc).",
                path.name,
                len(pages),
                len(capped),
            )

        done: list[OcrPage] = []
        deadline = time.monotonic() + settings.ocr_vlm_doc_timeout_s

        # Opened once for the whole document. render_page_png opens, renders and
        # closes per call, so this loop used to re-parse the entire PDF for
        # every page - 50 full parses for a 50-page document.
        try:
            doc_ctx = open_pdf(path)
            handle = doc_ctx.__enter__()
        except RasterError as exc:
            # Cannot open at all: every requested page fails the same way it
            # would have individually, so the shape the indexer sees is
            # unchanged.
            logger.warning("OCR (VLM): cannot open %s: %s", path.name, exc)
            return [OcrPage(page_num=p, error="RASTER_FAILED") for p in capped], ""

        try:
            for page_num in capped:
                if self._stopping:
                    break
                if time.monotonic() >= deadline:
                    logger.warning(
                        "OCR (VLM): %s hit the document budget after %d page(s).",
                        path.name,
                        len(done),
                    )
                    return done, proto.E_OCR_DOC_TIMEOUT
                try:
                    done.append(await recognize_page(path, page_num, doc=handle))
                except VlmNotConfiguredError as exc:
                    # Fatal for the tier, not for this document: nothing will
                    # succeed until the user picks a model again.
                    logger.error("OCR (VLM) is not configured: %s", exc)
                    return done, proto.E_TIER_NOT_INSTALLED
        finally:
            with contextlib.suppress(Exception):
                doc_ctx.__exit__(None, None, None)

        return done, ""

    async def _retire_if_idle_long_enough(self) -> float:
        """Keep a drained worker alive briefly. Returns how long to sleep.

        The worker was killed the instant claim_next returned nothing, so under
        the normal one-file-at-a-time watcher pattern every document paid a
        fresh venv start plus an ONNX model load. `ocr_worker_idle_timeout_s`
        has been in config since the tier work and was read nowhere.

        Not applied on the GPU tier: a resident DirectML session holds VRAM and
        Tier 2 already measures ~5.2 GB against a 4 GB target, so lingering
        there trades a cold start for pressure on the budget that actually
        binds.
        """
        if self._proc is None or self._proc.poll() is not None:
            self._idle_since = None
            return _IDLE_POLL_S

        if settings.ocr_tier == GPU_TIER:
            await self._retire_worker("idle")
            self._idle_since = None
            return _IDLE_POLL_S

        now = time.monotonic()
        if self._idle_since is None:
            self._idle_since = now

        remaining = max(1, int(settings.ocr_worker_idle_timeout_s)) - (now - self._idle_since)
        if remaining <= 0:
            await self._retire_worker("idle")
            self._idle_since = None
            return _IDLE_POLL_S
        # Wake when the linger expires rather than a poll later, so the timeout
        # means what it says instead of rounding up to _IDLE_POLL_S.
        return min(_IDLE_POLL_S, remaining)

    def _stale_claim_seconds(self) -> int:
        """How long a `running` row may sit before another pass may reclaim it.

        Keyed to the active tier's document timeout rather than a constant:
        ocr_vlm_doc_timeout_s is 7200s against 600s for the worker tiers
        (config.py:148, :165), so one fixed value would either never reclaim a
        worker-tier row or would steal a VLM document mid-run. Doubled, because
        a document that hits its own timeout is killed and reported normally -
        anything still `running` past twice that had no one left to report it.
        """
        base = (
            settings.ocr_vlm_doc_timeout_s
            if settings.ocr_tier == VLM_TIER
            else settings.ocr_doc_timeout_s
        )
        return max(int(base) * 2, 300)

    def _with_stderr_tail(self, code: str) -> str:
        """Attach the worker's dying words to a crash code.

        _spawn_sync clears _stderr_tail and the drain loop spawns the next
        worker immediately, so the traceback explaining a crash was gone before
        anyone could read it - runtime_state() only ever exposed the tail of
        whichever worker is current. Folding it into the failure reason gets it
        into ocr_queue.last_error, which the queue UI already renders.
        """
        if code != proto.E_WORKER_CRASHED or not self._stderr_tail:
            return code
        return f"{code}: {' | '.join(list(self._stderr_tail)[-3:])}"

    async def _process_doc(self, row: OcrQueueRow) -> None:
        path = Path(row.file_path)
        self._current_file = path.name

        file_row = await self.db.get_file_by_path(row.file_path)
        if file_row is None:
            await ocr_queue.mark_skipped(self.db, row.file_path, "file no longer indexed")
            return

        try:
            content_key = file_row["sha256"] or ""
        except (KeyError, IndexError):
            content_key = ""

        # The hashing pass writes these when it fails or is interrupted. They
        # identify no content, so caching under them would poison other files.
        if not ocr_cache.is_valid_content_key(content_key):
            await ocr_queue.mark_skipped(self.db, row.file_path, "no usable content hash")
            return

        if not path.is_file():
            await ocr_queue.mark_skipped(self.db, row.file_path, "file missing on disk")
            return

        # force_ocr is the documented escape hatch for the gate's blind spot
        # (gate.py:9-11): a page whose text layer looked native but was garbage.
        # Consulting the cache would hand back exactly the text the user is
        # trying to override, which made the whole feature inert - the API wrote
        # the flag, the row carried it, and nothing ever read it. The re-OCR
        # then overwrites the stale rows, since put_pages is INSERT OR REPLACE.
        cached: dict[int, OcrPage] = {}
        if row.force_ocr:
            logger.info(
                "OCR forced for %s - ignoring %d cached page(s).", path.name, len(row.pages)
            )
        else:
            cached = await ocr_cache.get_pages(
                self.db, content_key, list(row.pages), engine_id=self._active_engine_id()
            )
        todo = [p for p in row.pages if p not in cached]

        fresh: list[OcrPage] = []
        error_code = ""

        if todo and settings.ocr_tier == VLM_TIER:
            fresh, error_code = await self._run_document_vlm(path, todo)
        elif todo:
            ensure_dirs()
            doc_id = f"d-{uuid.uuid4().hex[:12]}"
            ndjson_path = ocr_scratch_dir() / f"{doc_id}.ndjson"
            try:
                error_code = await self._run_document(doc_id, path, todo, ndjson_path)
                # Read the NDJSON regardless of how the run ended. This is what
                # makes "partial results are always indexed" true: a killed
                # worker still leaves everything it finished on disk.
                fresh = await self._read_ndjson(ndjson_path)
            finally:
                with contextlib.suppress(OSError):
                    ndjson_path.unlink(missing_ok=True)

        # Outside the branch above: both the worker path and the VLM path
        # produce `fresh`, and both must be cached the same way.
        if fresh:
            # Written under whatever the engine reported, never under what the
            # stamp claimed - the run has completed by now, so a handshake has
            # happened and _engine_id reflects reality.
            await ocr_cache.put_pages(
                self.db, content_key, fresh, engine_id=self._active_engine_id()
            )
            self._pages_since_spawn += len(fresh)
            self._pages_since_evict += len(fresh)
            if self._pages_since_evict >= 100:
                self._pages_since_evict = 0
                await ocr_cache.evict_lru(self.db)

        all_pages = list(cached.values()) + fresh
        if all_pages:
            try:
                indexed = await self._indexing_service().index_ocr_pages(path, all_pages)
                await ocr_queue.mark_progress(self.db, row.file_path, len(all_pages))
                logger.info(
                    "OCR %s - %d/%d pages, %d chunk(s)%s",
                    path.name,
                    len(all_pages),
                    row.page_count or len(row.pages),
                    indexed,
                    f", error {error_code}" if error_code else "",
                )
            except Exception as exc:
                from app.indexing.service import progress

                # index_ocr_pages raises outright if an index run started after
                # the drain loop's idle check (service.py:1012). That is someone
                # else's timing, not a bad document, and it heals by itself -
                # terminally failing it here retired documents for good.
                if isinstance(exc, RuntimeError) and progress.status != "idle":
                    logger.info("OCR: deferring %s, indexing started mid-write.", path.name)
                    await ocr_queue.release_claim(self.db, row.file_path)
                    return
                logger.error("Failed to index OCR results for %s: %s", path.name, exc)
                await ocr_queue.mark_failed(
                    self.db, row.file_path, f"indexing failed: {exc}", terminal=True
                )
                return

        # A page carrying an error is not content, however successful the
        # document-level run looked.
        failed_pages = [p for p in all_pages if p.error]
        ok_pages = [p for p in all_pages if not p.error]

        # The pages actually queued for OCR, NOT the document's page count.
        # row.page_count is the whole PDF (service.py passes meta.page_count
        # alongside meta.ocr_pages), so for a mixed native/scanned document
        # this used to compare 3 recovered pages against a 10-page document:
        # a fully successful run read as incomplete, was retried to
        # exhaustion, then marked done with "3/10 pages, incomplete" - a
        # message whose two halves contradicted each other, because `missing`
        # was correctly empty.
        expected_count = len(row.pages)
        if error_code and len(all_pages) < expected_count:
            # Document encountered a crash, OOM, or timeout mid-run.
            terminal = row.attempts >= settings.ocr_max_attempts
            reason = self._with_stderr_tail(error_code)
            missing = [p for p in row.pages if p not in {pg.page_num for pg in all_pages}]
            missing_str = (
                f"missing pages {_format_page_ranges(missing)}" if missing else "incomplete"
            )
            self._last_error = (
                f"{path.name}: {reason} ({len(all_pages)}/{expected_count} pages, {missing_str})"
            )
            if terminal:
                if all_pages:
                    # Retries exhausted, but partial progress was indexed and cached.
                    # Mark done with actual completed page count and informative last_error.
                    detail = f"OCR incomplete: {len(all_pages)}/{expected_count} pages, {missing_str} ({reason})"
                    await ocr_queue.mark_done(
                        self.db, row.file_path, pages_done=len(all_pages), last_error=detail
                    )
                    self._docs_done += 1
                    self._docs_since_spawn += 1
                else:
                    await ocr_queue.mark_failed(self.db, row.file_path, reason, terminal=True)
            else:
                # Re-arm row for retry; previous finished pages are in ocr_cache,
                # so the next attempt will skip straight to the remaining pages.
                await ocr_queue.mark_failed(self.db, row.file_path, reason, terminal=False)
        elif not all_pages and row.pages:
            # Zero pages back when pages were asked for.
            reason = self._with_stderr_tail(error_code) if error_code else "no pages produced"
            terminal = row.attempts >= settings.ocr_max_attempts
            self._last_error = f"{path.name}: {reason}"
            await ocr_queue.mark_failed(self.db, row.file_path, reason, terminal=terminal)
        elif all_pages and not ok_pages:
            # Every page came back carrying an error, and nothing above caught
            # it. _read_ndjson keeps error records (it filters only
            # page_num >= 0) and _run_document returns "" for any doc_done, so
            # error_code is empty and len(all_pages) matches what was asked for
            # - the document read as a clean success and was marked done with
            # its last_error wiped. The existing regression test covers only the
            # zero-page branch above, where all_pages is empty.
            reason = f"all {len(all_pages)} page(s) failed"
            terminal = row.attempts >= settings.ocr_max_attempts
            self._last_error = f"{path.name}: {reason}"
            await ocr_queue.mark_failed(self.db, row.file_path, reason, terminal=terminal)
        else:
            # Genuine success, possibly partial. Record why when it produced
            # less than it looks like it did, so "done" is not the only signal
            # the user gets for a scan that yielded nothing readable.
            detail = ""
            if failed_pages:
                detail = f"{len(failed_pages)} of {len(all_pages)} page(s) failed"
            elif not any(p.indexable_text for p in ok_pages):
                detail = f"no readable text in {len(ok_pages)} page(s)"
            if detail:
                self._last_error = f"{path.name}: {detail}"
            await ocr_queue.mark_done(
                self.db, row.file_path, pages_done=len(all_pages), last_error=detail
            )
            self._docs_done += 1
            self._docs_since_spawn += 1

        self._current_file = ""

    async def _run_document(
        self, doc_id: str, path: Path, pages: list[int], ndjson_path: Path
    ) -> str:
        """Send one document to the worker. Returns "" or an error code."""
        loop = asyncio.get_running_loop()

        try:
            await self._ensure_worker()
        except Exception as exc:
            logger.error("Could not start OCR worker: %s", exc)
            return proto.E_WORKER_CRASHED

        message = proto.make_doc(
            doc_id=doc_id,
            path=str(path),
            pages=list(pages),
            ndjson_path=str(ndjson_path),
            dpi=settings.ocr_dpi,
        )
        if not await loop.run_in_executor(_OCR_EXECUTOR, self._send_sync, message):
            await self._retire_worker("send failed", force=True)
            return proto.E_WORKER_CRASHED

        deadline = time.monotonic() + settings.ocr_doc_timeout_s
        # A silent worker is as bad as a dead one; page acks are the heartbeat.
        quiet_limit = settings.ocr_page_timeout_s + 15
        last_message = time.monotonic()

        while True:
            if time.monotonic() > deadline:
                await self._retire_worker("doc timeout", force=True)
                return proto.E_OCR_DOC_TIMEOUT
            if time.monotonic() - last_message > quiet_limit:
                await self._retire_worker("page timeout", force=True)
                return proto.E_OCR_PAGE_TIMEOUT

            state, line = await self._next_line()
            if state == "timeout":
                continue  # back to the deadline checks above
            if state == "eof":
                await self._retire_worker("worker exited", force=True)
                return proto.E_WORKER_CRASHED
            if not line.strip():
                continue

            last_message = time.monotonic()
            try:
                msg = proto.decode(line)
            except proto.ProtocolError:
                continue

            kind = msg.get("t")
            if kind == proto.RSP_DOC_DONE:
                return ""
            if kind == proto.RSP_ERROR:
                code = msg.get("code") or proto.E_WORKER_CRASHED
                if proto.is_fatal(code):
                    self._fatal = code
                    logger.error(
                        "OCR tier unhealthy (%s): %s. Draining stopped.",
                        code,
                        msg.get("detail", ""),
                    )
                    await self._retire_worker("fatal", force=True)
                    return code
                if code in proto.DOC_LEVEL_ERRORS:
                    await self._retire_worker(code, force=True)
                    return code
                # Page-level: the worker already skipped it and continues.
                logger.debug("OCR page error %s: %s", code, msg.get("detail", ""))

    async def _read_ndjson(self, ndjson_path: Path) -> list[OcrPage]:
        """Parse whatever the worker managed to write.

        Read whole, then parsed line by line with per-line tolerance: a killed
        process can leave a truncated final line, and that must cost us one
        page rather than the document.
        """
        if not ndjson_path.is_file():
            return []
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(
                _OCR_EXECUTOR, lambda: ndjson_path.read_text(encoding="utf-8", errors="replace")
            )
        except OSError as exc:
            logger.warning("Could not read OCR results at %s: %s", ndjson_path, exc)
            return []

        pages: list[OcrPage] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue  # truncated tail from a kill
            if not isinstance(data, dict):
                continue
            page = OcrPage.from_worker_json(data, settings.ocr_conf_floor)
            if page.page_num >= 0:
                pages.append(page)
        return pages

    # ── worker process ───────────────────────────────────────────────────

    async def _ensure_worker(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            if (
                self._docs_since_spawn >= settings.ocr_worker_max_docs
                or self._pages_since_spawn >= settings.ocr_worker_max_pages
            ):
                await self._retire_worker("recycle")
            else:
                return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_OCR_EXECUTOR, self._spawn_sync)
        # Recycling is per worker process, so its budget resets with the process.
        self._docs_since_spawn = 0
        self._pages_since_spawn = 0

        deadline = time.monotonic() + _READY_TIMEOUT_S
        while time.monotonic() < deadline:
            state, line = await self._next_line()
            if state == "timeout":
                continue
            if state == "eof":
                await self._retire_worker("exited during handshake", force=True)
                raise RuntimeError("worker exited during handshake")
            if not line.strip():
                continue
            try:
                msg = proto.decode(line)
            except proto.ProtocolError:
                continue
            if msg.get("t") == proto.RSP_READY:
                logger.info(
                    "OCR worker ready (%s, %s).",
                    msg.get("model_version", "?"),
                    msg.get("ep", "?"),
                )
                self._record_engine(msg.get("model_version"), msg.get("ep"))
                return
            if msg.get("t") == proto.RSP_ERROR:
                code = msg.get("code") or proto.E_WORKER_CRASHED
                if proto.is_fatal(code):
                    self._fatal = code
                await self._retire_worker(code, force=True)
                raise RuntimeError(f"{code}: {msg.get('detail', '')}")

        await self._retire_worker("handshake timeout", force=True)
        raise RuntimeError("worker did not report ready")

    async def _next_line(self, tick: float = _READ_TICK_S):
        """Wait up to `tick` for one line. Returns ("line"|"eof"|"timeout", line).

        `readline()` blocks until data arrives, so it is submitted once and
        awaited across ticks. Timing out here does NOT cancel the underlying
        read - the executor has one thread, and abandoning it would wedge the
        pool. The thread is released either when a line arrives or when the
        process is killed and readline() returns EOF.
        """
        if self._pending_read is None:
            loop = asyncio.get_running_loop()
            self._pending_read = loop.run_in_executor(_OCR_EXECUTOR, self._readline_sync)

        done, _pending = await asyncio.wait({self._pending_read}, timeout=tick)
        if not done:
            return "timeout", None

        future = self._pending_read
        self._pending_read = None
        try:
            line = future.result()
        except Exception as exc:
            logger.debug("OCR read failed: %s", exc)
            return "eof", None
        if line is None:
            return "eof", None
        return "line", line

    def _spawn_sync(self) -> None:
        """Blocking spawn. Runs on _OCR_EXECUTOR."""
        argv = [str(ocr_python()), "-u", str(ocr_worker_dir() / "__main__.py")]
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "cwd": str(ocr_worker_dir()),
            "shell": False,
        }
        if sys.platform == "win32":
            # CREATE_NO_WINDOW only. Deliberately NOT DETACHED_PROCESS (it
            # breaks the pipes) and NOT CREATE_BREAKAWAY_FROM_JOB (the worker
            # must die with PMA - the Tauri shell puts us in a job object with
            # KILL_ON_JOB_CLOSE precisely so orphans cannot survive).
            kwargs["creationflags"] = CREATE_NO_WINDOW

        self._proc = subprocess.Popen(argv, **kwargs)  # nosec B603 - resolved paths only
        self._stderr_tail.clear()
        _OCR_ERR_EXECUTOR.submit(self._drain_stderr_sync, self._proc)

        # The tier's own weights when it has any, else the shared user drop-in
        # slot. Handing a tier the shared directory unconditionally is how a CPU
        # worker ended up able to load another tier's 194 MB server weights.
        tier_models = ocr_tier_models_dir()
        models_dir = tier_models if tier_models.is_dir() else ocr_models_dir()

        hello = proto.make_hello(
            models_dir=str(models_dir),
            dpi=settings.ocr_dpi,
            conf_floor=settings.ocr_conf_floor,
            page_timeout_s=settings.ocr_page_timeout_s,
        )
        self._send_sync(hello)

    def _drain_stderr_sync(self, proc: subprocess.Popen) -> None:
        """Continuously drain stderr for the process's whole life.

        Not optional: once the OS pipe buffer fills, the worker blocks on its
        next write and never returns.
        """
        stream = proc.stderr
        if stream is None:
            return
        try:
            for line in stream:
                text = line.rstrip()
                if text:
                    self._stderr_tail.append(text)
                    logger.debug("ocr-worker: %s", text)
        except Exception:
            pass

    def _send_sync(self, message: dict) -> bool:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            return False
        try:
            proc.stdin.write(proto.encode(message))
            proc.stdin.flush()
            return True
        except (OSError, ValueError) as exc:
            logger.debug("OCR worker write failed: %s", exc)
            return False

    def _readline_sync(self) -> str | None:
        """One line from the worker, or None once it is gone."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        try:
            line: str = proc.stdout.readline()
        except (OSError, ValueError):
            return None
        if line == "":
            return None  # EOF
        return line

    async def _retire_worker(self, reason: str, *, force: bool = False) -> None:
        """Shut the worker down and release the reader thread.

        `force=True` kills immediately rather than asking politely. That path
        must never queue work on _OCR_EXECUTOR first: when we force-retire, a
        readline() is usually still blocked on the pool's only thread, so
        anything submitted behind it would deadlock. `Popen.kill()` is a
        non-blocking syscall and is therefore safe to call from the event loop;
        it also makes the blocked readline() return EOF, which is what frees
        the thread.
        """
        proc = self._proc
        if proc is None:
            self._pending_read = None
            return
        self._proc = None

        if proc.poll() is None:
            if force:
                try:
                    proc.kill()
                except Exception as exc:
                    logger.debug("OCR worker kill failed: %s", exc)
            else:
                try:
                    if proc.stdin is not None:
                        proc.stdin.write(proto.encode(proto.make_shutdown()))
                        proc.stdin.flush()
                        proc.stdin.close()
                except (OSError, ValueError):
                    pass

        # Let the blocked read observe EOF so the pool thread comes back.
        if self._pending_read is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait({self._pending_read}, timeout=5)
            self._pending_read = None

        await asyncio.get_running_loop().run_in_executor(
            _OCR_EXECUTOR, self._reap_sync, proc, reason
        )

    def _reap_sync(self, proc: subprocess.Popen, reason: str) -> None:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, Exception):
                logger.warning("OCR worker did not die after kill().")
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
        logger.debug("OCR worker retired (%s).", reason)

    def _indexing_service(self):
        """Lazily built - importing at module scope would create a cycle."""
        if self._indexing is None:
            from app.indexing.service import IndexingService

            self._indexing = IndexingService(self.db, self.embedding_service, self.lancedb_client)
        return self._indexing
