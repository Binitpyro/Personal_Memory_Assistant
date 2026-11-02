"""Fleet lane L6 (storage), batch 1: regression tests for the audit findings.

A2-02 folder-remove boundary / all folders, A2-03 API writes vs the indexer's
open transaction, A2-06 query_cache prune on first write, A2-05 HNSW rebuild vs
the write lock, A2-09/A10-07 incremental_vacuum, A2-12/A10-09 one removal helper.
Offline: temp-dir SQLite and LanceDB, fake vector client.
"""

import asyncio
import os
import threading
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pyarrow as pa
import pytest

from app.storage.db import DatabaseManager
from app.vector_store.lancedb_client import LanceDBClient

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def fdb(tmp_path):
    db = DatabaseManager(str(tmp_path / "l6.db"))
    await db.init_db()
    yield db
    await db.close()


def _fake_vectors():
    v = MagicMock()
    v.delete_documents = AsyncMock()
    v.delete_summaries_by_ids = AsyncMock()
    return v


async def _add_file(db, path, tag="t", chunk_text=None):
    fid = await db.insert_file(
        {"path": str(path), "size": 1, "modified_at": "now", "type": ".txt", "folder_tag": tag}
    )
    cids = []
    if chunk_text:
        cids = await db.insert_chunks_bulk(
            [
                {
                    "file_id": fid,
                    "start_offset": 0,
                    "end_offset": len(chunk_text),
                    "text_preview": chunk_text,
                }
            ]
        )
    return fid, cids


async def _paths(db):
    return sorted(str(r["path"]) for r in await db.get_all_files())


# --- A2-02 -------------------------------------------------------------------


async def test_folder_removal_respects_boundary_and_wildcards(fdb, tmp_path):
    names = ["proj", "project2", "my_docs", "myXdocs", "p%"]
    for n in names:
        await _add_file(fdb, tmp_path / n / "f.txt")
    await _add_file(fdb, tmp_path / "keep.txt")

    await fdb.remove_from_index(None, folder=str(tmp_path / "proj"))
    await fdb.remove_from_index(None, folder=str(tmp_path / "my_docs"))
    await fdb.remove_from_index(None, folder=str(tmp_path / "p%"))

    left = await _paths(fdb)
    assert str(tmp_path / "project2" / "f.txt") in left
    assert str(tmp_path / "myXdocs" / "f.txt") in left
    assert str(tmp_path / "keep.txt") in left
    assert len(left) == 3, left


async def test_remove_endpoint_processes_every_folder(client, mock_db, mock_lancedb, tmp_path):
    mock_lancedb.delete_documents = AsyncMock()
    mock_lancedb.delete_summaries_by_ids = AsyncMock()
    for n in ("a", "b", "ab"):
        await _add_file(mock_db, tmp_path / n / "f.txt", chunk_text="hello " + n)

    resp = await client.post(
        "/api/index/folder/remove", json={"folders": [str(tmp_path / "a"), str(tmp_path / "b")]}
    )

    assert resp.status_code == 200
    assert resp.json()["chunks_removed"] == 2
    assert await _paths(mock_db) == [str(tmp_path / "ab" / "f.txt")]


# --- A2-12 / A10-09 ----------------------------------------------------------


async def test_remove_from_index_takes_everything_with_it(fdb, tmp_path):
    folder = tmp_path / "proj"
    other = tmp_path / "other"
    fid, cids = await _add_file(fdb, folder / "a.txt", tag="proj", chunk_text="zebra crossing")
    _, ocids = await _add_file(fdb, other / "b.txt", tag="other", chunk_text="zebra stripes")
    for p in (folder / "a.txt", other / "b.txt"):
        await fdb.execute_write("INSERT INTO ocr_queue (file_path) VALUES (?)", (str(p),))
    for f, tag in ((folder, "proj"), (other, "other")):
        await fdb.upsert_folder_profile(
            {
                "folder_path": str(f),
                "folder_tag": tag,
                "profile_text": "",
                "project_type": "x",
                "file_count": 1,
                "total_size_bytes": 1,
                "top_extensions": "",
                "key_files": "",
            }
        )
    vec = _fake_vectors()

    res = await fdb.remove_from_index(vec, folder=str(folder))

    assert res["chunks_removed"] == 1
    vec.delete_documents.assert_awaited_once_with([str(c) for c in cids])
    (summary_ids,) = vec.delete_summaries_by_ids.await_args.args
    assert set(summary_ids) == {f"file_{fid}", "folder_profile_proj"}
    assert [r["folder_path"] for r in await fdb.get_all_folder_profiles()] == [str(other)]
    rows = await fdb.execute_query("SELECT file_path FROM ocr_queue")
    assert [r[0] for r in rows] == [str(other / "b.txt")]
    fts = await fdb.execute_query("SELECT rowid FROM chunk_fts WHERE chunk_fts MATCH 'zebra'")
    assert [r[0] for r in fts] == ocids


