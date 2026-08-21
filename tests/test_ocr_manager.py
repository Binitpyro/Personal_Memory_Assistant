"""OcrManager supervision tests.

Uses a stub worker written by the test and run under `sys.executable` - never
a real venv, and never a real OCR engine. The stub speaks the actual protocol,
so these exercise the real handshake, the real NDJSON reader and the real
failure paths.
"""

import contextlib
import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.ocr import cache as ocr_cache
from app.ocr import manager as manager_mod
from app.ocr import protocol as proto
from app.ocr import queue as ocr_queue
from app.ocr.manager import OcrManager
from app.ocr.settings import GPU_TIER, VLM_TIER
from app.ocr.types import OcrLine, OcrPage

STUB_HEADER = """
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol

def emit(msg):
    sys.stdout.write(protocol.encode(msg))
    sys.stdout.flush()
"""


def write_stub(tmp_path: Path, body: str) -> Path:
    """Materialize a stub worker beside a real copy of protocol.py."""
    worker_dir = tmp_path / "worker"
    worker_dir.mkdir(parents=True, exist_ok=True)

    real_protocol = Path(__file__).parent.parent / "app" / "ocr" / "protocol.py"
    (worker_dir / "protocol.py").write_text(
        real_protocol.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (worker_dir / "__main__.py").write_text(STUB_HEADER + textwrap.dedent(body), encoding="utf-8")
    return worker_dir


@pytest.fixture
def stub_env(tmp_path, monkeypatch):
    """Point the manager at sys.executable + a stub worker directory."""

    def _install(body: str):
        worker_dir = write_stub(tmp_path, body)
        monkeypatch.setattr(manager_mod, "ocr_python", lambda: Path(sys.executable))
        monkeypatch.setattr(manager_mod, "ocr_worker_dir", lambda: worker_dir)
        monkeypatch.setattr(manager_mod, "ocr_models_dir", lambda: tmp_path / "models")
        monkeypatch.setattr(manager_mod, "ocr_scratch_dir", lambda: tmp_path / "scratch")
        monkeypatch.setattr(
            manager_mod,
            "ensure_dirs",
            lambda: (tmp_path / "scratch").mkdir(parents=True, exist_ok=True),
        )
        monkeypatch.setattr(manager_mod, "is_tier_installed", lambda: True)
        return worker_dir

    return _install


@pytest.fixture
def mgr(mock_db, mock_emb, mock_lancedb):
    m = OcrManager(mock_db, mock_emb, mock_lancedb)
    m._indexing = AsyncMock()
    m._indexing.index_ocr_pages = AsyncMock(return_value=3)
    return m


@pytest.fixture
def pdf_path(tmp_path):
    """A real file on disk - the manager skips documents that have vanished."""
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"%PDF-1.4 not a real pdf, the stub never opens it")
    return p


async def seed(db, pdf_path, pages=(0, 1, 2), sha="c" * 64):
    path_str = str(pdf_path.absolute())
    await db.batch_insert_files(
        [
            {
                "path": path_str,
                "size": 10,
                "modified_at": "2026-01-01T00:00:00",
                "type": ".pdf",
                "folder_tag": "docs",
                "sha256": sha,
            }
        ]
    )
    await db.execute_write("UPDATE files SET sha256 = ?", (sha,))
    await ocr_queue.enqueue_document(db, path_str, list(pages), len(pages))
    return await ocr_queue.claim_next(db, max_attempts=3)


# ── happy path ───────────────────────────────────────────────────────────

GOOD_STUB = """
for line in sys.stdin:
    try:
        msg = protocol.decode(line)
    except protocol.ProtocolError:
        continue
    if msg["t"] == protocol.REQ_HELLO:
        emit(protocol.make_ready(model_version="stub", ep="CPUExecutionProvider"))
    elif msg["t"] == protocol.REQ_DOC:
        with open(msg["ndjson"], "a", encoding="utf-8") as f:
            for p in msg["pages"]:
                f.write(json.dumps({
                    "page": p,
                    "lines": [{"text": "page %d text" % p, "conf": 0.9, "low": False}],
                    "mean_conf": 0.9, "ms": 5,
                }) + "\\n")
                f.flush()
                emit(protocol.make_page(doc_id=msg["doc_id"], page=p, ok=True, ms=5))
        emit(protocol.make_doc_done(
            doc_id=msg["doc_id"], pages_ok=len(msg["pages"]), pages_failed=0, mean_conf=0.9))
    elif msg["t"] == protocol.REQ_SHUTDOWN:
        break
"""


