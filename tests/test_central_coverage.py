import asyncio
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from app.search import context_builder
from app.search import reranker
from app.search import retrieval
from app.search.llm_client import LLMClient
from app.indexing.service import IndexingService, progress as indexing_progress
from app.insights.service import InsightsService
from app.embeddings.service import EmbeddingService
from app.storage.db import DatabaseManager

# --- Mocks ---

class MockEmbeddingService:
    def __init__(self):
        self._is_ready = True
        self._query_cache = {}
    @property
    def is_ready(self): return self._is_ready
    async def embed_query(self, text): return [0.1] * 384
    async def embed_texts(self, texts, batch_size=None):
        return [[0.1] * 384 for _ in texts]
    def load_model_background(self): pass

class MockChromaClient:
    async def semantic_search(self, embedding, k, where_filter=None):
        return {"ids": [["1"]], "distances": [[0.1]], "metadatas": [[{"file_path": "a.py", "text": "hello"}]]}
    async def search_summaries(self, embedding, k):
        return {"ids": [["s1"]], "distances": [[0.1]], "metadatas": [[{"file_path": "a.py", "folder_tag": "t"}]]}
    async def add_documents(self, ids, embeddings, metadatas): pass
    async def add_summaries_batch(self, items): pass
    async def delete_documents(self, ids): pass
    async def delete_folder_data(self, tag): pass
    def connect(self): pass

@pytest.fixture
async def real_db():
    db_path = f"test_pma_{os.getpid()}.db"
    db = DatabaseManager(db_path)
    await db.init_db()
    yield db
    await db.close()
    if os.path.exists(db_path):
        try: os.remove(db_path)
        except: pass

@pytest.mark.asyncio
async def test_db_logic_coverage(real_db):
    await real_db.execute_write("INSERT INTO files (path, type, size, modified_at) VALUES (?,?,?,?)", ("a.txt", ".txt", 100, "now"))
    counts = await real_db.get_counts()
    assert counts[0] >= 1
    await real_db.upsert_unreal_project_facts({
        "folder_path": "/p", "folder_tag": "t", "project_name": "N", "engine_version": "5",
        "total_assets": 1, "map_count": 1, "character_blueprints": 1, "pawn_blueprints": 1,
        "skeletal_meshes": 1, "material_count": 1, "niagara_systems": 1, "environment_assets": 1,
        "metadata_source": "S", "profile_text": "P"
    })
    facts = await real_db.get_all_unreal_project_facts()
    assert len(facts) > 0
    await real_db.save_query("q", "a", "c", 100)
    history = await real_db.get_query_history(limit=1)
    assert len(history) > 0
    await real_db.clear_query_history()
    await real_db.batch_increment_usage(["/p"])
    stats = await real_db.get_file_stats_summary()
    assert stats["total_files"] >= 1

@pytest.mark.asyncio
async def test_indexing_pipeline_deep(real_db):
    svc = IndexingService(real_db, MockEmbeddingService(), MockChromaClient())
    test_file = Path("test_index_file.txt")
    test_file.write_text("Hello world content.")
    try:
        loop = asyncio.get_running_loop()
        res = await svc._process_file_change(test_file, "tag", "now", 100, {}, loop)
        assert res == "new"
        with patch("app.indexing.service.IndexingService._extract_and_prepare", return_value={"path": test_file, "folder_tag": "tag", "file_data": {"path": str(test_file.absolute()), "size": 100, "modified_at": "now", "type": ".txt", "folder_tag": "tag", "summary": "", "sha256": ""}, "chunks": []}):
            await svc._batch_index_pipeline([(test_file, "tag")])
        change_map = await real_db.get_files_change_map([str(test_file.absolute())])
        assert str(test_file.absolute()) in change_map
    finally:
        if test_file.exists(): test_file.unlink()

@pytest.mark.asyncio
async def test_indexing_folder_walking(real_db):
    svc = IndexingService(real_db, MockEmbeddingService(), MockChromaClient())
    mock_entry = MagicMock()
    mock_entry.is_file.return_value = True
    mock_entry.is_dir.return_value = False
    mock_entry.name = "test.txt"
    mock_entry.path = "test.txt"
    mock_entry.stat.return_value.st_size = 100
    mock_entry.stat.return_value.st_mtime = 123456789
    
    with patch("os.scandir", return_value=[mock_entry]), \
         patch("app.indexing.service._resolve_folder_overlaps", return_value=[Path(".")]), \
         patch("app.indexing.service.IndexingService._batch_index_pipeline", AsyncMock()) as mock_proc:
        await svc.index_folders(["."])
        assert mock_proc.called

from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client(real_db):
    from app.main import get_db
    app.dependency_overrides[get_db] = lambda: real_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

def test_main_endpoints_high_coverage(client):
    client.get("/api/health")
    client.get("/api/system/metrics")
    client.get("/api/insights")
    client.post("/api/query", json={"question": "test"})
    client.post("/api/system/compact-db")
    client.get("/api/system/info")

@pytest.mark.asyncio
async def test_full_rag_logic():
    db_path = f"test_rag_{os.getpid()}.db"
    db = DatabaseManager(db_path)
    await db.init_db()
    # Correct column name is 'text_preview'
    await db.execute_write("INSERT INTO files (path, type, size, modified_at) VALUES (?,?,?,?)", ("a.py", ".py", 100, "now"))
    long_text = b"This is a reasonably long chunk of text that exceeds the fifty character minimum requirement for the hybrid retriever to consider it valid."
    await db.execute_write("INSERT INTO chunks (file_id, text_preview, start_offset, end_offset) VALUES (?,?,?,?)", (1, long_text, 0, 100))
    llm = MagicMock()
    llm.generate_answer = AsyncMock(return_value="Ans")
    try:
        with patch("app.search.retrieval._fts_search", AsyncMock(return_value=[{"id": "1", "score": 1.0}])), \
             patch("app.search.retrieval._semantic_search_with_emb", AsyncMock(return_value=[{"id": "1", "score": 1.0}])), \
             patch("app.search.retrieval._summary_search_with_emb", AsyncMock(return_value=["tag"])):
            res = await retrieval.full_rag("query", db, MockEmbeddingService(), MockChromaClient(), llm)
            assert res["answer"] == "Ans"
    finally:
        await db.close()
        if os.path.exists(db_path): os.remove(db_path)

def test_context_builder_edge_cases():
    stats = {"total_files": 0, "total_size_mb": 0, "by_type": [], "by_folder": []}
    res = context_builder.build_context([], 100, stats, "")
    # Check if we didn't crash and got the stats header at least
    assert "File Statistics" in res

@pytest.mark.asyncio
async def test_llm_client_retry_logic():
    client = LLMClient()
    client.api_key = None 
    ans = await client.generate_answer("q", "c")
    assert "LLM unavailable" in ans
