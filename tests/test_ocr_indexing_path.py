"""`IndexingService.index_ocr_pages` - the OCR results-to-index path.

This is where OCR output becomes searchable, and where a mistake either loses
native text or leaks low-confidence garbage into search. Both are covered here.
"""

import zlib
from pathlib import Path

import pytest

from app.indexing.service import IndexingService, progress
from app.ocr.types import OcrLine, OcrPage

FILE_PATH = Path(r"C:\docs\scanned.pdf")


@pytest.fixture
def service(mock_db, mock_emb, mock_lancedb):
    mock_lancedb.delete_documents = (
        mock_lancedb.delete_documents if hasattr(mock_lancedb, "delete_documents") else None
    )
    from unittest.mock import AsyncMock

    mock_lancedb.delete_documents = AsyncMock()
    return IndexingService(mock_db, mock_emb, mock_lancedb)


@pytest.fixture(autouse=True)
def idle_progress():
    """index_ocr_pages refuses to run while indexing is active."""
    original = progress.status
    progress.status = "idle"
    yield
    progress.status = original


async def insert_file(db, path=FILE_PATH, folder_tag="docs"):
    ids = await db.batch_insert_files(
        [
            {
                "path": str(path.absolute()),
                "size": 1234,
                "modified_at": "2026-01-01T00:00:00",
                "type": ".pdf",
                "folder_tag": folder_tag,
                "sha256": "b" * 64,
            }
        ]
    )
    return ids[0]


def ocr_page(num, *lines):
    return OcrPage(
        page_num=num,
        lines=tuple(OcrLine(text=t, conf=c, low=low) for t, c, low in lines),
        mean_conf=0.9,
    )


LONG = "The quarterly revenue figures were reviewed by the audit committee. " * 4


async def test_chunks_are_written_with_ocr_provenance(service, mock_db):
    await insert_file(mock_db)

    written = await service.index_ocr_pages(FILE_PATH, [ocr_page(0, (LONG, 0.95, False))])

    assert written > 0
    rows = await mock_db.execute_query("SELECT source FROM chunks")
    assert rows and all(r[0] == "ocr" for r in rows)


async def test_ocr_text_becomes_searchable_via_fts(service, mock_db):
    """Proves the chunks_ai trigger fired - no explicit FTS work is done."""
    await insert_file(mock_db)
    await service.index_ocr_pages(
        FILE_PATH, [ocr_page(0, ("Torvosaurus tanneri holotype specimen. " * 5, 0.95, False))]
    )

    hits = await mock_db.execute_query(
        "SELECT rowid FROM chunk_fts WHERE chunk_fts MATCH ?", ("Torvosaurus",)
    )
    assert hits


async def test_low_confidence_lines_are_excluded_from_the_index(service, mock_db):
    """They stay in ocr_cache; only the indexed text drops them."""
    await insert_file(mock_db)
    page = ocr_page(
        0,
        (LONG, 0.95, False),
        ("qqzzxx unreadable smudge", 0.05, True),
    )

    await service.index_ocr_pages(FILE_PATH, [page])

    rows = await mock_db.execute_query("SELECT text_preview FROM chunks")
    stored = " ".join(zlib.decompress(r[0]).decode("utf-8") for r in rows)
    assert "quarterly revenue" in stored
    assert "unreadable smudge" not in stored

    hits = await mock_db.execute_query(
        "SELECT rowid FROM chunk_fts WHERE chunk_fts MATCH ?", ("unreadable",)
    )
    assert not hits


async def test_reindexing_replaces_only_ocr_chunks(service, mock_db):
    """A mixed PDF's natively extracted text must survive a re-OCR."""
    file_id = await insert_file(mock_db)
    await mock_db.insert_chunks_bulk(
        [
            {
                "file_id": file_id,
                "start_offset": 0,
                "end_offset": 10,
                "text_preview": "native body text that was extracted normally",
                "sentence_offsets": "[]",
                "segmenter_version": "py_v1",
                "source": None,
            }
        ]
    )

    await service.index_ocr_pages(FILE_PATH, [ocr_page(0, (LONG, 0.95, False))])
    await service.index_ocr_pages(FILE_PATH, [ocr_page(0, (LONG, 0.95, False))])

    native = await mock_db.execute_query(
        "SELECT COUNT(*) FROM chunks WHERE source IS NULL AND file_id = ?", (file_id,)
    )
    assert native[0][0] == 1, "native chunk was destroyed by re-OCR"


async def _ocr_chunk_count(db, file_id):
    rows = await db.execute_query(
        "SELECT COUNT(*) FROM chunks WHERE source = 'ocr' AND file_id = ?", (file_id,)
    )
    return rows[0][0]


async def test_failed_insert_does_not_destroy_the_previous_ocr_text(service, mock_db, monkeypatch):
    """Both deletes used to commit before the insert, so the rollback restored nothing."""
    file_id = await insert_file(mock_db)
    await service.index_ocr_pages(FILE_PATH, [ocr_page(0, (LONG, 0.95, False))])
    before = await _ocr_chunk_count(mock_db, file_id)
    assert before > 0

    async def _boom(*_a, **_kw):
        raise RuntimeError("sqlite flush exploded")

    monkeypatch.setattr(service, "_flush_pending_chunks_sqlite", _boom)

    with pytest.raises(RuntimeError, match="sqlite flush exploded"):
        await service.index_ocr_pages(FILE_PATH, [ocr_page(0, (LONG, 0.95, False))])

    assert await _ocr_chunk_count(mock_db, file_id) == before, (
        "rollback left the file with no OCR text at all"
    )


