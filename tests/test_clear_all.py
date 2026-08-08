import zlib

import pytest

from app.storage.db import DatabaseManager


@pytest.mark.asyncio
async def test_clear_all_and_fts_behavior(mock_db: DatabaseManager):
    """L-01: Verify that clear_all doesn't break FTS triggers and handles compressed data."""
    # 1. Insert file
    file_data = {
        "path": "test.txt",
        "size": 100,
        "modified_at": "2026-05-11T00:00:00",
        "type": ".txt",
        "folder_tag": "Test",
    }
    file_id = await mock_db.insert_file(file_data)

    # 2. Insert compressed chunk
    text = "This is a test chunk with some unique keyword."
    compressed = zlib.compress(text.encode("utf-8"))

    await mock_db.execute_write(
        "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview) VALUES (?,?,?,?)",
        (file_id, 0, len(text), compressed),
    )

    # 3. Verify FTS search works before clear
    results = await mock_db.execute_query(
        "SELECT rowid FROM chunk_fts WHERE chunks_text MATCH ?", ("unique",)
    )
    assert len(results) == 1

    # 4. Perform clear_all
    await mock_db.clear_all()

    # Verify everything is empty
    rows, chunks = await mock_db.get_counts()
    assert rows == 0
    assert chunks == 0

    # 5. Re-insert and verify FTS still works (verifies triggers are correctly recreated)
    file_id_2 = await mock_db.insert_file(
        {
            "path": "test2.txt",
            "size": 100,
            "modified_at": "2026-05-11T00:00:01",
            "type": ".txt",
            "folder_tag": "Test",
        }
    )

    await mock_db.execute_write(
        "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview) VALUES (?,?,?,?)",
        (file_id_2, 0, len(text), compressed),
    )

    # Search FTS again
    results = await mock_db.execute_query(
        "SELECT rowid FROM chunk_fts WHERE chunks_text MATCH ?", ("unique",)
    )
    assert len(results) == 1


@pytest.mark.asyncio
async def test_clear_all_drops_the_ocr_queue_but_keeps_the_cache(mock_db: DatabaseManager):
    """The asymmetry is deliberate and easy to "fix" by mistake.

    `ocr_queue` refers to files that no longer exist after a wipe, so it goes.
    `ocr_cache` is keyed on content hash, so it stays valid and makes
    re-indexing the same documents free instead of re-running OCR.
    """
    from app.ocr import cache as ocr_cache
    from app.ocr import queue as ocr_queue
    from app.ocr.types import OcrLine, OcrPage

    await ocr_queue.enqueue_document(mock_db, r"C:\docs\scan.pdf", [0, 1], 2)
    await ocr_cache.put_pages(
        mock_db,
        "d" * 64,
        [OcrPage(page_num=0, lines=(OcrLine("cached page text", 0.9, False),), mean_conf=0.9)],
    )

    await mock_db.clear_all()

    queued = await mock_db.execute_query("SELECT COUNT(*) FROM ocr_queue")
    cached = await mock_db.execute_query("SELECT COUNT(*) FROM ocr_cache")
    assert queued[0][0] == 0, "ocr_queue should be cleared with the rest of the index"
    assert cached[0][0] == 1, "ocr_cache must survive - it is keyed on content, not file id"


@pytest.mark.asyncio
async def test_clear_vectors_only_leaves_files_chunks_and_fts_intact(mock_db: DatabaseManager):
    """clear_vectors_only() is the model-change-safe counterpart to clear_all():
    it must remove chunk_embeddings only, leaving everything else - including
    FTS search results - untouched."""
    file_data = {
        "path": "test.txt",
        "size": 100,
        "modified_at": "2026-05-11T00:00:00",
        "type": ".txt",
        "folder_tag": "Test",
    }
    file_id = await mock_db.insert_file(file_data)

    text = "This is a test chunk with some unique keyword."
    compressed = zlib.compress(text.encode("utf-8"))

    await mock_db.execute_write(
        "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview) VALUES (?,?,?,?)",
        (file_id, 0, len(text), compressed),
    )
    chunk_id_row = await mock_db.execute_query("SELECT id FROM chunks LIMIT 1")
    chunk_id = chunk_id_row[0]["id"]

    await mock_db.insert_chunk_embeddings_bulk([(chunk_id, b"\x00" * 384)])

    embeddings_before = await mock_db.execute_query("SELECT COUNT(*) as c FROM chunk_embeddings")
    assert embeddings_before[0]["c"] == 1

    result = await mock_db.clear_vectors_only()
    assert result == {"embeddings_removed": 1}

    embeddings_after = await mock_db.execute_query("SELECT COUNT(*) as c FROM chunk_embeddings")
    assert embeddings_after[0]["c"] == 0

    # files/chunks/FTS survive untouched
    rows, chunks = await mock_db.get_counts()
    assert rows == 1
    assert chunks == 1

    results = await mock_db.execute_query(
        "SELECT rowid FROM chunk_fts WHERE chunks_text MATCH ?", ("unique",)
    )
    assert len(results) == 1
