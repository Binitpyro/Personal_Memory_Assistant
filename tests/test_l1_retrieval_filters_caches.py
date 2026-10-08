"""Audit lane L1: filter pushdown (A3-01, A3-02), degraded results (A3-07), answer-cache keys (A3-09).

Everything is mocked at the embedding / LanceDB / LLM boundary; the SQLite side is real.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.search import retrieval
from app.search.planner import QueryPlanner
from app.search.reranker import RerankerFailedError, RerankerNotInstalledError
from app.vector_store.lancedb_client import LanceDBClient

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_caches():
    retrieval.clear_retrieval_cache()
    yield
    retrieval.clear_retrieval_cache()


async def _seed_chunks(db, path, folder_tag, n, file_type=".md"):
    """Insert one file with *n* chunks; returns its chunk ids."""
    file_id = await db.insert_file(
        {
            "path": path,
            "size": 10,
            "modified_at": "2026-03-03T12:00:00",
            "type": file_type,
            "folder_tag": folder_tag,
            "summary": "s",
        }
    )
    before = await db.execute_query("SELECT COALESCE(MAX(id), 0) FROM chunks")
    await db.insert_chunks_bulk(
        [
            {
                "file_id": file_id,
                "start_offset": i * 10,
                "end_offset": i * 10 + 9,
                "text_preview": f"turbulence passage {i} of {path} " * 3,
            }
            for i in range(n)
        ]
    )
    return list(range(before[0][0] + 1, before[0][0] + 1 + n))


def _lancedb_over(rows):
    """mock_lancedb whose semantic_search behaves like pma_chunks.

    rows: (chunk_id, file_path, folder_tag) in nearest-first order. Like the real
    table it has no ``file_type`` column, so a predicate on one raises.
    """
    lance = MagicMock()
    lance.cache_scope = LanceDBClient.cache_scope
    seen: list[dict] = []

    async def semantic_search(query_emb, k=10, where_filter=None):
        seen.append(dict(where_filter or {}))
        for key in where_filter or {}:
            if key not in ("folder_tag", "file_path", "chunk_id"):
                raise RuntimeError(f"lance error: No field named {key}")
        sel = [r for r in rows if not where_filter or r[2] == where_filter.get("folder_tag", r[2])]
        sel = sel[:k]
        return {
            "ids": [[str(r[0]) for r in sel]],
            "distances": [[0.1 + 0.001 * i for i in range(len(sel))]],
            "metadatas": [[{"file_path": r[1], "folder_tag": r[2]} for r in sel]],
        }

    lance.semantic_search = semantic_search
    lance.seen_where = seen
    lance.search_summaries = AsyncMock(
        return_value={"ids": [[]], "distances": [[]], "metadatas": [[]]}
    )
    lance.search_cache = AsyncMock(return_value=None)
    lance.add_query_cache = AsyncMock()
    return lance


@pytest.fixture
def no_reranker(monkeypatch):
    async def _missing(*_a, **_kw):
        raise RerankerNotInstalledError("test")

    monkeypatch.setattr(retrieval, "rerank", _missing)


# --- A3-01: folder / file filters reach retrieval, not just the final list -----


async def test_folder_filter_is_pushed_down_not_applied_to_the_global_top_k(
    mock_db, mock_emb, no_reranker
):
    physics = await _seed_chunks(mock_db, "d:/physics/p.md", "physics", 80)
    travel = await _seed_chunks(mock_db, "d:/travel/t.md", "travel", 3)
    rows = [(c, "d:/physics/p.md", "physics") for c in physics] + [
        (c, "d:/travel/t.md", "travel") for c in travel
    ]
    lance = _lancedb_over(rows)

    res = await retrieval.retrieve_only(
        "turbulence", mock_db, mock_emb, lance, QueryPlanner(), folder_tag="travel"
    )

    assert res["retrieved_count"] == 3
    assert {s["folder_tag"] for s in res["sources"]} == {"travel"}


async def test_profiles_and_stream_near_misses_obey_the_filter(
    mock_db, mock_emb, no_reranker, monkeypatch
):
    physics = await _seed_chunks(mock_db, "d:/physics/p.md", "physics", 80)
    travel = await _seed_chunks(mock_db, "d:/travel/t.md", "travel", 12)
    rows = [(c, "d:/physics/p.md", "physics") for c in physics] + [
        (c, "d:/travel/t.md", "travel") for c in travel
    ]
    lance = _lancedb_over(rows)
    llm = MagicMock()
    llm.get_model_class = MagicMock(return_value="7b_local")

    async def fake_stream(*_a, **_kw):
        yield "fine"

    llm.stream_answer = fake_stream
    mock_db.save_query = AsyncMock(return_value=1)
    mock_db.save_telemetry = AsyncMock()

    frames = [
        f
        async for f in retrieval.stream_rag(
            "turbulence",
            mock_db,
            mock_emb,
            lance,
            llm,
            QueryPlanner(),
            k=5,
            folder_tag="travel",
        )
    ]
    sources = next(f for f in frames if f["type"] == "sources")
    assert sources["near_misses"], "12 travel chunks at k=5 must leave an overflow tail"
    assert {s["folder_tag"] for s in sources["sources"] + sources["near_misses"]} == {"travel"}
    profile_calls = [
        c for c in lance.search_summaries.await_args_list if "is_folder_profile" in str(c)
    ]
    assert any(
        c.kwargs.get("where_filter", {}).get("folder_tag") == "travel"
        for c in profile_calls
        if c.kwargs.get("where_filter", {}).get("is_folder_profile") == "true"
    ), "folder-profile lookup must be restricted to the filtered folder"


async def test_file_type_filter_has_no_folder_profiles(mock_db):
    lance = MagicMock()
    lance.search_summaries = AsyncMock()

    out = await retrieval._get_top_relevant_profiles(lance, mock_db, [0.1], file_type=".md")

    assert out == ""
    lance.search_summaries.assert_not_called()


# --- A3-02: file_type is not a pma_chunks column -------------------------------


async def test_file_type_filter_does_not_reach_lancedb_and_still_filters(
    mock_db, mock_emb, no_reranker
):
    md = await _seed_chunks(mock_db, "d:/n/a.md", "n", 2, ".md")
    txt = await _seed_chunks(mock_db, "d:/n/b.txt", "n", 2, ".txt")
    rows = [
        (txt[0], "d:/n/b.txt", "n"),
        (md[0], "d:/n/a.md", "n"),
        (txt[1], "d:/n/b.txt", "n"),
        (md[1], "d:/n/a.md", "n"),
    ]
    lance = _lancedb_over(rows)

    out = await retrieval.hybrid_retrieve(
        "turbulence", mock_db, mock_emb, lance, k=5, use_reranker=False, file_type=".md"
    )

    assert out, "file_type retrieval must return the matching chunks"
    assert {r["file_path"] for r in out} == {"d:/n/a.md"}
    assert all("file_type" not in w for w in lance.seen_where)


# --- A3-07: a degraded result is not cached ------------------------------------


async def test_degraded_retrieval_is_not_cached(mock_db, mock_emb, monkeypatch):
    ids = await _seed_chunks(mock_db, "d:/n/a.md", "n", 3)
    lance = _lancedb_over([(c, "d:/n/a.md", "n") for c in ids])
    calls = {"n": 0}

    async def failing_rerank(query, results, **_kw):
        calls["n"] += 1
        raise RerankerFailedError("timeout")

    monkeypatch.setattr(retrieval, "rerank", failing_rerank)

    first = await retrieval.hybrid_retrieve("turbulence", mock_db, mock_emb, lance, k=5)
    second = await retrieval.hybrid_retrieve("turbulence", mock_db, mock_emb, lance, k=5)

    assert all(r.get("_degraded") for r in first + second)
    assert calls["n"] == 2, "the second call must retry the reranker, not replay RRF order"


async def test_healthy_retrieval_is_still_cached(mock_db, mock_emb, monkeypatch):
    """Control for the test above: the guard must not have switched the cache off."""
    ids = await _seed_chunks(mock_db, "d:/n/a.md", "n", 3)
    lance = _lancedb_over([(c, "d:/n/a.md", "n") for c in ids])
    calls = {"n": 0}

    async def ok_rerank(query, results, **_kw):
        calls["n"] += 1
        return results

    monkeypatch.setattr(retrieval, "rerank", ok_rerank)

    await retrieval.hybrid_retrieve("turbulence", mock_db, mock_emb, lance, k=5)
    await retrieval.hybrid_retrieve("turbulence", mock_db, mock_emb, lance, k=5)

    assert calls["n"] == 1


async def test_full_rag_does_not_cache_a_degraded_or_blank_answer(mock_db, mock_emb, monkeypatch):
    ids = await _seed_chunks(mock_db, "d:/n/a.md", "n", 3)
    lance = _lancedb_over([(c, "d:/n/a.md", "n") for c in ids])

    async def failing_rerank(query, results, **_kw):
        raise RerankerFailedError("timeout")

    monkeypatch.setattr(retrieval, "rerank", failing_rerank)
    llm = MagicMock()
    llm.get_model_class = MagicMock(return_value="7b_local")
    llm.generate_answer = AsyncMock(return_value="an answer")

    for _ in range(2):
        res = await retrieval.full_rag("turbulence", mock_db, mock_emb, lance, llm, QueryPlanner())
        assert res["mode"] == "degraded_rag"

    assert llm.generate_answer.await_count == 2, "degraded answer was replayed from the cache"
    await asyncio.sleep(0)
    lance.add_query_cache.assert_not_called()

    # blank answers are not cached either
    retrieval.clear_retrieval_cache()
    monkeypatch.setattr(retrieval, "rerank", AsyncMock(side_effect=lambda q, r, **k: r))
    llm.generate_answer = AsyncMock(return_value="  ")
    for _ in range(2):
        await retrieval.full_rag("turbulence", mock_db, mock_emb, lance, llm, QueryPlanner())
    assert llm.generate_answer.await_count == 2


# --- A3-09: mode / provider / model are part of the answer-cache keys ----------


async def test_full_rag_answer_cache_is_keyed_on_mode(mock_db, mock_emb, no_reranker):
    ids = await _seed_chunks(mock_db, "d:/n/a.md", "n", 3)
    lance = _lancedb_over([(c, "d:/n/a.md", "n") for c in ids])
    llm = MagicMock()
    llm.get_model_class = MagicMock(return_value="7b_local")
    llm.generate_answer = AsyncMock(side_effect=lambda q, c, history=None, mode=None: f"ans-{mode}")

    async def ask(mode):
        return await retrieval.full_rag(
            "turbulence", mock_db, mock_emb, lance, llm, QueryPlanner(), mode=mode
        )

    default = await ask(None)
    eli5 = await ask("eli5")
    again = await ask("eli5")
    await asyncio.sleep(0)

    assert default["answer"] == "ans-None"
    assert eli5["answer"] == "ans-eli5", "eli5 was served the default-mode answer"
    assert again.get("cache_hit") is True and again["answer"] == "ans-eli5"
    scopes = [c.kwargs["scope"] for c in lance.add_query_cache.await_args_list]
    assert scopes == ["|", "||eli5||"]


async def test_stream_semantic_cache_scope_includes_mode_and_overrides(
    mock_db, mock_emb, no_reranker
):
    ids = await _seed_chunks(mock_db, "d:/n/a.md", "n", 3)
    lance = _lancedb_over([(c, "d:/n/a.md", "n") for c in ids])
    llm = MagicMock()
    llm.get_model_class = MagicMock(return_value="7b_local")

    async def fake_stream(*_a, **_kw):
        yield "fine"

    llm.stream_answer = fake_stream
    mock_db.save_query = AsyncMock(return_value=1)
    mock_db.save_telemetry = AsyncMock()

    [
        f
        async for f in retrieval.stream_rag(
            "turbulence",
            mock_db,
            mock_emb,
            lance,
            llm,
            QueryPlanner(),
            mode="verify",
            override_provider="ollama",
            override_model="m1",
        )
    ]
    await asyncio.sleep(0)

    assert lance.search_cache.await_args.kwargs["scope"] == "||verify|ollama|m1"
    assert lance.add_query_cache.await_args.kwargs["scope"] == "||verify|ollama|m1"
