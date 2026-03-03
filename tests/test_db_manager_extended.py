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
    assert str(p2) in modified


@pytest.mark.asyncio
async def test_query_history_profiles_and_unreal_facts(db: DatabaseManager, tmp_path: Path):
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

    facts = {
        "folder_path": str(tmp_path / "uproject"),
        "folder_tag": "Game",
        "project_name": "SpaceGame",
        "engine_version": "5.3",
        "total_assets": 11,
        "map_count": 2,
        "character_blueprints": 1,
        "pawn_blueprints": 1,
        "skeletal_meshes": 2,
        "material_count": 3,
        "niagara_systems": 1,
        "environment_assets": 4,
        "metadata_source": "import",
    }
    await db.upsert_unreal_project_facts(facts)
    all_facts = await db.get_all_unreal_project_facts()
    assert len(all_facts) == 1
    assert all_facts[0]["project_name"] == "SpaceGame"


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
