import asyncio
import contextlib
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.embeddings.service import EmbeddingService
from app.insights.service import InsightsService
from app.main import app, get_db
from app.search import retrieval
from app.storage.db import DatabaseManager
from app.utils.metrics import Timer

# --- FIXTURES ---


@pytest.fixture
async def real_db(tmp_path):
    db_path = tmp_path / "test_exhaustive.db"
    db = DatabaseManager(str(db_path))
    await db.init_db()
    yield db
    await db.close()
    for suffix in ["", "-shm", "-wal", "-journal"]:
        p = Path(str(db_path) + suffix)
        if p.exists():
            with contextlib.suppress(BaseException):
                p.unlink()


@pytest.fixture
def mock_emb():
    m = MagicMock(spec=EmbeddingService)
    m.is_ready = True
    m.embed_query = AsyncMock(return_value=[0.1] * 384)
    import numpy as np
    m.embed_texts = AsyncMock(return_value=np.array([[0.1] * 384], dtype=np.float32))
    return m


@pytest.fixture
def mock_lancedb():
    m = MagicMock()
    m.semantic_search = AsyncMock(
        return_value={
            "ids": [["1"]],
            "distances": [[0.1]],
            "metadatas": [
                [
                    {
                        "file_path": "test.py",
                        "text": "hello world content string long enough to pass dedupe",
                        "rerank_score": 1.0,
                    }
                ]
            ],
        }
    )
    m.search_summaries = AsyncMock(
        return_value={
            "ids": [["s1"]],
            "metadatas": [[{"file_path": "test.py", "folder_tag": "tag"}]],
        }
    )
    m.add_documents = AsyncMock()
    m.add_summaries_batch = AsyncMock()
    m.add_query_cache = AsyncMock()
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
    await real_db.insert_file(
        {"path": "file1.py", "size": 1000, "modified_at": "now", "type": ".py", "folder_tag": "t"}
    )
    await real_db.insert_file(
        {"path": "file2.txt", "size": 500, "modified_at": "now", "type": ".txt", "folder_tag": "t"}
    )

    svc = InsightsService(real_db)

    # 2. Test dashboard insights
    stats = await svc.get_dashboard_insights()
    assert stats["total_size_bytes"] == 1500
    assert stats["file_count"] == 2
    assert stats["database_size_bytes"] > 0
    assert len(stats["top_files"]) == 2
    assert len(stats["cold_files"]) == 2
    assert ".py" in stats["type_breakdown"]
    assert stats["type_breakdown"][".py"]["count"] == 1

    # 3. Test get_filtered_files logic
    # Extension formatting branches (lstrip, lower)
    res_py = await svc.get_insights_for_extension("py")
    assert len(res_py["top_files"]) == 1
    assert res_py["top_files"][0]["path"] == "file1.py"

    res_dot_py = await svc.get_insights_for_extension(".PY")
    assert len(res_dot_py["top_files"]) == 1

    # 4. Error Path Coverage
    with patch.object(real_db, "execute_query", side_effect=Exception("Simulated Failure")):
        err_stats = await svc.get_dashboard_insights()
        assert err_stats["error"] is not None

        err_filter = await svc.get_insights_for_extension(".py")
        assert err_filter["error"] is not None


# --- OTHER LOGIC DEEP DIVE ---


@pytest.mark.asyncio
async def test_all_logic_deep(real_db, mock_emb, mock_lancedb, mock_llm):
    # 1. DB
    fid = await real_db.insert_file(
        {"path": "test.py", "size": 1024, "modified_at": "now", "type": ".py", "folder_tag": "tag"}
    )
    await real_db.insert_chunks_bulk(
        [
            {
                "file_id": fid,
                "start_offset": 0,
                "end_offset": 50,
                "text_preview": "hello world content string long enough to pass dedupe",
            }
        ]
        * 5
    )

    # 2. Retrieval logic
    from app.search.planner import QueryPlanner

    with (
        patch(
            "app.search.retrieval._fts_search", AsyncMock(return_value=[{"id": "1", "score": 1.0}])
        ),
        patch(
            "app.search.retrieval.rerank",
            AsyncMock(side_effect=lambda q, c, **kw: [dict(x, rerank_score=1.0) for x in c]),
        ),
    ):
        res = await retrieval.full_rag(
            "What is in test.py?", real_db, mock_emb, mock_lancedb, mock_llm, QueryPlanner()
        )
        assert res["answer"] == "Logic verified answer"


def test_metrics():
    with Timer("test"):
        pass


# --- API ---


def test_api_exhaustive(real_db, mock_emb, mock_lancedb):
    app.dependency_overrides[get_db] = lambda: real_db
    from app.api.deps import get_emb, get_lancedb, get_llm

    app.dependency_overrides[get_emb] = lambda: mock_emb
    app.dependency_overrides[get_lancedb] = lambda: mock_lancedb
    app.dependency_overrides[get_llm] = MagicMock()

    token = os.environ.get("X_LOCAL_ACCESS_TOKEN", "test-token")
    client = TestClient(app, headers={"X-Local-Access-Token": token})
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/system/metrics").status_code == 200
    assert client.get("/api/insights").status_code == 200
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
async def cleanup():
    existing_tasks = set(asyncio.all_tasks())
    yield
    for task in asyncio.all_tasks():
        if task not in existing_tasks and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
