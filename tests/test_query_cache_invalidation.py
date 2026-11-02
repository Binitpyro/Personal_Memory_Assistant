"""The persistent semantic `query_cache` must not outlive the index it answered from.

The LanceDB `query_cache` table stores verbatim question and answer text and is
served for any similar query. It used to be cleared only by "clear history", so
an answer quoting a re-indexed, deleted or removed file kept being served.

Every index-mutation path now clears it through `clear_all_query_caches`, and
`LanceDBClient.delete_documents` (the funnel for folder removal, a changed
file's old rows, OCR re-index and ghost cleanup) drops it too. A failure to
clear is logged at WARNING and never fails indexing.

Offline: the LanceDB tests use a temp directory, the rest use fakes.
"""

import logging
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.indexing.service import IndexingService, progress
from app.ocr.types import OcrLine, OcrPage
from app.search.retrieval import clear_all_query_caches
from app.storage.db import DatabaseManager
from app.vector_store.lancedb_client import LanceDBClient

pytestmark = pytest.mark.asyncio

FILE_PATH = Path(r"C:\docs\scanned.pdf")
LONG = "The quarterly revenue figures were reviewed by the audit committee. " * 4


@pytest.fixture
def real_lancedb():
    d = tempfile.mkdtemp(prefix="lancedb_qc_test_")
    client = LanceDBClient(persist_directory=d)
    client.connect()
    yield client
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def idle_progress():
    original = progress.status
    progress.status = "idle"
    yield
    progress.status = original


async def _cache_one(client: LanceDBClient) -> np.ndarray:
    vec = np.full(384, 0.9, dtype=np.float32)
    await client.add_query_cache(vec, "what did the audit say", "answer quoting a file", 1.0)
    assert await client.search_cache(vec.tolist(), threshold=0.99) is not None, "setup"
    return vec


async def _seed_chunks(client: LanceDBClient) -> None:
    await client.add_documents(
        ["1", "2"],
        [[0.1] * 384, [0.2] * 384],
        [{"folder_tag": "a", "path": "p"}, {"folder_tag": "a", "path": "p"}],
    )


# --- the shared function -----------------------------------------------------


async def test_clear_all_query_caches_empties_the_persistent_table(real_lancedb):
    vec = await _cache_one(real_lancedb)

    await clear_all_query_caches(real_lancedb)

    assert await real_lancedb.search_cache(vec.tolist(), threshold=0.99) is None


async def test_a_clear_failure_is_a_warning_not_an_exception(caplog):
    lance = AsyncMock()
    lance.clear_query_cache.side_effect = RuntimeError("table locked")

    with caplog.at_level(logging.WARNING, logger="app.search.retrieval"):
        await clear_all_query_caches(lance)  # must not raise

    assert any(
        r.levelno == logging.WARNING and "table locked" in r.getMessage() for r in caplog.records
    )


# --- LanceDBClient.delete_documents: folder removal, re-index, ghost cleanup -


async def test_deleting_chunks_drops_the_cached_answers(real_lancedb):
    vec = await _cache_one(real_lancedb)
    await _seed_chunks(real_lancedb)

    await real_lancedb.delete_documents(["1"])

    assert await real_lancedb.search_cache(vec.tolist(), threshold=0.99) is None
    assert real_lancedb.get_all_ids() == {"2"}, "the delete itself must still happen"


async def test_a_cache_that_cannot_be_dropped_does_not_fail_the_delete(
    real_lancedb, monkeypatch, caplog
):
    await _seed_chunks(real_lancedb)

    def _boom():
        raise RuntimeError("cache locked")

    monkeypatch.setattr(real_lancedb, "_drop_query_cache", _boom)

    with caplog.at_level(logging.WARNING, logger="app.vector_store.lancedb_client"):
        await real_lancedb.delete_documents(["1"])  # must not raise

    assert real_lancedb.get_all_ids() == {"2"}
    assert any("cache locked" in r.getMessage() for r in caplog.records)


# --- IndexingService ---------------------------------------------------------


@pytest.fixture
def service(mock_db, mock_emb, mock_lancedb):
    mock_lancedb.delete_documents = AsyncMock()
    mock_lancedb.clear_query_cache = AsyncMock()
    return IndexingService(mock_db, mock_emb, mock_lancedb)


async def _insert_pdf(db):
    ids = await db.batch_insert_files(
        [
            {
                "path": str(FILE_PATH.absolute()),
                "size": 1234,
                "modified_at": "2026-01-01T00:00:00",
                "type": ".pdf",
                "folder_tag": "docs",
                "sha256": "b" * 64,
            }
        ]
    )
    return ids[0]


def _page():
    return OcrPage(page_num=0, lines=(OcrLine(text=LONG, conf=0.95, low=False),), mean_conf=0.9)


@pytest.fixture
async def run_service(tmp_path, mock_emb, mock_lancedb):
    """A service over an on-disk DB: index_folders reads through a second connection."""
    db = DatabaseManager(str(tmp_path / "qc_index.db"))
    await db.init_db()
    mock_lancedb.delete_documents = AsyncMock()
    mock_lancedb.clear_query_cache = AsyncMock()
    yield IndexingService(db, mock_emb, mock_lancedb)
    await db.close()


async def test_an_index_run_clears_the_persistent_cache(
    run_service, mock_lancedb, tmp_path, monkeypatch
):
    service = run_service
    (tmp_path / "a.txt").write_text("hello world " * 20, encoding="utf-8")
    monkeypatch.setattr(service, "_batch_index_pipeline", AsyncMock())

    await service.index_folders([str(tmp_path)])

    mock_lancedb.clear_query_cache.assert_awaited()


async def test_ocr_reindex_clears_the_persistent_cache(service, mock_db, mock_lancedb):
    await _insert_pdf(mock_db)

    written = await service.index_ocr_pages(FILE_PATH, [_page()])

    assert written > 0
    mock_lancedb.clear_query_cache.assert_awaited_once()


async def test_a_cache_clear_failure_does_not_fail_ocr_indexing(
    service, mock_db, mock_lancedb, caplog
):
    await _insert_pdf(mock_db)
    mock_lancedb.clear_query_cache.side_effect = RuntimeError("table locked")

    with caplog.at_level(logging.WARNING, logger="app.search.retrieval"):
        written = await service.index_ocr_pages(FILE_PATH, [_page()])

    assert written > 0
    assert any("table locked" in r.getMessage() for r in caplog.records)


async def test_a_cache_clear_failure_does_not_fail_an_index_run(
    run_service, mock_lancedb, tmp_path, monkeypatch
):
    service = run_service
    (tmp_path / "a.txt").write_text("hello world " * 20, encoding="utf-8")
    monkeypatch.setattr(service, "_batch_index_pipeline", AsyncMock())
    mock_lancedb.clear_query_cache.side_effect = RuntimeError("table locked")

    await service.index_folders([str(tmp_path)])  # must not raise

    mock_lancedb.clear_query_cache.assert_awaited()
    assert progress.status != "error"


# --- POST /api/system/clear-cache --------------------------------------------


async def test_the_clear_cache_endpoint_clears_the_persistent_cache(client, mock_lancedb):
    mock_lancedb.clear_query_cache = AsyncMock()

    response = await client.post("/api/system/clear-cache")

    assert response.status_code == 200
    mock_lancedb.clear_query_cache.assert_awaited_once()