async def test_happy_path_indexes_every_page(mgr, mock_db, stub_env, pdf_path, monkeypatch):
    stub_env(GOOD_STUB)
    monkeypatch.setattr(settings, "ocr_conf_floor", 0.3)
    row = await seed(mock_db, pdf_path)

    await mgr._process_doc(row)

    mgr._indexing.index_ocr_pages.assert_awaited_once()
    _path, pages = mgr._indexing.index_ocr_pages.await_args.args
    assert sorted(p.page_num for p in pages) == [0, 1, 2]
    assert (await ocr_queue.get_row(mock_db, row.file_path)).status.value == "done"

    await mgr.stop()


async def test_results_are_cached_and_reused(mgr, mock_db, stub_env, pdf_path):
    """The second pass must not spawn a worker at all."""
    stub_env(GOOD_STUB)
    row = await seed(mock_db, pdf_path)
    await mgr._process_doc(row)
    await mgr.stop()

    await ocr_queue.requeue(mock_db, row.file_path)
    row2 = await ocr_queue.claim_next(mock_db, max_attempts=3)
    mgr._indexing.index_ocr_pages.reset_mock()

    await mgr._process_doc(row2)

    assert mgr._proc is None, "cache hit should not have started a worker"
    _path, pages = mgr._indexing.index_ocr_pages.await_args.args
    assert len(pages) == 3


# ── failure paths ────────────────────────────────────────────────────────

CRASH_MIDWAY_STUB = """
for line in sys.stdin:
    try:
        msg = protocol.decode(line)
    except protocol.ProtocolError:
        continue
    if msg["t"] == protocol.REQ_HELLO:
        emit(protocol.make_ready(model_version="stub", ep="CPU"))
    elif msg["t"] == protocol.REQ_DOC:
        with open(msg["ndjson"], "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "page": msg["pages"][0],
                "lines": [{"text": "first page survived", "conf": 0.9, "low": False}],
                "mean_conf": 0.9, "ms": 5,
            }) + "\\n")
            f.flush()
        emit(protocol.make_page(doc_id=msg["doc_id"], page=msg["pages"][0], ok=True, ms=5))
        os._exit(1)
"""


async def test_partial_results_survive_a_crash(mgr, mock_db, stub_env, pdf_path):
    """Whatever reached the NDJSON is still indexed. This is the core promise."""
    stub_env(CRASH_MIDWAY_STUB)
    row = await seed(mock_db, pdf_path)

    await mgr._process_doc(row)

    mgr._indexing.index_ocr_pages.assert_awaited_once()
    _path, pages = mgr._indexing.index_ocr_pages.await_args.args
    assert [p.page_num for p in pages] == [0]
    assert pages[0].indexable_text == "first page survived"


SILENT_STUB = """
for line in sys.stdin:
    try:
        msg = protocol.decode(line)
    except protocol.ProtocolError:
        continue
    if msg["t"] == protocol.REQ_HELLO:
        emit(protocol.make_ready(model_version="stub", ep="CPU"))
    elif msg["t"] == protocol.REQ_DOC:
        time.sleep(600)
"""


async def test_silent_worker_hits_the_doc_timeout_and_is_killed(
    mgr, mock_db, stub_env, pdf_path, monkeypatch
):
    stub_env(SILENT_STUB)
    monkeypatch.setattr(settings, "ocr_doc_timeout_s", 2)
    monkeypatch.setattr(settings, "ocr_page_timeout_s", 1)
    row = await seed(mock_db, pdf_path)

    await mgr._process_doc(row)

    assert mgr._proc is None, "hung worker was not killed"
    stored = await ocr_queue.get_row(mock_db, row.file_path)
    assert stored.status.value in ("pending", "failed")
    assert stored.last_error in ("OCR_DOC_TIMEOUT", "OCR_PAGE_TIMEOUT")


