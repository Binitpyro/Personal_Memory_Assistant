import asyncio
import json
import os
import sys
import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from datetime import datetime

# 1. Setup Environment
os.environ["GEMINI_API_KEY"] = "fake_key"

# MOCK heavy libraries
sys.modules["sentence_transformers"] = MagicMock()
sys.modules["torch"] = MagicMock()
sys.modules["pdfplumber"] = MagicMock()
sys.modules["docx"] = MagicMock()

# Patch app.main globals
with patch("app.main.lifespan", AsyncMock()), \
     patch("app.main._get_embedding_service"), \
     patch("app.main._get_chroma_client"), \
     patch("app.main._get_llm_client"), \
     patch("app.main.DatabaseManager"):
    from app.main import app, get_db

from app.search import context_builder, reranker, retrieval
from app.indexing.service import IndexingService, _resolve_folder_overlaps, _detect_project_type, _build_folder_profile, _dominant_extension_project_type
from app.storage.db import DatabaseManager
from app.embeddings.service import EmbeddingService
from app.insights.service import InsightsService
from app.search.llm_client import LLMClient
from app.utils.metrics import Timer
from app.insights.unreal_import import parse_unreal_metadata
from app.scanner.scanner import scan_folder

# --- FIXTURES ---

@pytest.fixture
async def real_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = DatabaseManager(path)
    await db.init_db()
    yield db
    await db.close()
    try: os.remove(path)
    except: pass

@pytest.fixture
def mock_emb():
    m = MagicMock(spec=EmbeddingService)
    m.is_ready = True
    m.embed_query = AsyncMock(return_value=[0.1]*384)
    m.embed_texts = AsyncMock(return_value=[[0.1]*384])
    return m

@pytest.fixture
def mock_chroma():
    m = MagicMock()
    m.semantic_search = AsyncMock(return_value={
        "ids": [["1"]], 
        "distances": [[0.1]], 
        "metadatas": [[{
            "file_path": "test.py", 
            "text": "hello world content string long enough to pass dedupe",
            "rerank_score": 1.0
        }]]
    })
    m.search_summaries = AsyncMock(return_value={
        "ids": [["s1"]], 
        "metadatas": [[{"file_path": "test.py", "folder_tag": "tag"}]]
    })
    m.add_documents = AsyncMock()
    m.add_summaries_batch = AsyncMock()
    return m

@pytest.fixture
def mock_llm():
    m = MagicMock()
    m.generate_answer = AsyncMock(return_value="Logic verified answer")
    async def fake_stream(*args, **kwargs):
        yield "Part 1"
        yield "Part 2"
    m.stream_answer = fake_stream
    return m

# --- INSIGHTS SERVICE EXHAUSTIVE ---

@pytest.mark.asyncio
async def test_insights_service_deep(real_db):
    # 1. Setup Data
    await real_db.insert_file({"path": "file1.py", "size": 1000, "modified_at": "now", "type": ".py", "folder_tag": "t"})
    await real_db.insert_file({"path": "file2.txt", "size": 500, "modified_at": "now", "type": ".txt", "folder_tag": "t"})
    
    svc = InsightsService(real_db)
    
    # 2. Test get_stats
    stats = await svc.get_stats()
    assert stats["total_size_bytes"] == 1500
    assert stats["file_count"] == 2
    assert stats["database_size_bytes"] > 0
    assert len(stats["top_files"]) == 2
    assert len(stats["cold_files"]) == 2
    assert ".py" in stats["type_breakdown"]
    assert stats["type_breakdown"][".py"]["count"] == 1
    
    # 3. Test get_filtered_files logic
    # Extension formatting branches (lstrip, lower)
    res_py = await svc.get_filtered_files("py")
    assert len(res_py["top_files"]) == 1
    assert res_py["top_files"][0]["path"] == "file1.py"
    
    res_dot_py = await svc.get_filtered_files(".PY")
    assert len(res_dot_py["top_files"]) == 1
    
    # 4. Error Path Coverage
    with patch.object(real_db, "execute_query", side_effect=Exception("Simulated Failure")):
        err_stats = await svc.get_stats()
        assert err_stats["error"] is not None
        
        err_filter = await svc.get_filtered_files(".py")
        assert err_filter["error"] is not None

# --- OTHER LOGIC DEEP DIVE ---

@pytest.mark.asyncio
async def test_all_logic_deep(real_db, mock_emb, mock_chroma, mock_llm):
    # 1. DB
    fid = await real_db.insert_file({"path": "test.py", "size": 1024, "modified_at": "now", "type": ".py", "folder_tag": "tag"})
    await real_db.insert_chunks_bulk([{"file_id": fid, "start_offset": 0, "end_offset": 50, "text_preview": "hello world content string long enough to pass dedupe"}] * 5)
    
    # 2. Retrieval logic
    with patch("app.search.retrieval._fts_search", AsyncMock(return_value=[{"id": "1", "score": 1.0}])), \
         patch("app.search.retrieval.rerank", AsyncMock(side_effect=lambda q, c, **kw: [dict(x, rerank_score=1.0) for x in c])):
        res = await retrieval.full_rag("What is in test.py?", real_db, mock_emb, mock_chroma, mock_llm)
        assert res["answer"] == "Logic verified answer"

def test_unreal_and_metrics():
    with Timer("test"): pass
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as tf:
        json.dump({"ProjectName": "P", "EngineVersion": "5", "AssetStats": {"Total": 1}, "Components": []}, tf)
        tf_path = Path(tf.name)
    try:
        f = parse_unreal_metadata(tf_path, "t")
        assert f["project_name"] == "P"
    finally:
        os.remove(tf_path)

def test_desktop_startup_deep():
    with patch("uvicorn.Server"), patch("webview.create_window"), patch("webview.start"), \
         patch("desktop._wait_for_server", return_value=True), patch("sys.exit"):
        import desktop
        desktop.main()

# --- API ---
from fastapi.testclient import TestClient

def test_api_exhaustive(real_db, mock_emb, mock_chroma):
    app.dependency_overrides[get_db] = lambda: real_db
    from app.main import get_emb, get_chroma, get_llm
    app.dependency_overrides[get_emb] = lambda: mock_emb
    app.dependency_overrides[get_chroma] = lambda: mock_chroma
    app.dependency_overrides[get_llm] = MagicMock()
    
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/system/metrics").status_code == 200
    assert client.get("/api/insights").status_code == 200
    app.dependency_overrides.clear()

@pytest.fixture(autouse=True)
async def cleanup():
    yield
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
            try: await task
            except: pass
