"""Fleet lane L6 (storage), batch 2: regression tests for the audit findings.

A2-10 pma_* compaction, A2-11/A9-05/A6-10 one ANN-rebuild guard, A2-13/A10-15
duplicate FTS triggers, A2-15 /index/clear vs a running index, A1-09 cross-file
KG edges surviving a callee re-index, A5-07 BLOCKED_ROOTS ancestors.
Offline: temp-dir SQLite and LanceDB.
"""

import asyncio
from datetime import timedelta

import numpy as np
import pyarrow as pa
import pytest

from app.api.indexing import IndexRequest, _is_blocked_root
from app.storage.db import DatabaseManager
from app.vector_store.lancedb_client import LanceDBClient

pytestmark = pytest.mark.asyncio

DIM = 16


@pytest.fixture
async def fdb(tmp_path):
    db = DatabaseManager(str(tmp_path / "l6b2.db"))
    await db.init_db()
    yield db
    await db.close()


@pytest.fixture
def lance(tmp_path):
    c = LanceDBClient(persist_directory=str(tmp_path / "lance"))
    c.connect()
    return c


def _vec_table(start, n, seed=0):
    rng = np.random.default_rng(seed + start)
    return pa.table(
        {
            "id": pa.array([str(i) for i in range(start, start + n)]),
            "vector": pa.FixedSizeListArray.from_arrays(
                pa.array(rng.random(n * DIM, dtype=np.float32)), list_size=DIM
            ),
        }
    )


class _CountingTable:
    """Delegates to the real Lance table; counts full index builds."""

    def __init__(self, tbl):
        self._t = tbl
        self.builds = 0

    def create_index(self, **kw):
        self.builds += 1
        return self._t.create_index(**kw)

    def __getattr__(self, name):
        return getattr(self._t, name)


# --- A2-11 / A9-05 / A6-10 ---------------------------------------------------


async def test_hnsw_is_rebuilt_only_when_needed(lance):
    lance._create_or_open_table("pma_chunks", _vec_table(0, 1000))
    tbl = _CountingTable(lance._table_cache["pma_chunks"])
    lance._table_cache["pma_chunks"] = tbl

    await lance.create_hnsw_index("pma_chunks")
    assert tbl.builds == 1  # no index yet

    await lance.create_hnsw_index("pma_chunks")  # no-op run
    lance._table_cache["pma_chunks"]._t.add(_vec_table(1000, 20))  # one-file edit
    await lance.create_hnsw_index("pma_chunks")
    assert tbl.builds == 1, "small change must extend the index, not rebuild it"
    idx = tbl.list_indices()[0]
    assert tbl.index_stats(idx.name).num_unindexed_rows == 0

    tbl._t.add(_vec_table(2000, 800))  # corpus grew past the staleness fraction
    await lance.create_hnsw_index("pma_chunks")
    assert tbl.builds == 2


# --- A2-10 -------------------------------------------------------------------


async def test_pma_chunks_versions_are_compacted(lance, monkeypatch):
    monkeypatch.setattr(LanceDBClient, "_VERSION_RETENTION", timedelta(0))
    lance._create_or_open_table("pma_chunks", _vec_table(0, 600))
    tbl = lance._table_cache["pma_chunks"]
    for i in range(30):  # watcher-style delete + add of one file's chunks
        tbl.delete(f"id = '{i}'")
        tbl.add(_vec_table(10_000 + i, 1))
    assert len(tbl.list_versions()) > 50

    await lance.create_hnsw_index("pma_chunks")

    assert len(tbl.list_versions()) <= 3
    assert tbl.count_rows() == 600


# --- A2-13 / A10-15 ----------------------------------------------------------


def _chunk_triggers(db):
    conn = db._get_conn()
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='chunks' ORDER BY name"
    )


async def _trigger_names(db):
    async with _chunk_triggers(db) as cur:
        return [r[0] for r in await cur.fetchall()]


async def test_restart_after_ingest_leaves_one_fts_trigger_set(tmp_path):
    path = str(tmp_path / "trg.db")
    db = DatabaseManager(path)
    await db.init_db()
    await db.enter_ingest_mode()
    await db.exit_ingest_mode()
    await db.close()

    db = DatabaseManager(path)  # restart: schema.sql runs again
    await db.init_db()
    try:
        assert await _trigger_names(db) == ["chunk_fts_ad", "chunk_fts_ai", "chunk_fts_au"]
    finally:
        await db.close()