async def test_cleanup_stale_files_removes_vectors_in_portable_mode(fdb, tmp_path):
    live = tmp_path / "live.txt"
    live.write_text("x")
    await _add_file(fdb, live, chunk_text="live text")
    gone_id, gone_chunks = await _add_file(fdb, tmp_path / "gone.txt", chunk_text="gone text")
    vec = _fake_vectors()

    cleaned = await fdb.cleanup_stale_files(lancedb_client=vec)

    assert cleaned == [str(tmp_path / "gone.txt")]
    vec.delete_documents.assert_awaited_once_with([str(c) for c in gone_chunks])
    vec.delete_summaries_by_ids.assert_awaited_once_with([f"file_{gone_id}"])
    assert await _paths(fdb) == [str(live)]


# --- A2-03 -------------------------------------------------------------------


async def test_api_writes_do_not_commit_the_indexers_open_transaction(fdb, tmp_path):
    await fdb.begin_transaction()
    await _add_file_in_txn(fdb, tmp_path / "half.txt")

    await fdb.save_query("q", "a", 1, 1.0)
    await fdb.save_telemetry(None, 1.0, "m", "c", 1, 1, 1, 0)
    await fdb.batch_increment_usage([str(tmp_path / "half.txt")])
    await fdb.increment_usage_count(str(tmp_path / "half.txt"))
    await fdb.set_system_state("k", "v")
    await fdb.clear_query_history()
    await fdb.remove_from_index(None, folder=str(tmp_path / "nothing"))
    await fdb.rollback_transaction()

    assert await _paths(fdb) == [], "a foreign commit made the half-built window durable"


async def _add_file_in_txn(db, path):
    fid = await db.insert_file(
        {"path": str(path), "size": 1, "modified_at": "now", "type": ".txt", "folder_tag": "t"},
        auto_commit=False,
    )
    return fid, []


# --- A2-09 / A10-07 ----------------------------------------------------------


async def _freelist(db):
    return (await db.execute_query("PRAGMA freelist_count"))[0][0]


async def _make_free_pages(db, tmp_path):
    for i in range(60):
        await db.insert_file(
            {
                "path": str(tmp_path / f"{i}.txt"),
                "size": 1,
                "modified_at": "now",
                "type": ".txt",
                "folder_tag": "t",
                "summary": "x" * 40000,
            }
        )
    await db.execute_write("DELETE FROM files")
    return await _freelist(db)


async def test_one_vacuum_step_frees_n_pages_not_one(fdb, tmp_path):
    before = await _make_free_pages(fdb, tmp_path)
    assert before > 10

    left = await fdb._incremental_vacuum_step(4)

    assert left == before - 4


async def test_incremental_vacuum_frees_every_page(fdb, tmp_path):
    before = await _make_free_pages(fdb, tmp_path)
    assert before > 10

    await fdb.incremental_vacuum(4)

    assert await _freelist(fdb) == 0


async def test_compact_db_endpoint_frees_every_page(client, tmp_path):
    from app.api import system as system_api
    from app.api.deps import get_db
    from app.main import app
    from app.state import bg_tasks

    db = DatabaseManager(str(tmp_path / "compact.db"))
    await db.init_db()
    try:
        assert await _make_free_pages(db, tmp_path) > 10
        app.dependency_overrides[get_db] = lambda: db
        resp = await client.post("/api/system/compact-db")
        assert resp.status_code == 200
        await asyncio.gather(*list(bg_tasks))
        assert system_api._vacuum_last_error is None
        assert await _freelist(db) == 0
    finally:
        await db.close()


# --- lancedb_client ----------------------------------------------------------


@pytest.fixture
def lance(tmp_path):
    c = LanceDBClient(persist_directory=str(tmp_path / "lance"))
    c.connect()
    return c


async def test_query_cache_is_pruned_on_the_first_write_of_a_process(lance, monkeypatch):
    calls = []
    real = lance.prune_query_cache

    async def _spy(max_rows):
        calls.append(max_rows)
        return await real(max_rows)

    monkeypatch.setattr(lance, "prune_query_cache", _spy)
    vec = np.ones(8, dtype=np.float32)

    await lance.add_query_cache(vec, "q1", "a1", 1.0)
    assert len(calls) == 1, "a light session never pruned"
    await lance.add_query_cache(vec, "q2", "a2", 2.0)
    assert len(calls) == 1


async def test_hnsw_rebuild_does_not_block_query_cache_writes(lance):
    seed = pa.table(
        {
            "id": pa.array(["1"]),
            "vector": pa.FixedSizeListArray.from_arrays(
                pa.array(np.ones(8, dtype=np.float32)), list_size=8
            ),
        }
    )
    lance._create_or_open_table("pma_chunks", seed)
    release = threading.Event()
    started = threading.Event()

    class _BlockingTable:
        def create_index(self, **kw):
            started.set()
            release.wait(30)

    lance._table_cache["pma_chunks"] = _BlockingTable()
    rebuild = asyncio.create_task(lance.create_hnsw_index("pma_chunks"))
    try:
        while not started.is_set():
            await asyncio.sleep(0.01)
        await asyncio.wait_for(
            lance.add_query_cache(np.ones(8, dtype=np.float32), "q", "a", 1.0), timeout=5
        )
    finally:
        release.set()
        await rebuild
    assert os.path.isdir(lance.persist_directory)
