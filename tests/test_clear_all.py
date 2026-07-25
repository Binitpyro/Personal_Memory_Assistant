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
