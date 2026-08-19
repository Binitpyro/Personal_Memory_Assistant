"""The indexing pipeline's worker threads must never park forever.

`app/indexing/service.py` owns two module-level thread pools (`_DISK_EXECUTOR`,
`_EXTRACT_EXECUTOR`). `concurrent.futures.thread` registers an atexit hook that
`join()`s every pool's worker threads **with no timeout**, so a single worker
parked on an unbounded blocking call hangs interpreter exit forever, at zero
CPU, after all the real work has finished and been committed.

That is not hypothetical. A survey run over a 403 MB corpus indexed all 151
files, wrote `folder_profiles`, closed the database - and then sat for 61
minutes consuming no CPU until it was killed.

Three calls could park: the untimed `bridge.get()` in `_pump`, the untimed
`bridge.put(sentinel)` in `_extract_and_chunk`'s `finally`, and the
`stdlib_queue.Full` retry loops, which spun forever once their consumer went
away because they only tested `progress.is_cancelled`. Cancelling the asyncio
future does nothing to a thread already inside any of them.

NOTE for anyone running these as a negative control: with the fix reverted these
do not merely fail, they leave a parked worker behind, so the pytest process
itself will not exit. That is the bug.
"""

import asyncio
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.indexing import service as idx
from tests.test_indexing_service_extended import FakeDB, FakeEmb, FakeLanceDB

# Comfortably above the bridge's maxsize=64, so the bridge is full and the
# extractor is inside its put-retry loop before anything gets cancelled.
_CHUNKS_WANTED = 400


def _make_service() -> idx.IndexingService:
    return idx.IndexingService(FakeDB(), FakeEmb(), FakeLanceDB())


