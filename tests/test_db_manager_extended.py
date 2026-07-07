from pathlib import Path

import pytest

from app.storage.db import DatabaseManager


@pytest.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    mgr = DatabaseManager(str(db_path))
    await mgr.init_db(schema_path="app/storage/schema.sql")
    yield mgr
    await mgr.close()


def _file_data(path: Path, folder_tag: str = "Test"):
    return {
        "path": str(path),
        "size": 10,
        "modified_at": "2026-03-03T00:00:00",
        "type": path.suffix.lower() or ".txt",
        "folder_tag": folder_tag,
        "summary": "summary",
    }


@pytest.mark.asyncio
async def test_file_and_chunk_crud_and_counts(db: DatabaseManager, tmp_path: Path):
    p1 = tmp_path / "a.py"
    p1.write_text("print('a')", encoding="utf-8")

    file_id = await db.insert_file(_file_data(p1, "A"))
    assert file_id > 0

    single_chunk_id = await db.insert_chunk(
        {"file_id": file_id, "start_offset": 0, "end_offset": 5, "text_preview": "hello"}
    )
    assert single_chunk_id > 0

    bulk_ids = await db.insert_chunks_bulk(
        [
            {"file_id": file_id, "start_offset": 6, "end_offset": 10, "text_preview": "world"},
            {"file_id": file_id, "start_offset": 11, "end_offset": 15, "text_preview": "again"},
        ]
    )
    assert len(bulk_ids) == 2

    chunks = await db.get_file_chunks(file_id)
    assert len(chunks) == 3

    files_count, chunks_count = await db.get_counts()
    assert files_count == 1
    assert chunks_count == 3

    found = await db.get_file_by_path(str(p1))
    assert found is not None

    await db.delete_file_chunks(file_id)
    chunks_after = await db.get_file_chunks(file_id)
    assert chunks_after == []


@pytest.mark.asyncio
async def test_usage_filters_stats_and_modified_map(db: DatabaseManager, tmp_path: Path):
    p1 = tmp_path / "one.py"
    p2 = tmp_path / "two.md"
    p1.write_text("x", encoding="utf-8")
    p2.write_text("y", encoding="utf-8")

    await db.insert_file(_file_data(p1, "Alpha"))
    await db.insert_file(_file_data(p2, "Beta"))

    await db.increment_usage_count(str(p1))
    await db.batch_increment_usage([str(p1), str(p2)])

    all_files = await db.get_all_files()
    assert len(all_files) == 2

    only_py = await db.get_files_by_filter(file_type=".py")
    assert len(only_py) == 1
    assert only_py[0]["path"] == str(p1)

    only_alpha = await db.get_files_by_filter(folder_tag="Alpha")
    assert len(only_alpha) == 1

    stats = await db.get_file_stats_summary()
    assert stats["total_files"] == 2
    assert any(item["ext"] == ".py" for item in stats["by_type"])

    modified = await db.get_files_modified_map([str(p1), str(p2), str(tmp_path / "none.txt")])
    assert str(p1) in modified
    # L-06: Strengthened assertions to ensure we return timestamps, not numeric IDs
    assert isinstance(modified[str(p1)], str)
    assert "T" in modified[str(p1)]  # basic ISO 8601 check
    assert str(p2) in modified


@pytest.mark.asyncio
async def test_query_history_profiles(db: DatabaseManager, tmp_path: Path):
    qid = await db.save_query("q", "a", 2, 12.3)
    assert qid > 0
    history = await db.get_query_history(limit=5)
    assert history
    assert history[0]["question"] == "q"

    profile = {
        "folder_path": str(tmp_path / "proj"),
        "folder_tag": "Proj",
        "profile_text": "A python project",
        "project_type": "Python",
        "file_count": 3,
        "total_size_bytes": 500,
        "top_extensions": ".py (3)",
        "key_files": "pyproject.toml",
    }
    await db.upsert_folder_profile(profile)
    profiles = await db.get_all_folder_profiles()
    assert len(profiles) == 1
    text = await db.get_folder_profiles_text()
    assert "Indexed Project/Folder Profiles" in text
    assert "Proj" in text


