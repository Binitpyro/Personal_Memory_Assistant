"""Integration lane: A1-04 vanished files, run_id, A10-11 shutdown cancel, db connect leak."""

import asyncio
import zlib

import numpy as np
import pytest

from app.indexing import service as idx
from app.storage.db import DatabaseManager
from app.vector_store.lancedb_client import LanceDBClient

pytestmark = pytest.mark.asyncio


class _Emb:
    async def embed_texts(self, texts, batch_size=None, progress_callback=None):
        if progress_callback:
            progress_callback(1, 1)
        rng = np.random.default_rng(0)
        return rng.random((len(texts), 8), dtype=np.float32)


async def _env(tmp_path):
    db = DatabaseManager(str(tmp_path / "int.db"))
    await db.init_db(schema_path="app/storage/schema.sql")
    lance = LanceDBClient(persist_directory=str(tmp_path / "lance"))
    return db, lance, idx.IndexingService(db, _Emb(), lance)  # type: ignore[arg-type]


async def _run(service, folders):
    idx.progress.status = "idle"
    await service.index_folders([str(f) for f in folders])


async def _names(db):
    return sorted(
        str(r["path"]).replace("\\", "/").rsplit("/", 1)[-1] for r in await db.get_all_files()
    )


async def _chunk_ids(db):
    return {str(r[0]) for r in await db.execute_query("SELECT id FROM chunks")}


async def _text(db):
    rows = await db.execute_query("SELECT text_preview FROM chunks")
    return " ".join(zlib.decompress(r[0]).decode() for r in rows)


def _doc(folder, name, word):
    p = folder / name
    p.write_text((word + " ") * 60, encoding="utf-8")
    return p