def _corpus_file(tmp_path: Path, name: str, service: idx.IndexingService) -> Path:
    """A file big enough to emit ~`_CHUNKS_WANTED` chunks through StreamChunker."""
    p = tmp_path / name
    body = "alpha bravo charlie delta echo foxtrot golf hotel india juliet. "
    p.write_text(body * (_CHUNKS_WANTED * service.chunk_size // len(body)), encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_cancelling_extraction_leaves_the_extract_pool_usable(tmp_path, monkeypatch):
    """Occupy every `_EXTRACT_EXECUTOR` worker, cancel them, require the pool alive.

    Each file gets its own stalled consumer (`maxsize=1`), so draining exactly
    one item per task proves all of them are past the header and into the chunk
    stream, with a full bridge behind them. Before the fix the retry loop exited
    only on `progress.is_cancelled` - False here, because the *task* was
    cancelled, not the run - so every worker span forever and the pool was dead
    for the life of the process.
    """
    service = _make_service()
    monkeypatch.setattr(idx.progress, "is_cancelled", False)

    workers = idx._EXTRACT_EXECUTOR._max_workers
    queues: list[asyncio.Queue] = [asyncio.Queue(maxsize=1) for _ in range(workers)]

    tasks = [
        asyncio.create_task(
            service._stream_extract_and_prepare(
                _corpus_file(tmp_path, f"doc{i}.txt", service), "tag", None, q
            )
        )
        for i, q in enumerate(queues)
    ]

    for q in queues:
        assert (await asyncio.wait_for(q.get(), timeout=30.0))["type"] == "header"
    await asyncio.sleep(1.0)

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    probe = idx._EXTRACT_EXECUTOR.submit(lambda: "alive")
    assert await asyncio.wait_for(asyncio.wrap_future(probe), timeout=15.0) == "alive"


@pytest.mark.asyncio
async def test_pump_exits_when_the_extractor_finishes_without_a_sentinel(tmp_path, monkeypatch):
    """`_pump` must not depend on the sentinel arriving.

    `_extract_and_chunk` publishes the sentinel from a `finally`, so the only
    ways it goes missing are the extract worker dying and its now-bounded put
    giving up. In both cases the old `_pump` sat on an untimed `bridge.get()`
    for the life of the process. The stub executor here resolves the future
    without ever running the callable, which is what that looks like to `_pump`.
    """
    service = _make_service()
    monkeypatch.setattr(idx.progress, "is_cancelled", False)

    class _NeverRunsExecutor:
        def submit(self, fn, *args, **kwargs):
            fut: concurrent.futures.Future = concurrent.futures.Future()
            fut.set_result(("NOCONTENT", "", None))
            return fut

    monkeypatch.setattr(idx, "_EXTRACT_EXECUTOR", _NeverRunsExecutor())

    path = _corpus_file(tmp_path, "orphan.txt", service)
    queue: asyncio.Queue = asyncio.Queue()

    await asyncio.wait_for(
        service._stream_extract_and_prepare(path, "tag", None, queue), timeout=20.0
    )

    kinds = [queue.get_nowait()["type"] for _ in range(queue.qsize())]
    assert "header" in kinds and "footer" in kinds


@pytest.mark.asyncio
async def test_shutdown_executors_is_idempotent(monkeypatch):
    """Shutdown must be safe to call twice - the FastAPI lifespan and
    `EvalIndex.close()` both call it, and a test session may too.

    Run against throwaway pools so monkeypatch can restore the real ones, then
    assert both halves of the contract: the old pools are retired, and the
    module globals point at usable replacements. Without the replacement, one
    `close()` would make every later indexing run in the process die on "cannot
    schedule new futures after shutdown".

    This is also why shutdown is *not* the fix for the hang - it cannot retire
    a thread already parked inside an untimed call; only bounding those calls
    can.
    """
    disk, extract = ThreadPoolExecutor(max_workers=1), ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(idx, "_DISK_EXECUTOR", disk)
    monkeypatch.setattr(idx, "_EXTRACT_EXECUTOR", extract)

    idx.shutdown_executors()
    idx.shutdown_executors()

    assert disk._shutdown and extract._shutdown
    assert idx._DISK_EXECUTOR is not disk and idx._EXTRACT_EXECUTOR is not extract

    await _make_service().shutdown()
    probe = idx._DISK_EXECUTOR.submit(lambda: "alive")
    assert await asyncio.wait_for(asyncio.wrap_future(probe), timeout=10.0) == "alive"


@pytest.mark.asyncio
async def test_enqueue_ocr_does_not_build_the_api_layer_database_manager(monkeypatch):
    """The indexer must not construct `app.api.deps`' module-global DatabaseManager.

    This is what actually hung the 403 MB survey run. `_maybe_enqueue_ocr` used
    `deps.get_ocr()`, which constructs on demand, and constructing pulls in
    `deps.get_db()` - a *second* DatabaseManager on the same file. The FastAPI
    lifespan closes that one; the eval harness and `scripts/` build their own
    and never touch it. `aiosqlite.Connection` starts its worker with
    `Thread(...)` and no `daemon=True`, so the unclosed connection blocked
    `threading._shutdown` - which runs after the atexit hook - and the process
    sat at zero CPU with every file indexed and committed.

    Only reachable with OCR on and a document that defers pages, which is why
    a corpus of .md and .py files never showed it.
    """
    from app.api import deps
    from app.ocr import queue as ocr_queue

    monkeypatch.setattr(deps, "_db_manager", None)
    monkeypatch.setattr(deps, "_ocr_manager", None)
    monkeypatch.setattr(idx.settings, "ocr_enabled", True)
    monkeypatch.setattr(idx.settings, "ocr_tier", "cpu")

    enqueued: list[str] = []

    async def _fake_enqueue(db, path_str, pages, page_count, tier=""):
        enqueued.append(path_str)

    monkeypatch.setattr(ocr_queue, "enqueue_document", _fake_enqueue)

    footer = {
        "extract_meta": SimpleNamespace(ocr_pages=(0, 1), page_count=2),
        "sha256": "a" * 64,
    }
    await _make_service()._maybe_enqueue_ocr("C:/scanned.pdf", footer)

    assert enqueued == ["C:/scanned.pdf"], "the durable queue row must still be written"
    assert deps._db_manager is None, (
        "the indexer built the API layer's DatabaseManager; its aiosqlite thread "
        "is non-daemon and will block interpreter exit"
    )


@pytest.mark.asyncio
async def test_index_folders_checkpoints_the_wal_before_returning(tmp_path, monkeypatch):
    """The WAL checkpoint must complete during the run, not be fired into
    `state.bg_tasks` and forgotten.

    That set is drained only by the FastAPI lifespan, which *cancels* its tasks
    rather than awaiting them, and no other entry point drains it at all. So the
    checkpoint either never ran or raced `DatabaseManager.close()` - every survey
    run logged "WAL checkpoint failed: Cannot operate on a closed database" and
    left the WAL un-truncated, which is the one thing the call exists to prevent.

    Asserted at the instant `index_folders` returns: a task created but not yet
    scheduled has not run, so the backgrounded version fails here deterministically.
    """
    from app import state
    from app.storage.db import DatabaseManager

    # A real DatabaseManager, not FakeDB: index_folders opens its own reader on
    # self.db.db_path for change detection, so the tail of the run cannot be
    # exercised against a stub.
    mgr = DatabaseManager(str(tmp_path / "ckpt.db"))
    await mgr.init_db(schema_path="app/storage/schema.sql")
    service = idx.IndexingService(mgr, FakeEmb(), FakeLanceDB())

    calls: list[int] = []

    async def _spy():
        calls.append(1)

    monkeypatch.setattr(mgr, "wal_checkpoint", _spy)

    corpus = tmp_path / "docs"
    corpus.mkdir()
    (corpus / "a.md").write_text("# Title\n\nSome indexable prose here.\n", encoding="utf-8")

    before = set(state.bg_tasks)
    idx.progress.status = "idle"
    await service.index_folders([str(corpus)])

    try:
        assert calls == [1], "wal_checkpoint did not complete before index_folders returned"
        assert set(state.bg_tasks) - before == set(), "left a background task behind"
    finally:
        await mgr.close()


@pytest.mark.asyncio
async def test_wal_checkpoint_on_a_closed_manager_logs_instead_of_raising(tmp_path, caplog):
    """`_get_conn()` sat outside the try, so a closed manager raised RuntimeError
    out of a function whose every other failure mode is logged and swallowed.

    Now that `index_folders` awaits the call, an unguarded raise would surface as
    a failed indexing run rather than a warning.
    """
    from app.storage.db import DatabaseManager

    mgr = DatabaseManager(str(tmp_path / "ckpt.db"))
    await mgr.init_db(schema_path="app/storage/schema.sql")
    await mgr.close()

    with caplog.at_level("WARNING"):
        await mgr.wal_checkpoint()

    assert any("WAL checkpoint failed" in r.message for r in caplog.records)
