"""Audit-fix lane L7 batch 1: A6-03..A6-07 (OCR gate, queue, VLM tier, hung page)."""
# ruff: noqa: F811, F401  (imported fixtures are used by name)

import asyncio
import hashlib
import io
import time
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from app.config import settings
from app.ocr import cache as ocr_cache
from app.ocr import manager as manager_mod
from app.ocr import queue as ocr_queue
from app.ocr.gate import GateConfig, classify_page
from app.ocr.manager import OcrManager
from app.ocr.settings import VLM_TIER
from app.ocr.types import PageVerdict
from tests.test_ocr_manager import (
    GOOD_STUB,
    pdf_path,
    seed,
    stub_env,
)


@pytest.fixture
async def mgr(mock_db, mock_emb, mock_lancedb):
    """As test_ocr_manager's, but always stopped: a reverted fix can spawn a
    stub worker the test never stops, and it would hold pytest's pipes open."""
    m = OcrManager(mock_db, mock_emb, mock_lancedb)
    m._indexing = AsyncMock()
    m._indexing.index_ocr_pages = AsyncMock(return_value=3)
    yield m
    await m.stop()


# ── A6-03: gate step 5 needs the stream length from real pypdf objects ───────


def _real_page(content: bytes, *, flate: bool):
    w = PdfWriter()
    page = w.add_blank_page(200, 200)
    stream = DecodedStreamObject()
    stream.set_data(content)
    if flate:
        stream = stream.flate_encode()
    page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO()
    w.write(buf)
    return PdfReader(io.BytesIO(buf.getvalue())).pages[0]


@pytest.mark.parametrize("flate", [False, True])
def test_vector_outline_page_is_sent_to_ocr(flate):
    """Text drawn as curves: no images, no text, a big content stream."""
    # Varied numbers: a repeated line deflates to ~50 bytes, under the threshold.
    ops = b"".join(
        b"%d %d m %d %d l S\n" % (i, i * 7 % 97, i * 3 % 89, i * 11 % 83) for i in range(2000)
    )
    page = _real_page(ops, flate=flate)

    signal = classify_page(page, "", GateConfig(blank_stream_bytes=512))

    assert signal.verdict == PageVerdict.OCR
    assert signal.stream_bytes > 512


def test_genuinely_empty_page_is_still_blank():
    page = _real_page(b"q Q", flate=False)

    assert classify_page(page, "", GateConfig(blank_stream_bytes=512)).verdict == PageVerdict.BLANK


# ── A6-04: a re-enqueue during a running job must survive its verdict ────────


async def test_reenqueue_during_a_running_job_is_not_clobbered(
    mgr,
    mock_db,
    stub_env,
    pdf_path,
    monkeypatch,
):
    stub_env(GOOD_STUB)
    row = await seed(mock_db, pdf_path, pages=(0, 1))

    async def _reenqueue_mid_job(_path, _pages):
        await ocr_queue.enqueue_document(mock_db, row.file_path, [0, 1, 2, 3, 4], 5)
        return 2

    mgr._indexing.index_ocr_pages = AsyncMock(side_effect=_reenqueue_mid_job)

    await mgr._process_doc(row)
    await mgr.stop()

    after = await ocr_queue.get_row(mock_db, row.file_path)
    assert after.status.value == "pending"
    assert after.pages == (0, 1, 2, 3, 4)
    assert after.attempts == 0
    claimed = await ocr_queue.claim_next(mock_db, max_attempts=3)
    assert claimed is not None and claimed.pages == (0, 1, 2, 3, 4)


# ── A6-05: an unreachable VLM provider must not burn the queue ───────────────


class _FakePdf:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


def _vlm_env(monkeypatch, base_url, consent):
    from app.ocr import settings as ocr_settings

    monkeypatch.setattr(settings, "ocr_tier", VLM_TIER)
    monkeypatch.setattr(
        ocr_settings, "vlm_selection", lambda: {"provider": "ollama", "model": "glm-ocr"}
    )
    monkeypatch.setattr("app.providers.env_base_url", lambda pid: base_url)
    monkeypatch.setattr(
        "app.settings_store.SettingsStore.read",
        lambda: {"llm": {"cloud_privacy_consent": consent}},
    )
    monkeypatch.setattr("app.ocr.raster_png.open_pdf", lambda path: _FakePdf())
    monkeypatch.setattr("app.ocr.vlm_engine.render_page_from", lambda *a: b"\x89PNG")
    monkeypatch.setattr(manager_mod, "is_tier_installed", lambda: True)


# 127.0.0.1:9 is a real closed port (ConnectError); the off-box host is refused
# for want of consent before any request is built.
@pytest.mark.parametrize(
    ("base_url", "consent"),
    [("http://127.0.0.1:9", False), ("http://ocr.example.com:11434", False)],
)
async def test_unavailable_vlm_provider_holds_the_queue(
    mgr,
    mock_db,
    pdf_path,
    monkeypatch,
    base_url,
    consent,
):
    _vlm_env(monkeypatch, base_url, consent)
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(manager_mod, "_VLM_RETRY_S", 30.0)
    paths = []
    for i in range(3):
        p = pdf_path.with_name(f"scan{i}.pdf")
        p.write_bytes(b"%PDF-1.4 " + bytes([i]))
        paths.append(p)
        await seed(mock_db, p, pages=(0,))
    # seed() stamps one sha on every files row; give each file its own.
    for p in paths:
        await mock_db.execute_write(
            "UPDATE files SET sha256 = ? WHERE path = ?",
            (hashlib.sha256(p.read_bytes()).hexdigest(), str(p.absolute())),
        )
    await mock_db.execute_write("UPDATE ocr_queue SET status = 'pending', attempts = 0")

    task = asyncio.create_task(mgr._drain_loop())
    for _ in range(200):  # a refused connect can take ~2 s on Windows
        await asyncio.sleep(0.1)
        if mgr._retry_after:
            break
    await asyncio.sleep(0.5)  # a burning loop would have claimed the rest by now
    mgr._stopping = True
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    rows = await ocr_queue.list_queue(mock_db)
    assert [(r.status.value, r.last_error) for r in rows] == [("pending", "")] * 3, (
        "queue was burned"
    )
    assert [r.attempts for r in rows] == [0, 0, 0], "attempts were spent on an outage"
    assert mgr._retry_after > time.monotonic()
    assert "unavailable" in mgr._last_error