async def test_a1_04_deleted_and_renamed_files_leave_the_index(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    gone = _doc(root, "gone.txt", "zebrafish")
    moved = _doc(root, "moved.txt", "walrus")
    _doc(root, "kept.txt", "otter")
    db, lance, service = await _env(tmp_path)
    try:
        await _run(service, [root])
        assert await _names(db) == ["gone.txt", "kept.txt", "moved.txt"]
        assert lance.count_rows() == len(await _chunk_ids(db)) > 0
        walrus_before = (await _text(db)).count("walrus")

        gone.unlink()
        moved.rename(root / "renamed.txt")
        await _run(service, [root])

        assert await _names(db) == ["kept.txt", "renamed.txt"]
        text = await _text(db)
        assert "zebrafish" not in text
        # The renamed content is indexed once, not once under each name.
        assert text.count("walrus") == walrus_before > 0
        # No orphan vectors: LanceDB holds exactly the chunks SQLite holds.
        assert lance.get_all_ids() == await _chunk_ids(db)
        fts = await db.execute_query(
            "SELECT count(*) FROM chunk_fts WHERE chunk_fts MATCH 'zebrafish'"
        )
        assert fts[0][0] == 0
    finally:
        await db.close()


async def test_a1_04_unreachable_or_emptied_root_is_never_wiped(tmp_path):
    a, b, c = (tmp_path / n for n in ("a", "b", "c"))
    for d in (a, b, c):
        d.mkdir()
    _doc(a, "a.txt", "alpha")
    _doc(b, "b.txt", "bravo")
    _doc(c, "c.txt", "charlie")
    db, _lance, service = await _env(tmp_path)
    try:
        await _run(service, [a, b, c])
        assert await _names(db) == ["a.txt", "b.txt", "c.txt"]
        ids = await _chunk_ids(db)
        # Indexed earlier but skipped by today's scan (hidden), yet still on disk:
        # the scanner not seeing a file is not evidence that it is gone.
        hidden = _doc(a, ".hidden.txt", "quiet")
        await db.insert_file(
            {
                "path": str(hidden.absolute()),
                "size": 1,
                "modified_at": "x",
                "type": ".txt",
                "folder_tag": "a",
            }
        )

        # b: the drive/folder is gone entirely (resolve_folder_overlaps drops it).
        b.rename(tmp_path / "b_unplugged")
        # c: the directory is still there but the scan sees nothing in it.
        (c / "c.txt").rename(c / "c.bin")
        _doc(a, "a2.txt", "alpha2")
        await _run(service, [a, b, c])

        assert await _names(db) == [".hidden.txt", "a.txt", "a2.txt", "b.txt", "c.txt"]
        assert ids <= await _chunk_ids(db)
    finally:
        await db.close()


async def test_a1_04_cancelled_run_removes_nothing(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    gone = _doc(root, "gone.txt", "zebrafish")
    _doc(root, "kept.txt", "otter")
    db, _lance, service = await _env(tmp_path)
    try:
        await _run(service, [root])
        gone.unlink()
        real = service._detect_changes

        async def cancelling(*a, **k):
            service.cancel_indexing()
            return await real(*a, **k)

        service._detect_changes = cancelling
        await _run(service, [root])
        assert await _names(db) == ["gone.txt", "kept.txt"]
    finally:
        idx.progress.is_cancelled = False
        await db.close()


async def test_run_id_is_bumped_once_per_run(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    _doc(root, "a.txt", "alpha")
    db, _lance, service = await _env(tmp_path)
    try:
        start = idx.progress.run_id
        await _run(service, [root])
        assert idx.progress.run_id == start + 1
        await _run(service, [root])  # nothing changed: still a run
        assert idx.progress.run_id == start + 2
    finally:
        await db.close()


async def test_progress_stream_carries_run_id(client):
    from app.api.deps import ensure_indexing

    _, progress = ensure_indexing()
    progress.status = "idle"
    progress.run_id = 41
    res = await client.get("/api/index/progress-stream")
    assert '"run_id": 41' in res.text


async def test_a10_11_shutdown_stops_a_running_index_instead_of_waiting():
    from app.api import indexing as api

    _, progress = api.ensure_indexing()
    progress.is_cancelled = False
    progress.status = "running"

    async def run():
        while not progress.is_cancelled:  # a pipeline observes the flag per file
            await asyncio.sleep(0.01)

    task = asyncio.create_task(run())
    api._index_task = task
    try:
        await asyncio.wait_for(api.stop_indexing_for_shutdown(timeout=5), 3)
        assert task.done()
        assert progress.status == "cancelling"
    finally:
        task.cancel()
        api._index_task = None
        progress.status = "idle"
        progress.is_cancelled = False


async def test_a10_11_index_start_is_a_bg_task_not_request_bound_work(
    client, tmp_path, monkeypatch
):
    from app import state
    from app.api import indexing as api

    release = asyncio.Event()

    async def slow(self, folders):
        await release.wait()

    monkeypatch.setattr(idx.IndexingService, "index_folders", slow)
    d = tmp_path / "d"
    d.mkdir()
    res = await client.post("/api/index/start", json={"folders": [str(d)]})
    assert res.status_code == 200
    task = api._index_task
    assert task is not None
    try:
        assert not task.done()
        assert task in state.bg_tasks
    finally:
        release.set()
        await task


async def test_connect_failure_closes_every_opened_connection(tmp_path, monkeypatch):
    import aiosqlite

    opened: list = []
    real_connect = aiosqlite.connect

    async def tracking(*a, **k):
        conn = await real_connect(*a, **k)
        opened.append(conn)
        return conn

    db = DatabaseManager(str(tmp_path / "leak.db"), pool_size=3)
    real_cfg = db._configure_conn
    calls: list[bool] = []

    async def failing(conn, is_write_conn=False):
        calls.append(is_write_conn)
        if len(calls) == 3:  # write conn and first read conn fine, second read fails
            raise RuntimeError("pragma failed")
        await real_cfg(conn, is_write_conn=is_write_conn)

    monkeypatch.setattr(aiosqlite, "connect", tracking)
    monkeypatch.setattr(db, "_configure_conn", failing)
    with pytest.raises(RuntimeError, match="pragma failed"):
        await db.connect()
    assert len(opened) == 3
    # aiosqlite stops its worker thread on close(); a leaked one stays alive.
    for c in opened:
        c._thread.join(2)
    assert all(not c._thread.is_alive() for c in opened)
    assert db._write_conn is None and db._read_conns == [] and not db._pool_initialized


async def test_a1_04_permission_error_keeps_the_file_indexed(tmp_path, monkeypatch):
    import os

    root = tmp_path / "docs"
    root.mkdir()
    locked = _doc(root, "locked.txt", "zebrafish")
    _doc(root, "kept.txt", "otter")
    db, _lance, service = await _env(tmp_path)
    try:
        await _run(service, [root])
        locked.unlink()  # not scanned any more; the OS will say "denied", not "missing"
        real_stat = os.stat

        def denying(path, *a, **k):
            if str(path).endswith("locked.txt"):
                raise PermissionError(13, "denied")
            return real_stat(path, *a, **k)

        monkeypatch.setattr(os, "stat", denying)
        await _run(service, [root])
        monkeypatch.undo()
        assert await _names(db) == ["kept.txt", "locked.txt"]
    finally:
        await db.close()


async def test_a1_04_root_that_vanishes_during_the_run_removes_nothing(tmp_path, monkeypatch):
    import os

    root = tmp_path / "drive"
    root.mkdir()
    gone = _doc(root, "gone.txt", "zebrafish")
    _doc(root, "kept.txt", "otter")
    db, _lance, service = await _env(tmp_path)
    try:
        await _run(service, [root])
        gone.unlink()
        real_stat = os.stat
        fired: list[int] = []

        def unplug(path, *a, **k):
            if str(path).endswith("gone.txt") and not fired:
                fired.append(1)
                root.rename(tmp_path / "drive_unplugged")  # the drive drops mid-loop
            return real_stat(path, *a, **k)

        monkeypatch.setattr(os, "stat", unplug)
        await _run(service, [root])
        monkeypatch.undo()
        assert fired
        assert await _names(db) == ["gone.txt", "kept.txt"]
    finally:
        await db.close()
