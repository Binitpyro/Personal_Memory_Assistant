import pytest

from app.search import retrieval
from app.search.reranker import rerank


def test_query_type_heuristics_and_fts_sanitization():
    assert retrieval._is_inventory_query("how many files do i have")
    assert retrieval._is_project_query("show project overview")
    assert retrieval._is_unreal_query("ue5 uproject assets")

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


def test_fast_answer_builders_cover_branches():
    file_stats = {
        "total_files": 20,
        "total_size_mb": 12.5,
        "by_type": [{"ext": ".py", "count": 8}, {"ext": ".md", "count": 4}],
    }
    folder_profiles = [
        {
            "folder_tag": "GameProj",
            "project_type": "Unreal Engine",
            "file_count": 10,
            "total_size_bytes": 1024 * 1024,
            "folder_path": "C:/GameProj",
        }
    ]
    unreal_facts = [
        {
            "project_name": "GameProj",
            "folder_path": "C:/GameProj",
            "engine_version": "5.3",
            "total_assets": 50,
            "map_count": 2,
            "environment_assets": 8,
            "character_blueprints": 3,
            "pawn_blueprints": 1,
            "skeletal_meshes": 4,
            "material_count": 9,
            "niagara_systems": 2,
        }
    ]

    answer = retrieval._build_fast_answer(
        "show project summary and how many files and unreal overview",
        file_stats=file_stats,
        folder_profiles=folder_profiles,
        unreal_facts=unreal_facts,
    )
    assert answer is not None
    assert "You currently have 20 indexed files" in answer
    assert "Unreal project summary" in answer
    assert "Indexed project/folder profiles" in answer

    no_answer = retrieval._build_fast_answer(
        "explain quantum mechanics",
        file_stats=file_stats,
        folder_profiles=folder_profiles,
        unreal_facts=unreal_facts,
    )
    assert no_answer is None


@pytest.mark.asyncio
async def test_load_query_metadata_and_gather_full_inputs(monkeypatch):
    class FakeDB:
        async def get_all_folder_profiles(self):
            return [{"folder_tag": "A"}]

        async def get_file_stats_summary(self):
            return {"total_files": 1, "total_size_mb": 1.0, "by_type": [], "by_folder": []}

        async def get_all_unreal_project_facts(self):
            return [{"project_name": "U"}]

        async def get_folder_profiles_text(self):
            return "profiles text"

    db = FakeDB()
    profiles, stats, facts = await retrieval._load_query_metadata(
        db,
        inventory=True,
        project=True,
        unreal=True,
    )
    assert profiles and stats and facts

    async def fake_hybrid_retrieve(**_kwargs):
        return [{"file_path": "a.py", "text": "x", "folder_tag": "A"}]

    monkeypatch.setattr(retrieval, "hybrid_retrieve", fake_hybrid_retrieve)
    retrieved, out_stats, profiles_text = await retrieval._gather_full_rag_inputs(
        query="q",
        db=db,
        embedding_service=object(),
        chroma_client=object(),
        k=3,
        inventory=True,
        project=True,
        unreal=False,
        cached_file_stats=stats,
        include_profiles_text=True,
    )
    assert retrieved and out_stats == stats and profiles_text == "profiles text"


@pytest.mark.asyncio
async def test_rerank_empty_short_circuit():
    assert await rerank("query", []) == []


@pytest.mark.asyncio
async def test_rerank_with_mock_model(monkeypatch):
    class FakePrediction:
        def __init__(self, values):
            self._values = values

        def tolist(self):
            return self._values

    class FakeModel:
        def predict(self, pairs, show_progress_bar=False):
            assert show_progress_bar is False
            assert len(pairs) == 2
            return FakePrediction([0.1, 0.9])

    class FakeLoop:
        async def run_in_executor(self, _executor, fn):
            return fn()

    monkeypatch.setattr("app.search.reranker._get_model", lambda: FakeModel())
    monkeypatch.setattr("app.search.reranker.asyncio.get_running_loop", lambda: FakeLoop())

    results = [
        {"text": "first", "file_path": "a.py"},
        {"text": "second", "file_path": "b.py"},
    ]
    ranked = await rerank("question", results, top_k=1, text_key="text")
    assert len(ranked) == 1
    assert ranked[0]["file_path"] == "b.py"
    assert ranked[0]["rerank_score"] == 0.9
