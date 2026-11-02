"""Audit-fix lane L7 batch 2: A6-08, A6-09, A6-11, A6-12, A6-14, A6-15."""
# ruff: noqa: F811, F401  (imported fixtures are used by name)

import asyncio
import hashlib
import importlib
import importlib.util
import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from app.config import settings
from app.ocr import cache as ocr_cache
from app.ocr import manager as manager_mod
from app.ocr import queue as ocr_queue
from app.ocr.gate import GateConfig, classify_page
from app.ocr.manager import OcrManager
from app.ocr.settings import VLM_TIER
from app.ocr.types import OcrPage, PageVerdict
from tests.test_ocr_manager import (
    GOOD_STUB,
    pdf_path,
    seed,
    stub_env,
)


@pytest.fixture
async def mgr(mock_db, mock_emb, mock_lancedb):
    """Always stopped, so a reverted fix cannot leak a stub worker."""
    m = OcrManager(mock_db, mock_emb, mock_lancedb)
    m._indexing = AsyncMock()
    m._indexing.index_ocr_pages = AsyncMock(return_value=3)
    yield m
    await m.stop()


# ── A6-08: one bad record / one raising job must not strand the row ──────────

BAD_RECORD_STUB = GOOD_STUB.replace(
    '"mean_conf": 0.9, "ms": 5,',
    '"mean_conf": ("n/a" if p == 1 else 0.9), "ms": 5,',
)


async def test_schema_invalid_ndjson_record_costs_one_page(
    mgr, mock_db, stub_env, pdf_path, monkeypatch
):
    assert "n/a" in BAD_RECORD_STUB
    stub_env(BAD_RECORD_STUB)
    monkeypatch.setattr(settings, "ocr_conf_floor", 0.3)
    row = await seed(mock_db, pdf_path)

    await mgr._process_doc(row)

    _path, pages = mgr._indexing.index_ocr_pages.await_args.args
    assert sorted(p.page_num for p in pages) == [0, 2]


async def test_exception_in_a_job_does_not_strand_the_final_attempt(
    mgr, mock_db, pdf_path, monkeypatch
):
    row = await seed(mock_db, pdf_path)  # claimed: attempts == 1 == max below
    await ocr_queue.release_claim(mock_db, row.file_path)  # the drain loop claims it itself
    boom = AsyncMock(side_effect=OSError("disk went away"))
    mgr._process_doc = boom
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(settings, "ocr_max_attempts", 1)
    monkeypatch.setattr(manager_mod, "is_tier_installed", lambda: True)

    task = asyncio.create_task(mgr._drain_loop())
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            if boom.await_count:
                break
        await asyncio.sleep(0.2)
    finally:
        mgr._stopping = True
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    after = await ocr_queue.get_row(mock_db, row.file_path)
    assert after.status.value == "failed", f"row left {after.status.value}"
    assert "disk went away" in after.last_error


# ── A6-09: a tier switch must not keep serving from the old tier's worker ────


async def test_tier_switch_respawns_the_worker(mgr, mock_db, stub_env, pdf_path, monkeypatch):
    stub_env(GOOD_STUB)
    monkeypatch.setattr(settings, "ocr_tier", "cpu")
    row = await seed(mock_db, pdf_path)
    await mgr._process_doc(row)
    first_pid = mgr._proc.pid

    monkeypatch.setattr(settings, "ocr_tier", "gpu")
    await ocr_queue.requeue(mock_db, row.file_path)
    row2 = await ocr_queue.claim_next(mock_db, max_attempts=3)
    await mock_db.execute_write("DELETE FROM ocr_cache")  # force a real run
    await mgr._process_doc(row2)

    assert mgr._proc is not None and mgr._proc.pid != first_pid


async def test_pages_from_a_run_the_tier_changed_under_are_not_cached(
    mgr, mock_db, stub_env, pdf_path, monkeypatch
):
    stub_env(GOOD_STUB)
    monkeypatch.setattr(settings, "ocr_tier", "cpu")
    row = await seed(mock_db, pdf_path)
    real_run = mgr._run_document

    async def _run_then_switch(*a, **kw):
        result = await real_run(*a, **kw)
        monkeypatch.setattr(settings, "ocr_tier", "gpu")
        return result

    mgr._run_document = _run_then_switch

    await mgr._process_doc(row)

    mgr._indexing.index_ocr_pages.assert_awaited_once()
    key = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    assert await ocr_cache.get_pages(mock_db, key, [0, 1, 2], engine_id="stub") == {}


# ── A6-11: a text stamp over a scanned page must not hide the scan ───────────

FAX_HEADER = "FAX FROM +1 555 0100  TO +1 555 0199  PAGE 1 OF 1  2026-10-07 09:15  INVOICE 4471"


