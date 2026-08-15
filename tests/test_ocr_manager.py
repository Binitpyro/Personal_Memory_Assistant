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
from app.ocr import manager as manager_mod
from app.ocr import queue as ocr_queue
from app.ocr.manager import OcrManager

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


async def test_indexer_going_busy_mid_write_refunds_the_claim(mgr, mock_db, stub_env, pdf_path):
    """Losing the idle race is someone else's timing, not a bad document.

    index_ocr_pages raises if an index run starts after the drain loop's idle
    check. Treating that as a terminal indexing failure burned the document for
    good; it must go back to pending with its attempt refunded.
    """
    from app.indexing.service import progress

    stub_env(GOOD_STUB)
    row = await seed(mock_db, pdf_path)
    mgr._indexing.index_ocr_pages = AsyncMock(
        side_effect=RuntimeError("index_ocr_pages() called while indexing is active")
    )

    original = progress.status
    progress.status = "running"
    try:
        await mgr._process_doc(row)
    finally:
        progress.status = original

    stored = await ocr_queue.get_row(mock_db, row.file_path)
    assert stored.status.value == "pending"
    assert stored.attempts == 0
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
