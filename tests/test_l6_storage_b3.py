"""Fleet lane L6 (storage), batch 3: A2-14 pre-migration boot, A2-16 cheap FTS merge,
A5-14 dead executor, A10-17 streaming get_max_id, A10-13 maintenance tests that can fail.
Offline: temp-dir SQLite and LanceDB.
"""

import sqlite3

import numpy as np
import pyarrow as pa
import pytest

from app.storage.db import DatabaseManager
from app.vector_store.lancedb_client import LanceDBClient

pytestmark = pytest.mark.asyncio

DIM = 8


# --- A2-14 -------------------------------------------------------------------


async def test_pre_sha256_database_boots_and_migrates(tmp_path):
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL,
            size INTEGER NOT NULL, modified_at TEXT NOT NULL, type TEXT NOT NULL,
            folder_tag TEXT, usage_count INTEGER DEFAULT 0);
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, file_id INTEGER NOT NULL,
            start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
            text_preview TEXT NOT NULL);
        INSERT INTO files (path, size, modified_at, type) VALUES ('/a.txt', 1, 't', 'txt');
        """
    )
    raw.commit()
    raw.close()

    db = DatabaseManager(path)
    try:
        await db.init_db()  # raised "no such column: sha256" before the fix
        conn = db._get_conn()
        async with conn.execute("PRAGMA table_info(files)") as cur:
            cols = {r[1] for r in await cur.fetchall()}
        assert {"sha256", "extract_status", "root_path", "summary"} <= cols
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'idx_files_change_detection'"
        ) as cur:
            assert await cur.fetchone() is not None
        async with conn.execute("SELECT path FROM files") as cur:
            assert [r[0] for r in await cur.fetchall()] == ["/a.txt"]
    finally:
        await db.close()


# --- A2-16 -------------------------------------------------------------------


async def test_post_index_fts_optimize_is_a_bounded_merge_not_a_full_rewrite(tmp_path):
    db = DatabaseManager(str(tmp_path / "fts.db"))
    await db.init_db()
    try:
        conn = db._get_conn()
        seen: list[str] = []
        await conn.set_trace_callback(seen.append)
        await db.fts_optimize()
        assert not any("'optimize'" in s for s in seen), seen
        assert any("'merge'" in s for s in seen), seen
        seen.clear()
        await db.fts_optimize(full=True)  # compact-db keeps the full rewrite
        assert any("'optimize'" in s for s in seen), seen
    finally:
        await db.close()


# --- A5-14 -------------------------------------------------------------------


async def test_indexing_api_has_no_unused_encode_executor():
    import app.api.indexing as mod

    assert not hasattr(mod, "_ENCODE_EXECUTOR")


# --- A10-17 ------------------------------------------------------------------


async def test_get_max_id_streams_batches(tmp_path):
    c = LanceDBClient(persist_directory=str(tmp_path / "lance"))
    c.connect()
    n = 5000
    tbl = pa.table(
        {
            "id": pa.array([str(i) for i in range(n)]),
            "vector": pa.FixedSizeListArray.from_arrays(
                pa.array(np.zeros(n * DIM, dtype=np.float32)), list_size=DIM
            ),
        }
    )
    c.db.create_table("pma_chunks", tbl)

    class _Spy:
        """Fails if the whole id column is materialised at once."""

        def __init__(self, q):
            self._q = q

        def select(self, cols):
            return _Spy(self._q.select(cols))

        def to_arrow(self):
            raise AssertionError("get_max_id materialised the whole id column")

        def to_batches(self, *a, **k):
            return self._q.to_batches(*a, **k)

    real = c._get_table("pma_chunks")

    class _T:
        def search(self, *a, **k):
            return _Spy(real.search(*a, **k))

    c._get_table = lambda name: _T()  # type: ignore[method-assign]
    assert c.get_max_id() == n - 1