def _real_scan_page(text_chars: int, image_px: tuple[int, int]):
    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    img = DecodedStreamObject()
    img.set_data(b"\x00" * 16)
    img[NameObject("/Type")] = NameObject("/XObject")
    img[NameObject("/Subtype")] = NameObject("/Image")
    img[NameObject("/Width")] = NumberObject(image_px[0])
    img[NameObject("/Height")] = NumberObject(image_px[1])
    img[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
    img[NameObject("/BitsPerComponent")] = NumberObject(8)
    xobjects = DictionaryObject({NameObject("/Im0"): w._add_object(img)})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): xobjects})
    buf = io.BytesIO()
    w.write(buf)
    text = (FAX_HEADER * 20)[:text_chars]
    return PdfReader(io.BytesIO(buf.getvalue())).pages[0], text


def test_fax_header_over_a_full_page_scan_is_sent_to_ocr():
    page, text = _real_scan_page(103, (1700, 2200))
    assert len(text) == 103

    assert classify_page(page, text, GateConfig()).verdict == PageVerdict.OCR


def test_short_text_page_with_only_a_logo_stays_native():
    page, text = _real_scan_page(103, (120, 60))

    assert classify_page(page, text, GateConfig()).verdict == PageVerdict.NATIVE


def test_dense_text_page_with_a_background_scan_is_still_native_without_resources():
    page, text = _real_scan_page(2000, (1700, 2200))

    signal = classify_page(page, text, GateConfig())
    assert signal.verdict == PageVerdict.NATIVE
    assert signal.image_xobjects == -1  # short-circuit preserved for real text pages


# ── A6-12: the document budget grows with the page count ─────────────────────

SLOW_STUB = GOOD_STUB.replace(
    '            for p in msg["pages"]:\n',
    '            for p in msg["pages"]:\n                time.sleep(0.8)\n',
)


async def test_long_scan_is_not_killed_by_the_flat_doc_timeout(
    mgr, mock_db, stub_env, pdf_path, monkeypatch
):
    assert "time.sleep(0.8)" in SLOW_STUB
    stub_env(SLOW_STUB)
    monkeypatch.setattr(settings, "ocr_conf_floor", 0.3)
    monkeypatch.setattr(settings, "ocr_doc_timeout_s", 2)  # 5 pages x 0.8 s > 2 s
    monkeypatch.setattr(settings, "ocr_page_timeout_s", 3)  # budget: 5 x 3 = 15 s
    row = await seed(mock_db, pdf_path, pages=(0, 1, 2, 3, 4))

    await mgr._process_doc(row)

    after = await ocr_queue.get_row(mock_db, row.file_path)
    assert after.status.value == "done" and after.last_error == "", after.last_error
    _path, pages = mgr._indexing.index_ocr_pages.await_args.args
    assert len(pages) == 5


# ── A6-14: the reported provider is the session's, not the machine's ─────────


def test_engine_reports_cpu_when_directml_session_fell_back(monkeypatch):
    import onnxruntime

    class _Session:
        def get_providers(self):
            return ["CPUExecutionProvider"]

    class _FakeRapidOCR:
        def __init__(self, **kwargs):
            infer = type("Infer", (), {"session": _Session()})()
            self.text_det = type("Det", (), {"infer": infer})()

    monkeypatch.syspath_prepend(str(Path(manager_mod.__file__).parent / "worker"))
    monkeypatch.setitem(
        sys.modules,
        "rapidocr_onnxruntime",
        type(sys)("rapidocr_onnxruntime"),
    )
    sys.modules["rapidocr_onnxruntime"].RapidOCR = _FakeRapidOCR
    monkeypatch.setattr(
        onnxruntime,
        "get_available_providers",
        lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    )
    sys.modules.pop("pma_worker_engine", None)
    spec = importlib.util.spec_from_file_location(
        "pma_worker_engine", Path(manager_mod.__file__).parent / "worker" / "engine.py"
    )
    engine_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine_mod)

    assert engine_mod.Engine("", 0.3).execution_provider == "CPUExecutionProvider"


# ── A6-15: a page-capped VLM document says so ────────────────────────────────


class _FakePdf:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


async def test_vlm_page_cap_is_recorded_not_silent(mgr, mock_db, pdf_path, monkeypatch):
    from app.ocr.types import OcrLine

    monkeypatch.setattr(settings, "ocr_tier", VLM_TIER)
    monkeypatch.setattr(settings, "ocr_vlm_max_pages_per_doc", 2)
    monkeypatch.setattr("app.ocr.raster_png.open_pdf", lambda path: _FakePdf())

    async def _page(path, page_num, doc=None):
        return OcrPage(page_num=page_num, lines=(OcrLine("text", 0.9),), mean_conf=0.9)

    monkeypatch.setattr("app.ocr.vlm_engine.recognize_page", _page)
    row = await seed(mock_db, pdf_path, pages=(0, 1, 2, 3, 4))

    await mgr._process_doc(row)

    _path, pages = mgr._indexing.index_ocr_pages.await_args.args
    assert sorted(p.page_num for p in pages) == [0, 1]
    after = await ocr_queue.get_row(mock_db, row.file_path)
    assert after.status.value == "done"
    assert "ocr_vlm_max_pages_per_doc" in after.last_error and "2-4" in after.last_error
