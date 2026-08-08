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
* **Never write to SQLite while indexing runs.** `DatabaseManager` releases its
  write lock between `begin_transaction()` and `commit()`, so an OCR write
  landing mid-run would commit the indexing pipeline's open transaction.
  The drain loop waits for idle instead. It deliberately does *not* take
  `indexing_lock` - `index_folders` bails out immediately when that is held,
  which would silently turn the user's Index click into a no-op.
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
    ensure_dirs,
    is_tier_installed,
    load_persisted_state,
    ocr_models_dir,
    ocr_python,
    ocr_scratch_dir,
    ocr_worker_dir,
)
from app.ocr.types import OcrPage, OcrQueueRow

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000

# Single-threaded on purpose: one worker, one reader. A pool would let two
# readline() calls race for the same pipe.
_OCR_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pma-ocr")
_OCR_ERR_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pma-ocr-err")

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
        self._docs_done = 0
        self._pages_done = 0
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

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Begin draining. Never raises - a broken OCR install must not block boot."""
        try:
            # A previous loop's drain task (e.g. an earlier lifespan in the
            # same process) must not be left running alongside a new one.
            if self._task is not None and not self._task.done():
                await self.stop()

            ensure_dirs()
            registry.sweep_stale_installs()
            # An installed venv outranks whatever PMA_OCR_TIER says.
            load_persisted_state()
            if is_tier_installed():
                registry.sync_worker_files()
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
        from app.indexing.service import progress

        while not self._stopping:
            try:
                if self._fatal:
                    await self._sleep_or_kick(_DISABLED_POLL_S)
                    continue

                if not settings.ocr_enabled or not is_tier_installed():
                    await self._retire_worker("disabled")
                    await self._sleep_or_kick(_DISABLED_POLL_S)
                    continue

                # See the module docstring: writing during an index run would
                # commit the pipeline's open transaction.
                if progress.status != "idle":
                    await asyncio.sleep(_BUSY_POLL_S)
                    continue

                row = await ocr_queue.claim_next(
                    self.db, max_attempts=settings.ocr_max_attempts
                )
                if row is None:
                    await self._retire_worker("idle")
                    await self._finalize_indexes()
                    await self._sleep_or_kick(_IDLE_POLL_S)
                    continue

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

        cached = await ocr_cache.get_pages(self.db, content_key, list(row.pages))
        todo = [p for p in row.pages if p not in cached]

        fresh: list[OcrPage] = []
        error_code = ""

        if todo:
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

            if fresh:
                await ocr_cache.put_pages(self.db, content_key, fresh)
                self._pages_done += len(fresh)
                if self._pages_done >= 100:
                    self._pages_done = 0
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
                logger.error("Failed to index OCR results for %s: %s", path.name, exc)
                await ocr_queue.mark_failed(
                    self.db, row.file_path, f"indexing failed: {exc}", terminal=True
                )
                return

        if error_code and not all_pages:
            terminal = row.attempts >= settings.ocr_max_attempts
            self._last_error = f"{path.name}: {error_code}"
            await ocr_queue.mark_failed(self.db, row.file_path, error_code, terminal=terminal)
        else:
            await ocr_queue.mark_done(self.db, row.file_path, pages_done=len(all_pages))
            self._docs_done += 1

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
                self._docs_done >= settings.ocr_worker_max_docs
                or self._pages_done >= settings.ocr_worker_max_pages
            ):
                await self._retire_worker("recycle")
            else:
                return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_OCR_EXECUTOR, self._spawn_sync)

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

        hello = proto.make_hello(
            models_dir=str(ocr_models_dir()),
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

            self._indexing = IndexingService(
                self.db, self.embedding_service, self.lancedb_client
            )
        return self._indexing
