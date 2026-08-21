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


def _mk_embedder_service():
    """IndexingService wired with stubs; _embedder_worker touches only the queues
    and the embedding service."""
    embedding_service = MagicMock(spec=EmbeddingService)
    calls: list[int] = []

    async def _embed_texts(texts, **_kwargs):
        calls.append(len(texts))
        return np.zeros((len(texts), 384), dtype=np.float32)

    embedding_service.embed_texts = _embed_texts

    svc = IndexingService(
        db=MagicMock(), embedding_service=embedding_service, lancedb_client=MagicMock()
    )
    return svc, calls


def _hdr(name):
    return {"type": "header", "path": Path(name), "file_data": {"path": name}}


def _chk(name, i):
    return {
        "type": "chunk",
        "path": Path(name),
        "chunk": {"text_preview": f"{name}#{i}", "start_offset": i, "end_offset": i + 1},
    }


def _ftr(name):
    return {"type": "footer", "path": Path(name), "summary": "s", "sha256": "x"}


async def _drain(store_queue):
    out = []
    while True:
        item = store_queue.get_nowait()
        if item is None:
            return out
        out.append(item)


def _tag(item):
    return (item["type"], item["path"].name, item.get("chunk", {}).get("text_preview"))


@pytest.mark.asyncio
async def test_embedder_worker_batches_across_file_boundaries():
    """Headers and footers must stop cutting the embed batch.

    The old code flushed on every non-chunk item, so an interleaved two-file
    sequence produced one embed_texts call per file. It must now be one call,
    with per-path relative order (header -> chunks -> footer) untouched, because
    _storer_worker silently drops a chunk that arrives after its own footer.
    """
    svc, calls = _mk_embedder_service()

    embed_queue: asyncio.Queue = asyncio.Queue()
    store_queue: asyncio.Queue = asyncio.Queue()

    sequence = [
        _hdr("a.txt"),
        _chk("a.txt", 0),
        _chk("a.txt", 1),
        _hdr("b.txt"),
        _chk("b.txt", 0),
        _ftr("a.txt"),
        _ftr("b.txt"),
    ]
    for item in sequence:
        embed_queue.put_nowait(item)
    embed_queue.put_nowait(None)

    await svc._embedder_worker(embed_queue, store_queue)

    assert calls == [3], f"expected one batch of 3 chunks, got {calls}"

    got = await _drain(store_queue)
    assert [_tag(i) for i in got] == [_tag(i) for i in sequence]
    assert store_queue.empty(), "sentinel must be the last item"

    # Every chunk carries an embedding, and each file's header precedes and
    # footer follows its own chunks.
    for item in got:
        if item["type"] == "chunk":
            assert "_embedding" in item["chunk"]
    for name in ("a.txt", "b.txt"):
        idx = [n for n, i in enumerate(got) if i["path"].name == name]
        types = [got[n]["type"] for n in idx]
        assert types[0] == "header"
        assert types[-1] == "footer"


@pytest.mark.asyncio
async def test_embedder_worker_flushes_at_threshold_and_keeps_order():
    """The buffer is bounded: it flushes once the chunk count hits the threshold
    rather than growing with the queue."""
    svc, calls = _mk_embedder_service()
    svc._embed_flush_threshold = 2

    embed_queue: asyncio.Queue = asyncio.Queue()
    store_queue: asyncio.Queue = asyncio.Queue()

    sequence = [_hdr("a.txt")] + [_chk("a.txt", i) for i in range(5)] + [_ftr("a.txt")]
    for item in sequence:
        embed_queue.put_nowait(item)
    embed_queue.put_nowait(None)

    await svc._embedder_worker(embed_queue, store_queue)

    assert sum(calls) == 5, f"every chunk must be embedded exactly once, got {calls}"
    assert max(calls) <= 2, f"no batch may exceed the threshold, got {calls}"

    got = await _drain(store_queue)
    assert [_tag(i) for i in got] == [_tag(i) for i in sequence]


@pytest.mark.asyncio
async def test_embedder_worker_sends_sentinel_when_embedding_fails():
    """C-03: _storer_worker must still drain if the embedder raises."""
    svc, _calls = _mk_embedder_service()

    async def _boom(texts, **_kwargs):
        raise RuntimeError("onnx exploded")

    svc.embedding_service.embed_texts = _boom

    embed_queue: asyncio.Queue = asyncio.Queue()
    store_queue: asyncio.Queue = asyncio.Queue()
    embed_queue.put_nowait(_hdr("a.txt"))
    embed_queue.put_nowait(_chk("a.txt", 0))
    embed_queue.put_nowait(None)

    with pytest.raises(RuntimeError):
        await svc._embedder_worker(embed_queue, store_queue)

    assert store_queue.get_nowait() is None


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