FATAL_STUB = """
for line in sys.stdin:
    try:
        msg = protocol.decode(line)
    except protocol.ProtocolError:
        continue
    if msg["t"] == protocol.REQ_HELLO:
        emit(protocol.make_error(code=protocol.E_PROTOCOL_MISMATCH, detail="version skew"))
        sys.exit(3)
"""


async def test_protocol_mismatch_marks_the_tier_unhealthy(mgr, mock_db, stub_env, pdf_path):
    stub_env(FATAL_STUB)
    row = await seed(mock_db, pdf_path)

    await mgr._process_doc(row)

    assert mgr._fatal == "PROTOCOL_MISMATCH"
    status = await mgr.status()
    assert status["unhealthy"] is True


NOISY_STDERR_STUB = """
for line in sys.stdin:
    try:
        msg = protocol.decode(line)
    except protocol.ProtocolError:
        continue
    if msg["t"] == protocol.REQ_HELLO:
        # 1 MB of stderr. Without a drain thread the OS pipe buffer fills at
        # ~64 KB and this write blocks forever, hanging the whole test.
        for _ in range(1000):
            sys.stderr.write("x" * 1024 + "\\n")
        sys.stderr.flush()
        emit(protocol.make_ready(model_version="stub", ep="CPU"))
    elif msg["t"] == protocol.REQ_DOC:
        emit(protocol.make_doc_done(
            doc_id=msg["doc_id"], pages_ok=0, pages_failed=0, mean_conf=0.0))
"""


async def test_noisy_stderr_does_not_deadlock(mgr, mock_db, stub_env, pdf_path):
    """Regression guard: this hangs forever without the stderr drain thread."""
    stub_env(NOISY_STDERR_STUB)
    row = await seed(mock_db, pdf_path)

    await mgr._process_doc(row)

    assert len(mgr._stderr_tail) > 0
    await mgr.stop()


DOC_OPEN_FAILED_STUB = """
for line in sys.stdin:
    try:
        msg = protocol.decode(line)
    except protocol.ProtocolError:
        continue
    if msg["t"] == protocol.REQ_HELLO:
        emit(protocol.make_ready(model_version="stub", ep="CPU"))
    elif msg["t"] == protocol.REQ_DOC:
        # What worker/__main__.py does when open_document() raises: a whole
        # *document* failure reported with a page-level code, then doc_done.
        emit(protocol.make_error(
            code=protocol.E_RASTER_FAILED, detail="cannot open PDF", doc_id=msg["doc_id"]))
        emit(protocol.make_doc_done(
            doc_id=msg["doc_id"], pages_ok=0, pages_failed=1, mean_conf=0.0))
"""


async def test_document_that_produced_nothing_is_not_marked_done(mgr, mock_db, stub_env, pdf_path):
    """Zero pages back when pages were asked for is a failure, not a success.

    E_RASTER_FAILED is page-level (protocol.py), so _run_document only debug-logs
    it and returns "". With error_code empty and all_pages empty, the old guard
    `if error_code and not all_pages` was False and the row was marked *done* -
    retiring the document permanently, because enqueue_document re-arms only on
    a content change.
    """
    stub_env(DOC_OPEN_FAILED_STUB)
    row = await seed(mock_db, pdf_path)

    await mgr._process_doc(row)

    stored = await ocr_queue.get_row(mock_db, row.file_path)
    assert stored.status.value != "done"
    assert stored.last_error
    mgr._indexing.index_ocr_pages.assert_not_awaited()
    await mgr.stop()


# ── guards that need no subprocess ───────────────────────────────────────


@pytest.mark.parametrize("sentinel", ["", "ERROR", "CANCELLED"])
async def test_sentinel_hash_skips_the_document(mgr, mock_db, pdf_path, sentinel):
    """These identify no content, so nothing may be cached under them."""
    row = await seed(mock_db, pdf_path, sha=sentinel)

    await mgr._process_doc(row)

    assert (await ocr_queue.get_row(mock_db, row.file_path)).status.value == "skipped"
    mgr._indexing.index_ocr_pages.assert_not_awaited()


