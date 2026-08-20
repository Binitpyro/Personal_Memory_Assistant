import zlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.indexing.service as service_module
from app.indexing.service import IndexingService


@pytest.mark.asyncio
async def test_db_manager_coverage_booster(mock_db):
    db = mock_db

    # 1. Test get_existing_file_ids / get_files_modified_map / get_files_sha256_map / get_files_change_map on empty lists
    assert await db.get_existing_file_ids([]) == {}
    assert await db.get_files_modified_map([]) == {}
    assert await db.get_files_sha256_map([]) == {}
    assert await db.get_files_change_map([]) == {}

    # Insert a dummy file
    await db.execute_write(
        "INSERT INTO files (path, size, modified_at, sha256, folder_tag, usage_count, type) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("C:/file1.py", 100, "2026-07-05T20:00:00Z", "hash123", "FolderA", 1, ".py"),
    )

    # Fetch file id
    res = await db.get_file_by_path("C:/file1.py")
    assert res is not None
    file_id = res["id"]

    # Test mappings with data
    assert await db.get_existing_file_ids(["C:/file1.py", "C:/nonexistent.py"]) == {
        "C:/file1.py": file_id
    }
    assert await db.get_files_modified_map(["C:/file1.py"]) == {
        "C:/file1.py": "2026-07-05T20:00:00Z"
    }
    assert await db.get_files_sha256_map(["C:/file1.py"]) == {"C:/file1.py": "hash123"}
    assert await db.get_files_change_map(["C:/file1.py"]) == {
        "C:/file1.py": ("2026-07-05T20:00:00Z", "hash123")
    }

    # 2. Test get_chunk_embeddings on empty and filled data
    assert await db.get_chunk_embeddings([]) == {}

    # Insert chunks (text_preview needs to be compressed since get_file_chunks decompresses it)
    compressed_text = zlib.compress(b"def my_chunk(): pass")
    await db.execute_write(
        "INSERT INTO chunks (id, file_id, start_offset, end_offset, text_preview) VALUES (?, ?, ?, ?, ?)",
        (10, file_id, 0, 20, compressed_text),
    )
    await db.execute_write(
        "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
        (10, b"fake_embedding_bytes"),
    )

    embeddings_map = await db.get_chunk_embeddings([10])
    assert embeddings_map == {10: b"fake_embedding_bytes"}

    # 3. Test get_all_chunk_data_for_sync
    sync_data = await db.get_all_chunk_data_for_sync(limit=10, last_id=0)
    assert len(sync_data) == 1
    assert sync_data[0]["chunk_id"] == "10"
    assert sync_data[0]["file_path"] == "C:/file1.py"
    assert sync_data[0]["folder_tag"] == "FolderA"
    assert sync_data[0]["embedding"] == b"fake_embedding_bytes"

    # 4. Test get_file_chunks and delete_file_chunks
    chunks = await db.get_file_chunks(file_id)
    assert len(chunks) == 1
    assert chunks[0]["text_preview"] == "def my_chunk(): pass"

    await db.delete_file_chunks(file_id, auto_commit=True)
    assert len(await db.get_file_chunks(file_id)) == 0

    # Re-insert chunk for remaining tests
    await db.execute_write(
        "INSERT INTO chunks (id, file_id, start_offset, end_offset, text_preview) VALUES (?, ?, ?, ?, ?)",
        (10, file_id, 0, 20, compressed_text),
    )

    # 5. Test BFS and knowledge graph queries
    assert await db.bfs_from_chunks([]) == []
    assert await db.get_relational_paths([]) == []

    # Insert kg_nodes and kg_edges
    # properties contains chunk_id
    await db.execute_write(
        "INSERT INTO kg_nodes (id, label, type, properties) VALUES (?, ?, ?, ?)",
        ("node1", "Class", "type_foo", '{"chunk_id": 10}'),
    )
    await db.execute_write(
        "INSERT INTO kg_nodes (id, label, type, properties) VALUES (?, ?, ?, ?)",
        ("node2", "Method", "type_bar", '{"chunk_id": 11}'),
    )
    await db.execute_write(
        "INSERT INTO kg_edges (source, target, relation) VALUES (?, ?, ?)",
        ("node1", "node2", "defines"),
    )

    bfs_res = await db.bfs_from_chunks([10], max_depth=1, limit=5)
    assert 10 in bfs_res

    paths_res = await db.get_relational_paths([10], max_depth=1, limit=5)
    assert len(paths_res) > 0
    assert "node1" in paths_res[0]

    # 6. Test increment usage count and batch increment usage
    await db.increment_usage_count("C:/file1.py")
    res = await db.get_file_by_path("C:/file1.py")
    assert res["usage_count"] == 2

    await db.batch_increment_usage([])  # Empty edge case
    await db.batch_increment_usage(["C:/file1.py", "C:/file1.py"])
    res = await db.get_file_by_path("C:/file1.py")
    assert res["usage_count"] == 4

    # 7. Test stream_all_nodes
    # Insert folder profile first
    await db.execute_write(
        "INSERT INTO folder_profiles (folder_path, folder_tag, project_type, file_count, total_size_bytes) VALUES (?, ?, ?, ?, ?)",
        ("C:/FolderA", "FolderA", "Python", 1, 100),
    )
    nodes = []
    async for node in db.stream_all_nodes():
        nodes.append(node)

    assert len(nodes) == 2
    assert any(n["is_folder"] and n["path"] == "C:/FolderA" for n in nodes)
    assert any(not n["is_folder"] and n["path"] == "C:/file1.py" for n in nodes)

    # 8. Test get_file_stats_summary
    stats = await db.get_file_stats_summary()
    assert stats is not None


