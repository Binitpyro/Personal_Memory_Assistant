import pytest

from app.search import retrieval
from app.search.reranker import rerank


def test_query_heuristics_and_fts_sanitization():
    # Test new heuristics logic via intent determination
    from app.project_constants import determine_query_intent

    assert determine_query_intent("show me the latest files")["latest"]
    assert determine_query_intent("what is the biggest file")["largest"]

    # Terms are OR-ed, not implicitly AND-ed. The old expression required every
    # token to co-occur in one 512-character chunk, so real questions matched
    # nothing and the keyword leg contributed an empty list on every chat query.
    sanitized = retrieval._sanitize_fts_query('hello AND "world" * test')
    assert sanitized == '"hello" OR "test" OR "world"'

    # Sub-trigram terms are dropped: they cannot be indexed by a trigram
    # tokenizer, and under the old AND they constrained nothing at all.
    assert retrieval._sanitize_fts_query("3D pipeline") == '"pipeline"'

    # Nothing matchable -> empty expression, which _fts_search reports and
    # short-circuits rather than issuing a MATCH that cannot hit.
    assert retrieval._sanitize_fts_query('""') == ""
    assert retrieval._sanitize_fts_query("is a of") == ""

    # plan.keywords wins when supplied - it is the stop-word-stripped form.
    assert (
        retrieval._sanitize_fts_query("what is the turbulence model", ["turbulence", "model"])
        == '"model" OR "turbulence"'
    )


def test_compute_rrf_scores_and_filter_results(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "rrf_k", 60)
    monkeypatch.setattr(retrieval.settings, "rrf_fts_weight", 1.0)
    monkeypatch.setattr(retrieval.settings, "rrf_semantic_weight", 1.0)

    fts = [{"id": "1"}, {"id": "2"}]
    sem = [{"id": "2"}, {"id": "3"}]
    ranked = retrieval._compute_rrf_scores(fts, sem, None, k=3)

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
        ids = [1]  # noqa: RUF012
        attention_mask = [1]  # noqa: RUF012
        type_ids = [0]  # noqa: RUF012

    class FakeTokenizer:
        def encode_batch(self, pairs):
            return [FakeEncoding(), FakeEncoding()]

    class FakeLoop:
        async def run_in_executor(self, _executor, fn):
            return fn()

    monkeypatch.setattr(
        "app.search.reranker._get_model_assets", lambda: (FakeSession(), FakeTokenizer())
    )
    monkeypatch.setattr("app.search.reranker.asyncio.get_running_loop", lambda: FakeLoop())

    results = [
        {"text": "first", "file_path": "a.py"},
        {"text": "second", "file_path": "b.py"},
    ]
    ranked = await rerank("question", results, top_k=1, text_key="text")
    assert len(ranked) == 1
    assert ranked[0]["file_path"] == "b.py"
    assert ranked[0]["rerank_score"] == 0.9


def test_build_candidate_results_no_longer_deduplicates(monkeypatch):
    """Dedup moved out of the candidate stage.

    It used to run here on a MinHash of a 200-character middle slice, before
    the reranker, so it could drop a chunk the reranker would have promoted -
    and two chunks with similar middles but different heads and tails were
    treated as duplicates. There is now a single exact pass after reranking in
    ``context_builder._deduplicate_redundant``; this stage passes candidates
    through untouched.
    """
    base = "prefix" * 20  # 120 chars
    middle1 = " This is the target signature content that should match. " * 3  # ~150 chars
    suffix1 = " suffix1" * 10
    suffix2 = " suffix2" * 10

    text1 = base + middle1 + suffix1
    text2 = base + middle1 + suffix2  # same middle, different tail
    text3 = "different" * 50

    row_map = {
        1: (1, text1, "file1.txt", "tag1", 12345, 0, 10, "[]", "1.0", 11),
        2: (2, text2, "file2.txt", "tag2", 12345, 0, 10, "[]", "1.0", 12),
        3: (3, text3, "file3.txt", "tag3", 12345, 0, 10, "[]", "1.0", 13),
    }
    chunk_ids_ordered = [1, 2, 3]
    score_map = {1: 1.0, 2: 0.9, 3: 0.8}

    monkeypatch.setattr(retrieval.settings, "rrf_score_scale", 1.0)

    results = retrieval._build_candidate_results(chunk_ids_ordered, row_map, score_map)

    assert [r["chunk_id"] for r in results] == [1, 2, 3]
    assert [r["score"] for r in results] == [1.0, 0.9, 0.8]


def test_build_candidate_results_short_text():
    # Chunks < 50 chars should be skipped
    row_map = {
        1: (1, "too short", "file1.txt", "tag1", 12345, 0, 10, "[]", "1.0", 11),
        2: (2, "A" * 60, "file2.txt", "tag2", 12345, 0, 10, "[]", "1.0", 12),
    }
    chunk_ids_ordered = [1, 2]
    score_map = {1: 1.0, 2: 0.9}

    results = retrieval._build_candidate_results(chunk_ids_ordered, row_map, score_map)

    assert len(results) == 1
    assert results[0]["chunk_id"] == 2