async def test_indexing_failure_is_terminal_not_deferred(mgr, mock_db, stub_env, pdf_path):
    """Replaces test_indexer_going_busy_mid_write_refunds_the_claim.

    That test asserted a RuntimeError raised while an index run is active must
    refund the claim, on the premise that index_ocr_pages "raises outright" in
    that situation. It never did - the comment cited a service.py line that does
    not exist - and since 2d3f684 the write happens inside a SAVEPOINT, so a
    concurrent index run is safe. The deferral guarded a hazard that is gone,
    and its release_claim() refund meant a persistent RuntimeError retried
    forever with no budget.

    An indexing failure is now just a failure, whatever the indexer is doing.
    """
    from app.indexing.service import progress

    stub_env(GOOD_STUB)
    row = await seed(mock_db, pdf_path)
    mgr._indexing.index_ocr_pages = AsyncMock(side_effect=RuntimeError("insert blew up"))

    original = progress.status
    progress.status = "running"
    try:
        await mgr._process_doc(row)
    finally:
        progress.status = original

    stored = await ocr_queue.get_row(mock_db, row.file_path)
    assert stored.status.value == "failed", "must not be parked back on pending"
    assert "insert blew up" in stored.last_error
    assert stored.attempts == 1, "the attempt is spent, not refunded"
    await mgr.stop()


async def test_deleted_file_is_skipped(mgr, mock_db, pdf_path):
    row = await seed(mock_db, pdf_path)
    await mock_db.execute_write("DELETE FROM files")

    await mgr._process_doc(row)

    assert (await ocr_queue.get_row(mock_db, row.file_path)).status.value == "skipped"


async def test_drain_loop_runs_concurrently_with_indexing(mgr, mock_db, monkeypatch):
    """OCR drain loop processes queue items concurrently with active indexing."""
    from app.indexing.service import progress

    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(manager_mod, "is_tier_installed", lambda: True)
    claim = AsyncMock(return_value=None)
    monkeypatch.setattr(manager_mod.ocr_queue, "claim_next", claim)

    original = progress.status
    progress.status = "running"
    try:
        import asyncio

        task = asyncio.create_task(mgr._drain_loop())
        await asyncio.sleep(0.15)
        mgr._stopping = True
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        progress.status = original

    claim.assert_awaited()


async def test_ndjson_truncated_tail_is_tolerated(mgr, tmp_path):
    """A kill can cut the final line mid-write; that must cost one page."""
    ndjson = tmp_path / "partial.ndjson"
    good = json.dumps(
        {"page": 0, "lines": [{"text": "ok", "conf": 0.9, "low": False}], "mean_conf": 0.9}
    )
    ndjson.write_text(good + '\n{"page": 1, "lines": [{"te', encoding="utf-8")

    pages = await mgr._read_ndjson(ndjson)

    assert [p.page_num for p in pages] == [0]


async def test_missing_ndjson_returns_nothing(mgr, tmp_path):
    assert await mgr._read_ndjson(tmp_path / "nope.ndjson") == []


async def test_conf_floor_is_reapplied_when_reading_worker_output(mgr, tmp_path, monkeypatch):
    """A stale worker must not be able to smuggle low-confidence text in."""
    monkeypatch.setattr(settings, "ocr_conf_floor", 0.8)
    ndjson = tmp_path / "x.ndjson"
    ndjson.write_text(
        json.dumps(
            {
                "page": 0,
                # Worker claims low=False, but 0.5 is under our floor.
                "lines": [{"text": "shaky", "conf": 0.5, "low": False}],
                "mean_conf": 0.5,
            }
        ),
        encoding="utf-8",
    )

    pages = await mgr._read_ndjson(ndjson)

    assert pages[0].lines[0].low is True
    assert pages[0].indexable_text == ""


ALL_PAGES_FAILED_STUB = """
for line in sys.stdin:
    try:
        msg = protocol.decode(line)
    except protocol.ProtocolError:
        continue
    if msg["t"] == protocol.REQ_HELLO:
        emit(protocol.make_ready(model_version="stub", ep="CPUExecutionProvider"))
    elif msg["t"] == protocol.REQ_DOC:
        with open(msg["ndjson"], "a", encoding="utf-8") as f:
            for p in msg["pages"]:
                f.write(json.dumps({
                    "page": p, "lines": [], "mean_conf": 0.0, "ms": 3,
                    "error": "E_RASTER_FAILED",
                }) + "\\n")
                f.flush()
                emit(protocol.make_page(doc_id=msg["doc_id"], page=p, ok=False, ms=3))
        emit(protocol.make_doc_done(
            doc_id=msg["doc_id"], pages_ok=0, pages_failed=len(msg["pages"]), mean_conf=0.0))
    elif msg["t"] == protocol.REQ_SHUTDOWN:
        break
"""


