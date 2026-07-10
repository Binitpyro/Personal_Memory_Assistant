import json

import pytest

from app.storage.db import DatabaseManager


@pytest.fixture
async def db():
    mgr = DatabaseManager(":memory:")
    await mgr.connect()
    await mgr.init_db()
    yield mgr
    await mgr.close()


@pytest.mark.asyncio
async def test_db_vacuum_and_checkpoint(db: DatabaseManager):
    # Test vacuum, incremental_vacuum, wal_checkpoint
    await db.vacuum()
    await db.incremental_vacuum(pages=10)
    await db.wal_checkpoint()
    # Check that health is okay
    assert await db.is_healthy() is True


@pytest.mark.asyncio
async def test_db_batch_operations_and_maps(db: DatabaseManager):
    # batch_insert_files
    files = [
        {
            "path": "a.txt",
            "size": 10,
            "modified_at": "t1",
            "type": ".txt",
            "folder_tag": "T",
            "sha256": "sha1",
        },
        {
            "path": "b.txt",
            "size": 20,
            "modified_at": "t2",
            "type": ".txt",
            "folder_tag": "T",
            "sha256": "sha256_b",
        },
    ]
    ids = await db.batch_insert_files(files)
    assert len(ids) == 2

    # get_existing_file_ids
    existing_map = await db.get_existing_file_ids(["a.txt", "b.txt", "c.txt"])
    assert "a.txt" in existing_map
    assert "b.txt" in existing_map
    assert "c.txt" not in existing_map

    # get_files_modified_map
    mod_map = await db.get_files_modified_map(["a.txt", "b.txt"])
    assert mod_map["a.txt"] == "t1"
    assert mod_map["b.txt"] == "t2"

    # get_files_sha256_map
    sha_map = await db.get_files_sha256_map(["a.txt", "b.txt"])
    assert sha_map["a.txt"] == "sha1"
    assert sha_map["b.txt"] == "sha256_b"

    # get_files_change_map
    change_map = await db.get_files_change_map(["a.txt", "b.txt"])
    assert change_map["a.txt"] == ("t1", "sha1")

    # batch_increment_usage
    await db.batch_increment_usage(["a.txt", "b.txt"])
    file_a = await db.get_file_by_path("a.txt")
    assert file_a[7] == 1  # usage_count is index 7 based on schema order


@pytest.mark.asyncio
async def test_db_graph_rag_flow(db: DatabaseManager):
    # Insert file & chunk to reference in kg_nodes
    file_id = await db.insert_file(
        {"path": "test.py", "size": 100, "modified_at": "now", "type": ".py", "folder_tag": "tag"}
    )
    chunk_id = await db.insert_chunk(
        {"file_id": file_id, "start_offset": 0, "end_offset": 50, "text_preview": "preview"}
    )

    # Empty bulk inserts should do nothing and not fail
    await db.insert_kg_nodes_bulk([])
    await db.insert_kg_edges_bulk([])

    # insert_kg_nodes_bulk
    nodes = [
        (
            "node_1",
            "entity",
            "MainFunc",
            json.dumps({"chunk_id": chunk_id, "desc": "main"}),
            chunk_id,
        ),
        (
            "node_2",
            "entity",
            "HelperFunc",
            json.dumps({"chunk_id": chunk_id, "desc": "helper"}),
            chunk_id,
        ),
    ]
    await db.insert_kg_nodes_bulk(nodes)

    # get_graph_nodes
    graph_nodes = await db.get_graph_nodes(["node_1", "node_2", "node_3"])
    assert len(graph_nodes) == 2
    assert graph_nodes[0]["id"] == "node_1"
    assert graph_nodes[1]["id"] == "node_2"

    # Empty get_graph_nodes
    assert await db.get_graph_nodes([]) == []

    # insert_kg_edges_bulk with a pending edge to node_2
    edges = [
        ("node_1", "PENDING::HelperFunc", "calls", 1.0, json.dumps({"chunk_id": chunk_id})),
    ]
    await db.insert_kg_edges_bulk(edges)

    # resolve_pending_graph_edges
    await db.resolve_pending_graph_edges()

    # bfs_from_chunks
    bfs_results = await db.bfs_from_chunks([chunk_id], max_depth=2, limit=5)
    assert chunk_id in bfs_results

    # empty bfs_from_chunks
    assert await db.bfs_from_chunks([]) == []

    # get_relational_paths
    paths = await db.get_relational_paths([chunk_id], max_depth=2, limit=5)
    assert len(paths) > 0
    # relational path should show connection MainFunc -> HelperFunc
    assert "calls" in paths[0]

    # empty get_relational_paths
    assert await db.get_relational_paths([]) == []

    # get_graph_edges
    graph_edges = await db.get_graph_edges("node_1", max_depth=2)
    assert len(graph_edges) > 0
    assert graph_edges[0]["target"] == "node_2"

    # stream_all_nodes
    nodes_stream = []
    async for node in db.stream_all_nodes():
        nodes_stream.append(node)
    assert len(nodes_stream) == 1


@pytest.mark.asyncio
async def test_db_folder_profiles(db: DatabaseManager):
    # Empty profile text
    assert await db.get_folder_profiles_text() == ""

    # Upsert folder profile
    profile = {
        "folder_path": "/p1",
        "folder_tag": "tag1",
        "profile_text": "desc1",
        "project_type": "python",
        "file_count": 5,
        "total_size_bytes": 1024 * 1024,
        "top_extensions": "py, md",
        "key_files": "main.py",
    }
    await db.upsert_folder_profile(profile)

    # get_all_folder_profiles
    profiles = await db.get_all_folder_profiles()
    assert len(profiles) == 1
    assert profiles[0]["folder_tag"] == "tag1"

    # get_folder_profiles_text
    prof_text = await db.get_folder_profiles_text()
    assert "Indexed Project/Folder Profiles" in prof_text
    assert "tag1" in prof_text


@pytest.mark.asyncio
async def test_db_insert_chunks_bulk_large(db: DatabaseManager):
    file_id = await db.insert_file(
        {"path": "bulk.py", "size": 100, "modified_at": "now", "type": ".py", "folder_tag": "tag"}
    )

    # Generate 22 chunks (threshold is 20)
    chunks = [
        {
            "file_id": file_id,
            "start_offset": i * 10,
            "end_offset": (i + 1) * 10,
            "text_preview": f"preview {i}",
            "sentence_offsets": "[]",
            "segmenter_version": "v1",
        }
        for i in range(22)
    ]
    ids = await db.insert_chunks_bulk(chunks)
    assert len(ids) == 22
