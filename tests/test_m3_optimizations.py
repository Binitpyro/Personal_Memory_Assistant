import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.embeddings.service import EmbeddingService
from app.indexing.service import IndexingService
from app.storage.db import DatabaseManager
from app.vector_store.lancedb_client import LanceDBClient


@pytest.fixture
def temp_db_dir():
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def temp_db_path(temp_db_dir):
    return os.path.join(temp_db_dir, "test_metadata.db")


@pytest.mark.asyncio
async def test_sqlite_pragmas_and_wal(temp_db_path):
    db_mgr = DatabaseManager(temp_db_path, pool_size=1)
    await db_mgr.connect()
    conn = db_mgr._get_conn()

    # Verify WAL mode
    async with conn.execute("PRAGMA journal_mode;") as cur:
        row = await cur.fetchone()
        assert row[0].lower() == "wal"

    # Verify PRAGMAs
    async with conn.execute("PRAGMA synchronous;") as cur:
        row = await cur.fetchone()
        assert row[0] == 1  # 1 represents NORMAL in SQLite

    async with conn.execute("PRAGMA temp_store;") as cur:
        row = await cur.fetchone()
        assert row[0] == 2  # 2 represents MEMORY in SQLite

    async with conn.execute("PRAGMA mmap_size;") as cur:
        row = await cur.fetchone()
        assert row[0] == 1073741824  # 1GB

    async with conn.execute("PRAGMA cache_size;") as cur:
        row = await cur.fetchone()
        assert row[0] == -8192  # 8MB

    async with conn.execute("PRAGMA wal_autocheckpoint;") as cur:
        row = await cur.fetchone()
        assert row[0] == 10000

    await db_mgr.close()


@pytest.mark.asyncio
async def test_lancedb_table_caching_and_deferral(temp_db_dir):
    client = LanceDBClient(persist_directory=temp_db_dir)
    client.connect()
    assert client.db is not None

    # Initially cache is empty
    assert len(client._table_cache) == 0

    # Add document to create the table
    embeddings = [np.array([0.1] * 384, dtype=np.float32)]
    metadatas = [{"file_path": "test.txt", "text": "hello"}]
    await client.add_documents(["1"], embeddings, metadatas)

    # Table should be cached now
    assert "pma_chunks" in client._table_cache
    cached_tbl = client._table_cache["pma_chunks"]

    # Mock open_table and list_tables to count calls
    with patch.object(client.db, "open_table", wraps=client.db.open_table) as mock_open:
        # Subsequent gets should fetch from cache and NOT call open_table
        tbl1 = client._get_table("pma_chunks")
        tbl2 = client._get_table("pma_chunks")
        assert tbl1 is cached_tbl
        assert tbl2 is cached_tbl
        mock_open.assert_not_called()

    # Clear all should clear cache
    await client.clear_all()
    assert len(client._table_cache) == 0


@pytest.mark.asyncio
async def test_storer_worker_commit_once_per_file(temp_db_path, temp_db_dir):
    db_mgr = DatabaseManager(temp_db_path, pool_size=1)
    await db_mgr.connect()
    await db_mgr.init_db()

    lancedb_client = LanceDBClient(persist_directory=temp_db_dir)
    embedding_service = MagicMock(spec=EmbeddingService)

    indexing_service = IndexingService(
        db=db_mgr, embedding_service=embedding_service, lancedb_client=lancedb_client
    )

    # Create mock items for store queue
    store_queue = asyncio.Queue()
    path = Path("d:/projects/Personal_Memory_Assistant/test.txt")

    header_item = {
        "type": "header",
        "path": path,
        "file_data": {
            "path": str(path.absolute()),
            "size": 100,
            "modified_at": "2026-07-10T01:00:00",
            "type": ".txt",
            "folder_tag": "test_tag",
            "sha256": "dummy_sha",
        },
    }

    chunk_item = {
        "type": "chunk",
        "path": path,
        "chunk": {
            "text_preview": "some chunk text",
            "start_offset": 0,
            "end_offset": 15,
            "_embedding": [0.1] * 384,
        },
    }

    footer_item = {"type": "footer", "path": path, "summary": "file summary"}

    # Queue up items for one file
    await store_queue.put(header_item)
    await store_queue.put(chunk_item)
    await store_queue.put(footer_item)
    await store_queue.put(None)  # sentinel

    # Spy on DatabaseManager transactions
    begin_spy = MagicMock(wraps=db_mgr.begin_transaction)
    commit_spy = MagicMock(wraps=db_mgr.commit)

    db_mgr.begin_transaction = begin_spy
    db_mgr.commit = commit_spy

    # Run the storer worker
    await indexing_service._storer_worker(store_queue)

    # Verify transaction calls: exactly 1 begin and 1 commit for the file
    assert begin_spy.call_count == 1
    assert commit_spy.call_count == 1

    await db_mgr.close()