async def test_document_whose_every_page_failed_is_not_marked_done(
    mgr, mock_db, stub_env, pdf_path
):
    """Per-page errors with a clean doc_done must not read as success.

    Distinct from test_document_that_produced_nothing_is_not_marked_done, which
    covers the *zero-page* branch. Here the worker writes a record per page,
    each carrying an error and no lines, then reports doc_done normally.
    _read_ndjson keeps those records (it filters only page_num >= 0) and
    _run_document returns "" for any doc_done, so error_code was empty and
    len(all_pages) matched the request: the document fell through to mark_done,
    which also wiped last_error.
    """
    stub_env(ALL_PAGES_FAILED_STUB)
    row = await seed(mock_db, pdf_path)

    await mgr._process_doc(row)

    # Proves we are in the new branch and not the older zero-page one: the
    # stub really did write a record per page, so all_pages is non-empty and
    # index_ocr_pages was reached.
    mgr._indexing.index_ocr_pages.assert_awaited_once()
    _p, pages = mgr._indexing.index_ocr_pages.await_args.args
    assert len(pages) == 3
    assert all(pg.error for pg in pages)

    stored = await ocr_queue.get_row(mock_db, row.file_path)
    assert stored.status.value != "done"
    assert "page(s) failed" in stored.last_error
    await mgr.stop()


async def test_blank_but_successful_scan_records_why(mgr, mock_db, stub_env, pdf_path):
    """A scan that ran cleanly and found nothing is done, but says so.

    Without this the queue shows an ordinary success and the user has no way to
    tell an empty scan from an indexed one.
    """
    stub_env(BLANK_OK_STUB)
    row = await seed(mock_db, pdf_path)

    await mgr._process_doc(row)

    stored = await ocr_queue.get_row(mock_db, row.file_path)
    assert stored.status.value == "done"
    assert "no readable text" in stored.last_error
    await mgr.stop()


BLANK_OK_STUB = """
for line in sys.stdin:
    try:
        msg = protocol.decode(line)
    except protocol.ProtocolError:
        continue
    if msg["t"] == protocol.REQ_HELLO:
        emit(protocol.make_ready(model_version="stub", ep="CPUExecutionProvider"))
    elif msg["t"] == protocol.REQ_DOC:
        with open(msg["ndjson"], "a", encoding="utf-8") as f:
            for p in msg["pages"]:
                f.write(json.dumps({
                    "page": p, "lines": [], "mean_conf": 0.0, "ms": 3,
                }) + "\\n")
                f.flush()
                emit(protocol.make_page(doc_id=msg["doc_id"], page=p, ok=True, ms=3))
        emit(protocol.make_doc_done(
            doc_id=msg["doc_id"], pages_ok=len(msg["pages"]), pages_failed=0, mean_conf=0.0))
    elif msg["t"] == protocol.REQ_SHUTDOWN:
        break
"""


async def test_force_ocr_ignores_the_cache(mgr, mock_db, stub_env, pdf_path, monkeypatch):
    """The documented escape hatch was inert: the flag was written, never read.

    _process_doc consulted ocr_cache unconditionally, so Force OCR on a file
    with cached pages handed back exactly the text the user was overriding -
    which is the whole point of the gate's blind spot (gate.py:9-11).
    """
    monkeypatch.setattr(settings, "ocr_conf_floor", 0.3)
    stub_env(GOOD_STUB)
    row = await seed(mock_db, pdf_path, pages=(0,))

    # Seed the cache with text the forced run must NOT return.
    await ocr_cache.put_pages(
        mock_db,
        "c" * 64,
        [OcrPage(page_num=0, lines=(OcrLine("stale cached text", 0.9, False),), mean_conf=0.9)],
        engine_id=mgr._active_engine_id(),
    )

    # Same path POST /ocr/force takes (api.py:412): re-enqueue with force=True.
    await ocr_queue.enqueue_document(mock_db, row.file_path, [0], 1, force=True)
    forced = await ocr_queue.get_row(mock_db, row.file_path)
    assert forced.force_ocr is True

    await mgr._process_doc(forced)

    _p, pages = mgr._indexing.index_ocr_pages.await_args.args
    assert pages[0].indexable_text == "page 0 text", "must re-OCR, not serve the cache"
    await mgr.stop()


