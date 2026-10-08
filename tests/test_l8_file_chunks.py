"""A8-06: a file clicked in the 3D view is resolved to chunk ids by path."""

import zlib
from pathlib import Path

import pytest

from app.api.search import file_chunks
from app.storage.db import DatabaseManager


@pytest.fixture
async def real_db(tmp_path: Path):
    mgr = DatabaseManager(str(tmp_path / "l8.db"))
    await mgr.init_db(schema_path="app/storage/schema.sql")
    yield mgr
    await mgr.close()


async def _seed(db, path, n):
    conn = db._get_conn()
    cur = await conn.execute(
        "INSERT INTO files (path, size, modified_at, created_at, type, folder_tag, summary) "
        "VALUES (?, 1, 'now', 'now', 'md', 't', 's')",
        (path,),
    )
    ids = []
    for i in range(n):
        c = await conn.execute(
            "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview) VALUES (?,?,?,?)",
            (cur.lastrowid, i, i + 1, zlib.compress(b"x" * 40)),
        )
        ids.append(c.lastrowid)
    await conn.commit()
    return ids


@pytest.mark.asyncio
async def test_clicked_file_resolves_to_its_own_chunks_not_a_node_index(real_db):
    other = await _seed(real_db, r"C:\Docs\tax_return_2025.pdf", 6)
    mine = await _seed(real_db, r"C:\Docs\holiday_notes.txt", 5)

    # The visualizer reports "/" separators; the files table keeps the OS spelling.
    res = await file_chunks(path="C:/Docs/holiday_notes.txt", db=real_db)

    assert res["chunk_ids"] == mine[:3]
    assert not set(res["chunk_ids"]) & set(other)
    # Unknown file: nothing is forced, rather than guessing.
    assert (await file_chunks(path="C:/Docs/nope.txt", db=real_db))["chunk_ids"] == []
