import asyncio
import contextlib
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.indexing.service import IndexingService
from app.main import app
from app.search import context_builder, retrieval
from app.search.llm_client import LLMClient
from app.storage.db import DatabaseManager

# --- Mocks ---


class MockEmbeddingService:
    def __init__(self):
        self._is_ready = True
        self._query_cache = {}

    @property
    def is_ready(self):
        return self._is_ready

    async def embed_query(self, text):
        return [0.1] * 384

    async def embed_texts(self, texts, batch_size=None, progress_callback=None):
        if progress_callback:
            progress_callback(1, 1)
        import numpy as np
        return np.array([[0.1] * 384 for _ in texts], dtype=np.float32)

    def load_model_background(self):
        pass


class MockLanceDBClient:
    async def semantic_search(self, embedding, k, where_filter=None):
        return {
            "ids": [["1"]],
            "distances": [[0.1]],
            "metadatas": [[{"file_path": "a.py", "text": "hello"}]],
        }

    async def search_summaries(self, embedding, k):
        return {
            "ids": [["s1"]],
            "distances": [[0.1]],
            "metadatas": [[{"file_path": "a.py", "folder_tag": "t"}]],
        }

    async def add_documents(self, ids, embeddings, metadatas):
        pass

    async def add_summaries_batch(self, items):
        pass

    async def delete_documents(self, ids):
        pass

    async def delete_folder_data(self, tag):
        pass

    def connect(self):
        pass

    async def add_query_cache(self, *a, **k):
        pass

    async def search_cache(self, emb, k=1):
        return {"ids": [[]], "distances": [[]], "metadatas": [[]]}


@pytest.fixture
async def real_db(tmp_path):
    db_path = tmp_path / f"test_pma_{os.getpid()}.db"
    db = DatabaseManager(str(db_path))
    await db.init_db()
    yield db
    await db.close()
    for suffix in ["", "-shm", "-wal", "-journal"]:
        p = Path(str(db_path) + suffix)
        if p.exists():
            with contextlib.suppress(BaseException):
                p.unlink()


@pytest.mark.asyncio
async def test_db_logic_coverage(real_db):
    await real_db.execute_write(
        "INSERT INTO files (path, type, size, modified_at) VALUES (?,?,?,?)",
        ("a.txt", ".txt", 100, "now"),
    )
    counts = await real_db.get_counts()
    assert counts[0] >= 1
    await real_db.save_query("q", "a", "c", 100)
    history = await real_db.get_query_history(limit=1)
    assert len(history) > 0
    await real_db.clear_query_history()
    await real_db.batch_increment_usage(["/p"])
    stats = await real_db.get_file_stats_summary()
    assert stats["total_files"] >= 1


@pytest.mark.asyncio
async def test_indexing_pipeline_deep(real_db, tmp_path):
    svc = IndexingService(real_db, MockEmbeddingService(), MockLanceDBClient())
    test_file = tmp_path / "test_index_file.txt"
    test_file.write_text("Hello world content.")
    try:
        q = asyncio.Queue()
        await svc._stream_extract_and_prepare(test_file, "tag", None, q)

        header = await q.get()
        assert header["type"] == "header"

        chunk = await q.get()
        assert chunk["type"] == "chunk"
        assert "Hello" in chunk["chunk"]["text_preview"]

        footer = await q.get()
        assert footer["type"] == "footer"

        # Verification of DB record creation
        await svc._batch_index_pipeline([(test_file, "tag")])
        change_map = await real_db.get_files_change_map([str(test_file.absolute())])
        assert str(test_file.absolute()) in change_map
    finally:
        if test_file.exists():
            test_file.unlink()