def test_stale_claim_window_tracks_the_active_tier(mgr, monkeypatch):
    """A fixed threshold would steal a VLM document mid-run.

    ocr_vlm_doc_timeout_s is 7200s against 600s for the worker tiers, so the
    reclaim window has to follow whichever tier is active.
    """
    monkeypatch.setattr(settings, "ocr_tier", "cpu")
    monkeypatch.setattr(settings, "ocr_doc_timeout_s", 600)
    assert mgr._stale_claim_seconds() == 1200

    monkeypatch.setattr(settings, "ocr_tier", VLM_TIER)
    monkeypatch.setattr(settings, "ocr_vlm_doc_timeout_s", 7200)
    assert mgr._stale_claim_seconds() == 14400
    assert mgr._stale_claim_seconds() > settings.ocr_vlm_doc_timeout_s


def test_crash_reason_carries_the_worker_stderr(mgr):
    """_spawn_sync clears the tail, so the traceback had to be captured here."""
    mgr._stderr_tail.extend(["Traceback (most recent call last):", "  ...", "RuntimeError: boom"])

    enriched = mgr._with_stderr_tail(proto.E_WORKER_CRASHED)
    assert enriched.startswith(proto.E_WORKER_CRASHED)
    assert "RuntimeError: boom" in enriched

    # Non-crash codes are passed through untouched.
    assert mgr._with_stderr_tail("OCR_PAGE_TIMEOUT") == "OCR_PAGE_TIMEOUT"
    mgr._stderr_tail.clear()
    assert mgr._with_stderr_tail(proto.E_WORKER_CRASHED) == proto.E_WORKER_CRASHED


class _FakeProc:
    """Stands in for a live worker process in the idle-linger tests."""

    def poll(self):
        return None


async def test_idle_worker_lingers_before_retiring(mgr, monkeypatch):
    """ocr_worker_idle_timeout_s existed in config and was read nowhere.

    The worker was killed the instant the queue drained, so under the normal
    one-file-at-a-time watcher pattern every document paid a fresh venv start
    plus an ONNX model load.
    """
    monkeypatch.setattr(settings, "ocr_tier", "cpu")
    monkeypatch.setattr(settings, "ocr_worker_idle_timeout_s", 60)
    mgr._proc = _FakeProc()
    retired = []
    monkeypatch.setattr(mgr, "_retire_worker", AsyncMock(side_effect=lambda r: retired.append(r)))

    # First drain starts the clock and keeps the worker.
    wait = await mgr._retire_if_idle_long_enough()
    assert retired == []
    assert mgr._idle_since is not None
    assert 0 < wait <= 60, "should wake when the linger expires, not a poll later"

    # Still inside the window.
    assert await mgr._retire_if_idle_long_enough() is not None
    assert retired == []

    # Past it.
    mgr._idle_since -= 61
    await mgr._retire_if_idle_long_enough()
    assert retired == ["idle"]
    assert mgr._idle_since is None


async def test_gpu_tier_never_lingers(mgr, monkeypatch):
    """A resident DirectML session holds VRAM against a budget already over."""
    monkeypatch.setattr(settings, "ocr_tier", GPU_TIER)
    monkeypatch.setattr(settings, "ocr_worker_idle_timeout_s", 600)
    mgr._proc = _FakeProc()
    retired = []
    monkeypatch.setattr(mgr, "_retire_worker", AsyncMock(side_effect=lambda r: retired.append(r)))

    await mgr._retire_if_idle_long_enough()

    assert retired == ["idle"], "GPU tier must retire immediately"
    assert mgr._idle_since is None


async def test_no_worker_means_nothing_to_linger_on(mgr, monkeypatch):
    monkeypatch.setattr(settings, "ocr_tier", "cpu")
    mgr._proc = None
    retired = []
    monkeypatch.setattr(mgr, "_retire_worker", AsyncMock(side_effect=lambda r: retired.append(r)))

    assert await mgr._retire_if_idle_long_enough() > 0
    assert retired == []
    assert mgr._idle_since is None