@pytest.mark.asyncio
async def test_cleanup_delete_prefix_clear_all_and_health(db: DatabaseManager, tmp_path: Path):
    existing = tmp_path / "keep.txt"
    existing.write_text("keep", encoding="utf-8")
    stale = tmp_path / "missing.txt"
    prefixed = tmp_path / "root" / "child.txt"
    prefixed.parent.mkdir(parents=True, exist_ok=True)
    prefixed.write_text("x", encoding="utf-8")

    await db.insert_file(_file_data(existing, "X"))
    await db.insert_file(_file_data(stale, "X"))
    await db.insert_file(_file_data(prefixed, "Y"))

    cleaned = await db.cleanup_stale_files()
    assert str(stale) in cleaned

    await db.delete_files_by_folder_prefix(str(tmp_path / "root"))
    left = await db.get_all_files()
    assert all(not row["path"].startswith(str(tmp_path / "root")) for row in left)

    assert await db.is_healthy()

    cleared = await db.clear_all()
    assert "files_removed" in cleared
    assert "chunks_removed" in cleared

    files_count, chunks_count = await db.get_counts()
    assert files_count == 0
    assert chunks_count == 0

    await db.close()
    assert await db.is_healthy() is False


@pytest.mark.asyncio
async def test_cascading_deletes(db: DatabaseManager, tmp_path: Path):
    # 1. Insert a file that will become stale
    p = tmp_path / "stale_cascade.py"
    # Do not write the file, so it is stale/missing immediately
    file_id = await db.insert_file(_file_data(p, "CascadeTest"))
    assert file_id > 0

    # 2. Insert a chunk
    chunk_id = await db.insert_chunk(
        {"file_id": file_id, "start_offset": 0, "end_offset": 10, "text_preview": "cascade chunk"}
    )
    assert chunk_id > 0

    # 3. Insert chunk embedding
    await db.insert_chunk_embeddings_bulk([(chunk_id, b"dummy_embedding")])

    # 4. Insert kg_nodes connected to the chunk
    await db.insert_kg_nodes_bulk(
        [
            ("node1", "class", "NodeOne", "{}", chunk_id),
            ("node2", "function", "NodeTwo", "{}", chunk_id),
        ]
    )

    # 5. Insert kg_edge connecting the nodes
    await db.insert_kg_edges_bulk([("node1", "node2", "calls", 1.0, "{}")])

    # Verify everything exists initially
    async with db._get_read_conn() as conn:
        async with conn.execute("SELECT COUNT(*) FROM files WHERE id = ?", (file_id,)) as cur:
            assert (await cur.fetchone())[0] == 1
        async with conn.execute("SELECT COUNT(*) FROM chunks WHERE id = ?", (chunk_id,)) as cur:
            assert (await cur.fetchone())[0] == 1
        async with conn.execute(
            "SELECT COUNT(*) FROM chunk_embeddings WHERE chunk_id = ?", (chunk_id,)
        ) as cur:
            assert (await cur.fetchone())[0] == 1
        async with conn.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE chunk_id = ?", (chunk_id,)
        ) as cur:
            assert (await cur.fetchone())[0] == 2
        async with conn.execute("SELECT COUNT(*) FROM kg_edges WHERE source = 'node1'") as cur:
            assert (await cur.fetchone())[0] == 1

    # 6. Run cleanup_stale_files which should delete the stale file, cascading all the way down
    cleaned = await db.cleanup_stale_files()
    assert str(p) in cleaned

    # 7. Verify all related records are cascadingly deleted
    async with db._get_read_conn() as conn:
        async with conn.execute("SELECT COUNT(*) FROM files WHERE id = ?", (file_id,)) as cur:
            assert (await cur.fetchone())[0] == 0
        async with conn.execute("SELECT COUNT(*) FROM chunks WHERE id = ?", (chunk_id,)) as cur:
            assert (await cur.fetchone())[0] == 0
        async with conn.execute(
            "SELECT COUNT(*) FROM chunk_embeddings WHERE chunk_id = ?", (chunk_id,)
        ) as cur:
            assert (await cur.fetchone())[0] == 0
        async with conn.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE chunk_id = ?", (chunk_id,)
        ) as cur:
            assert (await cur.fetchone())[0] == 0
        async with conn.execute("SELECT COUNT(*) FROM kg_edges WHERE source = 'node1'") as cur:
            assert (await cur.fetchone())[0] == 0