async def test_a_scan_that_yields_nothing_keeps_the_previous_text(service, mock_db):
    """Every line under the confidence floor is a bad scan, not a delete instruction."""
    file_id = await insert_file(mock_db)
    await service.index_ocr_pages(FILE_PATH, [ocr_page(0, (LONG, 0.95, False))])
    before = await _ocr_chunk_count(mock_db, file_id)
    assert before > 0

    assert await service.index_ocr_pages(FILE_PATH, [ocr_page(0, ("blurry", 0.02, True))]) == 0

    assert await _ocr_chunk_count(mock_db, file_id) == before


async def test_missing_file_row_is_a_clean_noop(service, mock_db):
    """The file was deleted between enqueue and drain."""
    assert await service.index_ocr_pages(FILE_PATH, [ocr_page(0, (LONG, 0.95, False))]) == 0
    rows = await mock_db.execute_query("SELECT COUNT(*) FROM chunks")
    assert rows[0][0] == 0


async def test_pages_with_no_indexable_text_write_nothing(service, mock_db):
    await insert_file(mock_db)
    only_low = ocr_page(0, ("blurry", 0.02, True))
    assert await service.index_ocr_pages(FILE_PATH, [only_low]) == 0


async def test_vectors_carry_the_folder_tag(service, mock_db, mock_lancedb):
    await insert_file(mock_db, folder_tag="research")
    await service.index_ocr_pages(FILE_PATH, [ocr_page(0, (LONG, 0.95, False))])

    mock_lancedb.add_documents.assert_awaited()
    _ids, _embs, metas = mock_lancedb.add_documents.await_args.args
    assert all(m["folder_tag"] == "research" for m in metas)


async def test_pages_are_indexed_in_page_order(service, mock_db):
    await insert_file(mock_db)
    await service.index_ocr_pages(
        FILE_PATH,
        [
            ocr_page(2, ("ZZZ third page marker. " * 6, 0.9, False)),
            ocr_page(0, ("AAA first page marker. " * 6, 0.9, False)),
        ],
    )

    rows = await mock_db.execute_query("SELECT text_preview FROM chunks ORDER BY id")
    stored = " ".join(zlib.decompress(r[0]).decode("utf-8") for r in rows)
    assert stored.index("AAA first") < stored.index("ZZZ third")


async def test_can_run_while_indexing_is_active(service, mock_db):
    """OCR pages can be indexed concurrently while the indexing pipeline is active."""
    await insert_file(mock_db)
    progress.status = "running"

    written = await service.index_ocr_pages(FILE_PATH, [ocr_page(0, (LONG, 0.95, False))])
    assert written > 0
    rows = await mock_db.execute_query("SELECT source FROM chunks WHERE source = 'ocr'")
    assert rows


async def test_ocr_write_cannot_commit_the_indexers_transaction(service, mock_db):
    """The defect this savepoint exists for.

    index_ocr_pages used begin_transaction()/commit(). begin_transaction()
    no-ops when _in_external_transaction is already set, so the commit() that
    followed committed *the indexer's* open transaction and cleared the flag -
    making a half-finished index batch durable and leaving the storer writing
    outside any transaction for the rest of its window.
    """
    await insert_file(mock_db)

    # Stand in for the storer holding its commit window open.
    conn = mock_db._get_conn()
    await mock_db.begin_transaction()
    await conn.execute(
        "INSERT INTO files (path, size, modified_at, type, folder_tag, sha256) "
        "VALUES ('indexer-in-flight.txt', 1, '2026-01-01T00:00:00', '.txt', 'docs', 'x')"
    )

    written = await service.index_ocr_pages(FILE_PATH, [ocr_page(0, (LONG, 0.95, False))])
    assert written > 0

    assert mock_db._in_external_transaction is True, (
        "the indexer's transaction must still be open after an OCR write"
    )

    # And it is still *its* transaction: rolling back drops the indexer's row.
    await mock_db.rollback_transaction()
    rows = await mock_db.execute_query(
        "SELECT COUNT(*) FROM files WHERE path = 'indexer-in-flight.txt'"
    )
    assert rows[0][0] == 0, "the indexer must still own its own rollback"


async def test_failed_ocr_write_does_not_discard_the_indexers_work(service, mock_db):
    """The mirror image: rollback_transaction() used to bin the indexer's chunks."""
    file_id = await insert_file(mock_db)

    conn = mock_db._get_conn()
    await mock_db.begin_transaction()
    await conn.execute(
        "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview) VALUES (?,?,?,?)",
        (file_id, 0, 5, zlib.compress(b"indexer chunk, uncommitted")),
    )

    # Make the OCR insert blow up midway.
    async def boom(*_a, **_kw):
        raise RuntimeError("insert exploded")

    service._flush_pending_chunks_sqlite = boom

    with pytest.raises(RuntimeError, match="insert exploded"):
        await service.index_ocr_pages(FILE_PATH, [ocr_page(0, (LONG, 0.95, False))])

    assert mock_db._in_external_transaction is True
    rows = await mock_db.execute_query("SELECT COUNT(*) FROM chunks WHERE file_id = ?", (file_id,))
    assert rows[0][0] >= 1, "the indexer's uncommitted chunk must survive an OCR rollback"
    await mock_db.rollback_transaction()


async def test_savepoint_commits_normally_with_no_indexer_transaction(service, mock_db):
    """Standalone OCR must still be durable - SAVEPOINT/RELEASE outside a
    transaction has to behave like BEGIN/COMMIT."""
    await insert_file(mock_db)
    assert mock_db._in_external_transaction is False

    written = await service.index_ocr_pages(FILE_PATH, [ocr_page(0, (LONG, 0.95, False))])
    assert written > 0
    assert mock_db._in_external_transaction is False

    rows = await mock_db.execute_query("SELECT COUNT(*) FROM chunks WHERE source = 'ocr'")
    assert rows[0][0] > 0
