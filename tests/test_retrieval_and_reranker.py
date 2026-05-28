import pytest

from app.search import retrieval
from app.search.reranker import rerank


def test_query_heuristics_and_fts_sanitization():
    # Test new heuristics logic via intent determination
    from app.project_constants import determine_query_intent

    assert determine_query_intent("show me the latest files")["latest"]
    assert determine_query_intent("what is the biggest file")["largest"]

    sanitized = retrieval._sanitize_fts_query('hello AND "world" * test')
    assert sanitized == '"hello" "world" "test"'

    fallback = retrieval._sanitize_fts_query('""')
    assert fallback == '""'


def test_compute_rrf_scores_and_filter_results(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "rrf_k", 60)
    monkeypatch.setattr(retrieval.settings, "rrf_fts_weight", 1.0)
    monkeypatch.setattr(retrieval.settings, "rrf_semantic_weight", 1.0)

    fts = [{"id": "1"}, {"id": "2"}]
    sem = [{"id": "2"}, {"id": "3"}]
    ranked = retrieval._compute_rrf_scores(fts, sem, k=3)

    ids = [chunk_id for chunk_id, _ in ranked]
    assert "2" in ids
    assert len(ranked) == 3

    filtered = retrieval._filter_retrieved_results(
        [
            {"file_path": "a.py", "folder_tag": "A"},
            {"file_path": "b.md", "folder_tag": "B"},
        ],
        file_type=".py",
        folder_tag="A",
    )
    assert filtered == [{"file_path": "a.py", "folder_tag": "A"}]


@pytest.mark.asyncio
async def test_load_query_metadata_and_gather_full_inputs(monkeypatch):
    class FakeDB:
        async def get_all_folder_profiles(self):
            return [{"folder_tag": "A"}]

        async def get_file_stats_summary(self):
            return {"total_files": 1, "total_size_mb": 1.0, "by_type": [], "by_folder": []}



        async def get_folder_profiles_text(self):
            return "profiles text"

    db = FakeDB()
    profiles, stats = await retrieval._load_query_metadata(
        db,
        inventory=True,
        project=True,
    )
    assert profiles and stats

    async def fake_hybrid_retrieve(**_kwargs):
        return [{"file_path": "a.py", "text": "x", "folder_tag": "A"}]

    class FakeEmb:
        async def embed_query(self, q):
            return [0.1] * 384

    class FakeLanceDB:
        async def search_summaries(self, *args, **kwargs):
            return {"metadatas": [[]]}

    monkeypatch.setattr(retrieval, "hybrid_retrieve", fake_hybrid_retrieve)
    retrieved, out_stats, profiles_text = await retrieval._gather_full_rag_inputs(
        query="q",
        db=db,
        embedding_service=FakeEmb(),
        lancedb_client=FakeLanceDB(),
        k=3,
        inventory=True,
        project=True,
        cached_file_stats=stats,
        include_profiles_text=True,
    )
    assert retrieved and out_stats == stats and profiles_text == "profiles text"


@pytest.mark.asyncio
async def test_rerank_empty_short_circuit():
    assert await rerank("query", []) == []


@pytest.mark.asyncio
async def test_rerank_with_mock_model(monkeypatch):
    import numpy as np
    
    class FakeSession:
        def run(self, output_names, inputs):
            return [np.array([[0.1], [0.9]])]
            
    class FakeEncoding:
        ids = [1]
        attention_mask = [1]
        type_ids = [0]

    class FakeTokenizer:
        def encode_batch(self, pairs):
            return [FakeEncoding(), FakeEncoding()]

    class FakeLoop:
        async def run_in_executor(self, _executor, fn):
            return fn()

    monkeypatch.setattr("app.search.reranker._get_model_assets", lambda: (FakeSession(), FakeTokenizer()))
    monkeypatch.setattr("app.search.reranker.asyncio.get_running_loop", lambda: FakeLoop())

    results = [
        {"text": "first", "file_path": "a.py"},
        {"text": "second", "file_path": "b.py"},
    ]
    ranked = await rerank("question", results, top_k=1, text_key="text")
    assert len(ranked) == 1
    assert ranked[0]["file_path"] == "b.py"
    assert ranked[0]["rerank_score"] == 0.9


def test_build_candidate_results_deduplication(monkeypatch):
    # Setup data with overlapping snippets
    # The signature is extracted from max(0, mid-100) : mid+100

    base = "prefix" * 20  # 120 chars
    middle1 = " This is the target signature content that should match. " * 3  # ~150 chars
    suffix1 = " suffix1" * 10
    suffix2 = " suffix2" * 10

    text1 = base + middle1 + suffix1
    text2 = base + middle1 + suffix2  # same middle, should be deduplicated
    text3 = "different" * 50  # completely different

    row_map = {
        1: (1, text1, "file1.txt", "tag1"),
        2: (2, text2, "file2.txt", "tag2"),
        3: (3, text3, "file3.txt", "tag3"),
    }
    chunk_ids_ordered = [1, 2, 3]
    score_map = {1: 1.0, 2: 0.9, 3: 0.8}
    relevant_doc_paths = set()

    monkeypatch.setattr(retrieval.settings, "rrf_score_scale", 1.0)

    results = retrieval._build_candidate_results(
        chunk_ids_ordered, row_map, score_map, relevant_doc_paths
    )

    # Expected: chunk 1 and chunk 3. Chunk 2 is a duplicate signature.
    assert len(results) == 2
    assert results[0]["chunk_id"] == 1
    assert results[1]["chunk_id"] == 3

    # Verify scores
    assert results[0]["score"] == 1.0
    assert results[1]["score"] == 0.8


def test_build_candidate_results_short_text():
    # Chunks < 50 chars should be skipped
    row_map = {
        1: (1, "too short", "file1.txt", "tag1"),
        2: (2, "A" * 60, "file2.txt", "tag2"),
    }
    chunk_ids_ordered = [1, 2]
    score_map = {1: 1.0, 2: 0.9}

    results = retrieval._build_candidate_results(chunk_ids_ordered, row_map, score_map, set())

    assert len(results) == 1
    assert results[0]["chunk_id"] == 2