def test_indexing_service_extract_plain_text_stream(tmp_path):
    service = IndexingService(
        db=MagicMock(), embedding_service=MagicMock(), lancedb_client=MagicMock()
    )

    # Test valid file
    test_file = tmp_path / "test.txt"
    test_content = "hello world " * 100
    test_file.write_text(test_content, encoding="utf-8")

    chunks = list(service._extract_plain_text_stream(test_file))
    assert len(chunks) == 1
    assert chunks[0] == test_content

    # Test non-existent file (exception branch)
    non_existent = tmp_path / "nonexistent.txt"
    chunks_err = list(service._extract_plain_text_stream(non_existent))
    assert len(chunks_err) == 0


@pytest.mark.asyncio
async def test_scan_all_folders_rust_mocked(monkeypatch):
    # Mock rust_core if not available
    mock_rust = MagicMock()
    mock_rust.scan_folders.return_value = ["C:/FolderA/file.py"]

    monkeypatch.setattr(service_module, "RUST_CORE_AVAILABLE", True)
    monkeypatch.setattr(service_module, "rust_core", mock_rust, raising=False)

    service = IndexingService(
        db=MagicMock(), embedding_service=MagicMock(), lancedb_client=MagicMock()
    )
    service.supported_extensions = {".py"}

    # Test scan success path
    files, method, _dur = service._scan_all_folders([Path("C:/FolderA")])
    assert len(files) == 1
    # The third element is the indexed root the file was attributed to; it is
    # what /api/files/tree groups by and what the Explorer strips off.
    assert files[0] == (Path("C:/FolderA/file.py"), "FolderA", str(Path("C:/FolderA").resolve()))
    assert method == "rust_jwalk"

    # Test exception fallback path
    mock_rust.scan_folders.side_effect = Exception("Rust scan failed")
    _files_fallback, method_fallback, _ = service._scan_all_folders([Path("C:/FolderA")])
    assert method_fallback == "scandir"  # fell back to python


def test_indexing_service_is_binary():
    service = IndexingService(
        db=MagicMock(), embedding_service=MagicMock(), lancedb_client=MagicMock()
    )

    # Exception path
    mock_path = MagicMock()
    from unittest.mock import patch

    with patch("builtins.open", side_effect=Exception("open error")):
        assert service._is_binary(mock_path) is True