async def test_legacy_fts_triggers_are_dropped_on_boot(tmp_path):
    path = str(tmp_path / "legacy.db")
    db = DatabaseManager(path)
    await db.init_db()
    conn = db._get_conn()
    await conn.executescript(
        "CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN SELECT 1; END;"
        "CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN SELECT 1; END;"
        "CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN SELECT 1; END;"
    )
    await db.close()

    db = DatabaseManager(path)
    await db.init_db()
    try:
        assert await _trigger_names(db) == ["chunk_fts_ad", "chunk_fts_ai", "chunk_fts_au"]
    finally:
        await db.close()


# --- A1-09 -------------------------------------------------------------------


async def _edges(db):
    async with db._get_conn().execute(
        "SELECT source, target, relation FROM kg_edges ORDER BY source, target"
    ) as cur:
        return [tuple(r) for r in await cur.fetchall()]


async def _file_with_node(db, path, node_id, name, chunk_text):
    fid = await db.insert_file(
        {"path": path, "size": 1, "modified_at": "now", "type": ".py", "folder_tag": "t"}
    )
    (cid,) = await db.insert_chunks_bulk(
        [{"file_id": fid, "start_offset": 0, "end_offset": 1, "text_preview": chunk_text}]
    )
    await db.insert_kg_nodes_bulk([(node_id, "function", name, "{}", cid)])
    return fid


async def test_cross_file_edge_survives_callee_reindex(fdb):
    await _file_with_node(fdb, "a.py", "a.py::main", "main", "def main(): helper()")
    fid_b = await _file_with_node(fdb, "b.py", "b.py::helper", "helper", "def helper(): pass")
    await fdb.insert_kg_edges_bulk([("a.py::main", "PENDING::helper", "CALLS", 1.0, "{}")])
    await fdb.resolve_pending_graph_edges()
    assert await _edges(fdb) == [("a.py::main", "b.py::helper", "CALLS")]

    # b.py is edited: its rows go, then it is indexed again. a.py is unchanged
    # and is NOT re-extracted, so its edge must not be lost with b's old node.
    await fdb.remove_from_index(None, file_ids=[fid_b])
    await _file_with_node(fdb, "b.py", "b.py::helper", "helper", "def helper(): return 1")
    await fdb.resolve_pending_graph_edges()

    assert await _edges(fdb) == [("a.py::main", "b.py::helper", "CALLS")]


async def test_edge_is_dropped_when_callee_is_really_gone(fdb):
    await _file_with_node(fdb, "a.py", "a.py::main", "main", "def main(): helper()")
    fid_b = await _file_with_node(fdb, "b.py", "b.py::helper", "helper", "def helper(): pass")
    await fdb.insert_kg_edges_bulk([("a.py::main", "PENDING::helper", "CALLS", 1.0, "{}")])
    await fdb.resolve_pending_graph_edges()

    await fdb.remove_from_index(None, file_ids=[fid_b])
    await fdb.resolve_pending_graph_edges()

    assert await _edges(fdb) == []


# --- A2-15 -------------------------------------------------------------------


async def test_clear_index_refuses_while_indexing_runs(client, mock_lancedb):
    from unittest.mock import AsyncMock

    from app.indexing.service import indexing_lock

    mock_lancedb.clear_all = AsyncMock()
    async with indexing_lock:
        # Without the guard the handler would queue behind the lock forever.
        resp = await asyncio.wait_for(client.post("/api/index/clear"), timeout=5)
    assert resp.status_code == 409
    mock_lancedb.clear_all.assert_not_called()

    resp = await client.post("/api/index/clear")
    assert resp.status_code == 200
    mock_lancedb.clear_all.assert_called_once()


async def test_clear_index_holds_the_indexing_lock(client, mock_lancedb):
    from app.indexing.service import indexing_lock

    seen = []

    async def _clear():
        seen.append(indexing_lock.locked())

    mock_lancedb.clear_all = _clear
    assert (await client.post("/api/index/clear")).status_code == 200
    assert seen == [True]
    await asyncio.sleep(0)
    assert not indexing_lock.locked()


# --- A5-07 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/", "C:\\", "c:/", "C:\\Windows", "C:\\Windows\\System32", "/etc", "/etc/ssl", "/proc/1"],
)
async def test_blocked_roots_cover_ancestors_and_descendants(path):
    assert _is_blocked_root(path)


@pytest.mark.parametrize(
    "path",
    ["/devices_backup", "/bootcamp", "/system_notes", "D:\\data", "C:\\Users\\me", "/home/me"],
)
async def test_blocked_roots_do_not_overmatch_siblings(path):
    assert not _is_blocked_root(path)


async def test_validated_folders_rejects_filesystem_root(monkeypatch):
    import os

    monkeypatch.setattr(os.path, "realpath", lambda p: "/")
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    assert IndexRequest(folders=["/"]).validated_folders == []
