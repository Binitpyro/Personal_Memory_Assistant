"""Poll-based folder watcher.

Re-indexes previously indexed folders when their contents change, so the corpus
does not silently drift from the filesystem between manual runs.

**Why polling and not OS filesystem events.** An event-based watcher
(``watchdog``) detects changes in milliseconds, but it would add a third-party
runtime dependency to a project that argues about its dependency tree, and it
brings problems this design does not have: editors emit bursts of write events
for a single save, recursive watches hit per-platform descriptor limits on large
trees, and network or synced folders deliver events unreliably or not at all.

Polling instead reuses machinery that already exists and is already fast:
``scan_folder`` (Rust fast path) enumerates, and ``IndexingService`` does its own
change detection via sha256 and mtime. The watcher therefore does not decide
*what* changed - it only decides *when to ask*. Detection latency equals the
poll interval, which for a document corpus is an acceptable trade for zero new
dependencies and no platform-specific failure modes.

The watcher never indexes a folder the user has not already indexed: it derives
its roots from ``folder_profiles``, which is populated by an explicit index run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Floor on the poll interval, so a mistyped config cannot turn the watcher into
# a busy loop that re-scans the whole corpus continuously. Module-level rather
# than inline so tests can drive the loop without waiting out real intervals.
_MIN_INTERVAL_SECONDS = 10


class FolderWatcher:
    """Periodically re-indexes known folders whose contents have changed.

    One task, started at app startup and cancelled at shutdown. Safe to start
    when disabled - it becomes a no-op rather than a task that wakes up to do
    nothing.
    """

    def __init__(self, db: Any, indexing_service_factory: Any):
        self._db = db
        self._make_service = indexing_service_factory
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        # Observability for tests and the status endpoint.
        self.last_scan_at: float = 0.0
        self.last_indexed_folders: list[str] = []
        self.cycles = 0

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> bool:
        if not settings.watcher_enabled:
            logger.info("Folder watcher disabled (PMA_WATCHER_ENABLED=false).")
            return False
        if self._task is not None and not self._task.done():
            return True

        self._stopping.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("Folder watcher started (interval=%ss).", settings.watcher_interval_seconds)
        return True

    async def stop(self) -> None:
        self._stopping.set()
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        # CancelledError is listed explicitly: it derives from BaseException,
        # not Exception, so suppressing Exception alone lets cancellation
        # escape and makes app shutdown raise.
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    # ── main loop ────────────────────────────────────────────────────────

    async def _run(self) -> None:
        interval = max(_MIN_INTERVAL_SECONDS, settings.watcher_interval_seconds)
        try:
            while not self._stopping.is_set():
                # Wait first: a scan at startup would duplicate the work the
                # user's own startup indexing is already doing.
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=interval)
                    return  # stop() was called
                except TimeoutError:
                    pass

                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # A watcher that dies on one bad cycle is worse than no
                    # watcher: it fails silently and never recovers.
                    logger.error("Folder watcher cycle failed: %s", e, exc_info=True)
        except asyncio.CancelledError:
            logger.info("Folder watcher stopped.")
            raise

    async def run_once(self) -> list[str]:
        """One poll cycle. Returns the folders that were handed to indexing."""
        import time

        from app.indexing.service import indexing_lock

        self.cycles += 1
        self.last_scan_at = time.time()

        if indexing_lock.locked():
            # A manual index is running. Skipping is correct rather than
            # queueing: the next cycle re-derives state from disk anyway, so
            # nothing is lost by waiting one interval.
            logger.debug("Folder watcher: indexing already in progress, skipping cycle.")
            self.last_indexed_folders = []
            return []

        roots = await self._watched_roots()
        if not roots:
            self.last_indexed_folders = []
            return []

        service = self._make_service()
        # index_folders does its own scan and change detection, and is a no-op
        # when nothing changed. Duplicating that logic here would mean two
        # implementations of "has this file changed" that could disagree.
        await service.index_folders(roots)

        self.last_indexed_folders = roots
        return roots

    async def _watched_roots(self) -> list[str]:
        """Folders the user has actually indexed, that still exist on disk."""
        try:
            rows = await self._db.execute_query(
                "SELECT folder_path FROM folder_profiles WHERE folder_path != ''"
            )
        except Exception as e:
            logger.warning("Folder watcher could not read indexed folders: %s", e)
            return []

        roots: list[str] = []
        for row in rows:
            path = row[0]
            if not path:
                continue
            try:
                if Path(path).is_dir():
                    roots.append(path)
                else:
                    # Deleted or unmounted (external drive, network share).
                    # Handing it to the indexer would scan nothing and, worse,
                    # could read as "every file removed".
                    logger.debug("Folder watcher: skipping missing root %s", path)
            except OSError as e:
                logger.debug("Folder watcher: cannot stat %s: %s", path, e)
        return roots
