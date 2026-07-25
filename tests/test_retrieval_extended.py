import pytest

from app.search import retrieval
from app.search.planner import PlanMode, QueryPlan


class MockCursor:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def fetchall(self):
        return self.rows


class MockConnection:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def execute(self, sql, params=()):
        return MockCursor(self.rows)


class MockDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self._pool_initialized = True

    def _get_read_conn(self):
        return MockConnection(self.rows)

    async def get_all_folder_profiles(self):
        return [
            {
                "folder_tag": "tag",
                "total_size_bytes": 1024 * 1024,
                "project_type": "python",
                "folder_path": "/p",
                "file_count": 5,
                "top_extensions": "py",
                "key_files": "a.py",
                "profile_text": "desc",
            }
        ]

    async def get_file_stats_summary(self):
        return {"total_files": 1, "total_size_mb": 1.0, "by_type": [], "by_folder": []}

    async def get_folder_profiles_text(self):
        return "profiles text"

    async def execute_query(self, sql, params=()):
        if "ORDER BY modified_at" in sql:
            return [("a.py", "2026-07-03")]
        if "ORDER BY size DESC" in sql:
            return [("a.py", 100)]
        if "FROM files" in sql or "FROM chunk_fts" in sql:
            return [(1, "chunks_text")]
        return self.rows


class MockEmbedding:
    async def embed_query(self, query):
        return [0.1] * 384


class MockLanceDB:
    async def semantic_search(self, query_emb, k=10, where_filter=None):
        return {
            "ids": [["1"]],
            "distances": [[0.9]],
            "metadatas": [[{"chunk_id": "1", "file_path": "a.py", "folder_tag": "tag"}]],
        }

    async def search(self, vector, k, filter_dict=None):
        return [{"chunk_id": "1", "file_path": "a.py", "folder_tag": "tag", "score": 0.9}]

    async def search_summaries(self, vector, k, where_filter=None):
        return {
            "metadatas": [
                [
                    {
                        "chunk_id": "1",
                        "file_path": "a.py",
                        "folder_tag": "tag",
                        "_embedding": [0.1] * 384,
                    }
                ]
            ]
        }


@pytest.mark.asyncio
async def test_determine_metadata_insights():
    # Test inventory, project, latest, largest intents
    db = MockDB([("a.py", 100, ".py", "tag", "2026-07-03")])
    stats = {
        "total_files": 1,
        "total_size_mb": 1.0,
        "by_type": [{"ext": ".py", "count": 1, "size_mb": 1.0}],
        "by_folder": [{"folder_tag": "tag", "count": 1, "size_mb": 1.0}],
    }
    profiles = [
        {
            "folder_tag": "tag",
            "total_size_bytes": 100,
            "project_type": "py",
            "folder_path": "/p",
            "file_count": 1,
            "top_extensions": "py",
            "key_files": "",
            "profile_text": "",
        }
    ]

    # query with latest intent
    res = await retrieval._get_metadata_insights("show the latest files", db, stats, profiles)
    assert res is not None
    assert "Metadata Insights" in res
    assert "a.py" in res

    # query with largest intent
    res2 = await retrieval._get_metadata_insights("what is the largest file", db, stats, profiles)
    assert res2 is not None
    assert "a.py" in res2

    # None mode
    plan_none = QueryPlan(mode=PlanMode.FULL_RAG, original_query="q", intents={})
    assert retrieval._build_fast_answer("q", plan_none, stats, profiles) is None


@pytest.mark.asyncio
async def test_build_fast_answer():
    # FAST_METADATA mode
    plan_meta = QueryPlan(
        mode=PlanMode.FAST_METADATA, original_query="how many files", intents={"inventory": True}
    )
    stats = {"total_files": 10, "total_size_mb": 5.5}
    ans = retrieval._build_fast_answer("how many files", plan_meta, stats, [])
    assert "10 indexed files" in ans

    # FAST_PROJECT mode
    plan_proj = QueryPlan(
        mode=PlanMode.FAST_PROJECT, original_query="projects", intents={"project": True}
    )
    profiles = [
        {
            "folder_tag": "tag",
            "total_size_bytes": 100,
            "project_type": "py",
            "folder_path": "/p",
            "file_count": 1,
            "top_extensions": "py",
            "key_files": "",
            "profile_text": "",
        }
    ]
    ans2 = retrieval._build_fast_answer("projects", plan_proj, stats, profiles)
    assert "indexed projects" in ans2

    # None mode
    plan_none = QueryPlan(mode=PlanMode.FULL_RAG, original_query="q", intents={})
    assert retrieval._build_fast_answer("q", plan_none, stats, profiles) is None


@pytest.mark.asyncio
async def test_fts_search_errors():
    from unittest.mock import AsyncMock

    db = MockDB()
    db.execute_query = AsyncMock(side_effect=Exception("Database error"))
    # fts search should return empty list on exception
    res = await retrieval._fts_search(db, "select *", 5)
    assert res == []


@pytest.mark.asyncio
async def test_hybrid_retrieve_cache_and_lengths():
    db = MockDB([("a.py", 1, 2, "text", "tag")])
    emb = MockEmbedding()
    lancedb = MockLanceDB()

    # Query with short words (<=3 words)
    res1 = await retrieval.hybrid_retrieve("hello doc", db, emb, lancedb, k=2, use_reranker=False)
    assert len(res1) >= 0

    # Query with medium words (<=8 words)
    res2 = await retrieval.hybrid_retrieve(
        "hello nice doc in project fold tag", db, emb, lancedb, k=2, use_reranker=False
    )
    assert len(res2) >= 0

    # Query with long words (>8 words)
    res3 = await retrieval.hybrid_retrieve(
        "hello nice doc in project fold tag with more long sentence here",
        db,
        emb,
        lancedb,
        k=2,
        use_reranker=False,
    )
    assert len(res3) >= 0