# ── A6-06: never cache text under a hash the file no longer has ──────────────


async def test_file_edited_after_indexing_is_not_ocrd_under_the_old_hash(
    mgr,
    mock_db,
    stub_env,
    pdf_path,
):
    stub_env(GOOD_STUB)
    row = await seed(mock_db, pdf_path)
    old_key = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    pdf_path.write_bytes(b"%PDF-1.4 version two, saved before the queue drained")

    await mgr._process_doc(row)

    mgr._indexing.index_ocr_pages.assert_not_awaited()
    assert await ocr_cache.get_pages(mock_db, old_key, [0, 1, 2]) == {}
    assert (await ocr_queue.get_row(mock_db, row.file_path)).status.value == "skipped"


async def test_file_edited_during_ocr_is_not_cached(mgr, mock_db, stub_env, pdf_path):
    edit = (
        '    elif msg["t"] == protocol.REQ_DOC:\n        open(msg["path"], "ab").write(b"edited")\n'
    )
    stub_env(GOOD_STUB.replace('    elif msg["t"] == protocol.REQ_DOC:\n', edit))
    row = await seed(mock_db, pdf_path)
    old_key = hashlib.sha256(pdf_path.read_bytes()).hexdigest()

    await mgr._process_doc(row)
    await mgr.stop()

    mgr._indexing.index_ocr_pages.assert_not_awaited()
    assert await ocr_cache.get_pages(mock_db, old_key, [0, 1, 2]) == {}
    assert (await ocr_queue.get_row(mock_db, row.file_path)).status.value == "skipped"


# ── A6-07: a page the worker hangs on must not block the pages behind it ─────

HANG_ON_PAGE_1_STUB = GOOD_STUB.replace(
    '            for p in msg["pages"]:\n',
    '            for p in msg["pages"]:\n                if p == 1:\n                    time.sleep(600)\n',
)


async def test_hung_page_goes_last_on_retry(mgr, mock_db, stub_env, pdf_path, monkeypatch):
    assert "time.sleep(600)" in HANG_ON_PAGE_1_STUB
    stub_env(HANG_ON_PAGE_1_STUB)
    monkeypatch.setattr(settings, "ocr_conf_floor", 0.3)
    monkeypatch.setattr(settings, "ocr_page_timeout_s", -13)  # quiet limit: 2 s
    row = await seed(mock_db, pdf_path, pages=(0, 1, 2, 3, 4))  # seed() claims it

    try:
        while row is not None:
            await mgr._process_doc(row)
            row = await ocr_queue.claim_next(mock_db, max_attempts=settings.ocr_max_attempts)
    finally:
        await mgr.stop()

    _path, pages = mgr._indexing.index_ocr_pages.await_args.args
    assert sorted(p.page_num for p in pages) == [0, 2, 3, 4]


# ── checkpoint review: skip verdicts honour a re-enqueue too ─────────────────


async def test_reenqueue_during_a_job_that_ends_skipped_is_not_clobbered(
    mgr, mock_db, stub_env, pdf_path
):
    edit = (
        '    elif msg["t"] == protocol.REQ_DOC:\n        open(msg["path"], "ab").write(b"edited")\n'
    )
    stub_env(GOOD_STUB.replace('    elif msg["t"] == protocol.REQ_DOC:\n', edit))
    row = await seed(mock_db, pdf_path)
    real_run = mgr._run_document

    async def _run_then_reenqueue(*a, **kw):
        result = await real_run(*a, **kw)
        # the watcher re-indexed the edited file while the old job was running
        await ocr_queue.enqueue_document(mock_db, row.file_path, [0, 1, 2, 3], 4)
        return result

    mgr._run_document = _run_then_reenqueue

    await mgr._process_doc(row)

    after = await ocr_queue.get_row(mock_db, row.file_path)
    assert after.status.value == "pending"
    assert after.pages == (0, 1, 2, 3)


# ── a base URL saved on the Providers page is the one the VLM tier uses ──────


async def test_vlm_uses_the_saved_base_url_and_gates_it(monkeypatch):
    from app.ocr import settings as ocr_settings
    from app.ocr.vlm_engine import VlmNotConfiguredError, recognize_page

    created = []
    monkeypatch.setattr(
        ocr_settings, "vlm_selection", lambda: {"provider": "ollama", "model": "glm-ocr"}
    )
    monkeypatch.setattr("app.providers.env_base_url", lambda pid: "http://127.0.0.1:11434")
    monkeypatch.setattr(
        "app.providers.create_provider", lambda *a, **kw: created.append(kw) or None
    )
    monkeypatch.setattr(
        "app.settings_store.SettingsStore.read",
        lambda: {
            "llm": {
                "cloud_privacy_consent": False,
                "per_provider": {"ollama": {"base_url": "http://ocr.example.com:11434"}},
            }
        },
    )

    with pytest.raises(VlmNotConfiguredError, match="consent"):
        await recognize_page("doc.pdf", 0)
    assert created == []