@pytest.mark.asyncio
async def test_indexing_folder_walking(real_db, tmp_path):
    svc = IndexingService(real_db, MockEmbeddingService(), MockLanceDBClient())
    test_file = tmp_path / "test_walk.txt"
    test_file.write_text("dummy")
    try:
        mock_entry = MagicMock()
        mock_entry.is_file.return_value = True
        mock_entry.is_dir.return_value = False
        mock_entry.name = "test_walk.txt"
        mock_entry.path = str(test_file)

        mock_it = MagicMock()
        mock_it.__enter__.return_value = [mock_entry]
        mock_it.__exit__.return_value = None

        with (
            patch("os.scandir", return_value=mock_it),
            patch("app.indexing.service._resolve_folder_overlaps", return_value=[tmp_path]),
            patch(
                "app.indexing.service.IndexingService._batch_index_pipeline", AsyncMock()
            ) as mock_proc,
        ):
            await svc.index_folders([str(tmp_path)])
            assert mock_proc.called
    finally:
        if test_file.exists():
            test_file.unlink()


@pytest.fixture
def client(real_db):
    from app.api.deps import get_emb, get_lancedb, get_llm
    from app.main import get_db

    app.dependency_overrides[get_db] = lambda: real_db
    app.dependency_overrides[get_emb] = lambda: MockEmbeddingService()
    app.dependency_overrides[get_lancedb] = lambda: MockLanceDBClient()

    mock_llm = MagicMock()
    mock_llm.generate_response = AsyncMock(return_value="Mock")
    app.dependency_overrides[get_llm] = lambda: mock_llm

    # Store and clear router events to prevent executing real initialization logic
    old_startup = app.router.on_startup.copy()
    old_shutdown = app.router.on_shutdown.copy()
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    with TestClient(app) as c:
        yield c

    app.router.on_startup = old_startup
    app.router.on_shutdown = old_shutdown
    app.dependency_overrides.clear()


def test_main_endpoints_high_coverage(client):
    client.get("/api/health")
    client.get("/api/system/metrics")
    client.get("/api/insights")
    client.post("/api/query", json={"question": "test"})
    client.post("/api/system/compact-db")
    client.get("/api/system/info")


@pytest.mark.asyncio
async def test_full_rag_logic(tmp_path):
    db_path = tmp_path / f"test_rag_{os.getpid()}.db"
    db = DatabaseManager(str(db_path))
    await db.init_db()
    # Correct column name is 'text_preview'
    await db.execute_write(
        "INSERT INTO files (path, type, size, modified_at) VALUES (?,?,?,?)",
        ("a.py", ".py", 100, "now"),
    )
    long_text = (
        b"This is a reasonably long chunk of text that exceeds the fifty character "
        b"minimum requirement for the hybrid retriever to consider it valid."
    )
    await db.execute_write(
        "INSERT INTO chunks (file_id, text_preview, start_offset, end_offset) VALUES (?,?,?,?)",
        (1, long_text, 0, 100),
    )
    llm = MagicMock()
    llm.generate_answer = AsyncMock(return_value="Ans")
    try:
        from app.search.planner import QueryPlanner

        with (
            patch(
                "app.search.retrieval._fts_search",
                AsyncMock(return_value=[{"id": "1", "score": 1.0}]),
            ),
            patch(
                "app.search.retrieval._semantic_search_with_emb",
                AsyncMock(return_value=[{"id": "1", "score": 1.0}]),
            ),
            patch("app.search.retrieval._summary_search_with_emb", AsyncMock(return_value=["tag"])),
        ):
            res = await retrieval.full_rag(
                "query", db, MockEmbeddingService(), MockLanceDBClient(), llm, QueryPlanner()
            )
            assert res["answer"] == "Ans"
    finally:
        await db.close()
        for suffix in ["", "-shm", "-wal", "-journal"]:
            p = Path(str(db_path) + suffix)
            if p.exists():
                with contextlib.suppress(BaseException):
                    p.unlink()


def test_context_builder_edge_cases():
    stats = {"total_files": 0, "total_size_mb": 0, "by_type": [], "by_folder": []}
    res, _ = context_builder.build_context([], 100, stats, "")
    # Check if we didn't crash and got the stats header at least
    assert "File Statistics" in res


@pytest.mark.asyncio
async def test_llm_client_retry_logic():
    client = LLMClient()
    client.api_key = None
    client._check_ollama_health = AsyncMock(return_value=False)
    client._check_lm_studio_health = AsyncMock(return_value=False)
    ans = await client.generate_answer("q", "c")
    assert "LLM unavailable" in ans
