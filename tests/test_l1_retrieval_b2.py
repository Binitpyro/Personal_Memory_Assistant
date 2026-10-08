"""Audit lane L1 batch 2: citation numbers (A3-10), semantic-leg degradation (A3-18),
rerank queue time (A9-07 / A3-13), challenge-mode conflicts banner (A3-19).

Everything is mocked at the embedding / LanceDB / LLM boundary; the SQLite side is real.
"""

import asyncio
import re
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.search import context_builder, retrieval
from app.search.planner import QueryPlanner
from app.search.reranker import RerankerNotInstalledError

_REAL_WAIT_FOR = asyncio.wait_for


@pytest.fixture(autouse=True)
def _clean_caches():
    retrieval.clear_retrieval_cache()
    yield
    retrieval.clear_retrieval_cache()


# --- A3-10: [n] in the prompt is the n-th source frame --------------------------


def test_snippet_numbers_are_positions_in_the_retrieved_list(monkeypatch):
    # No tokenizer download in a unit test: use the len//4 path.
    monkeypatch.setattr(context_builder, "_ENCODING", False)

    def chunk(cid, path, word):
        return {
            "chunk_id": cid,
            "file_id": hash(path) % 1000,
            "file_path": path,
            "text": f"{word} " * 30,
            "score": 1.0 - cid / 100,
        }

    # Three chunks of thesis.md then budget.md: the per-file cap (2) drops the
    # third thesis chunk, so budget.md is the 4th source frame but the 3rd snippet.
    retrieved = [
        chunk(11, "thesis.md", "alpha"),
        chunk(12, "thesis.md", "bravo"),
        chunk(13, "thesis.md", "charlie"),
        chunk(21, "budget.md", "delta"),
    ]
    ctx, _ = context_builder.build_context(retrieved, max_tokens=4000)

    labels = dict(re.findall(r"Snippet (\d+) \[ID: (\d+)\]", ctx))
    assert labels == {"1": "11", "2": "12", "4": "21"}, labels


# --- A3-18: a failing semantic leg degrades instead of failing the query --------


async def _seed_fts_hit(db):
    file_id = await db.insert_file(
        {
            "path": "d:/n/a.md",
            "size": 10,
            "modified_at": "2026-03-03T12:00:00",
            "type": ".md",
            "folder_tag": "n",
            "summary": "s",
        }
    )
    await db.insert_chunks_bulk(
        [
            {
                "file_id": file_id,
                "start_offset": 0,
                "end_offset": 50,
                "text_preview": "turbulence passage about wings " * 3,
            }
        ]
    )


def _lance_semantic_raises():
    lance = MagicMock()
    lance.semantic_search = AsyncMock(
        side_effect=RuntimeError("Invalid user input: query dim(768) doesn't match column dim(384)")
    )
    lance.search_summaries = AsyncMock(
        return_value={"ids": [[]], "distances": [[]], "metadatas": [[]]}
    )
    return lance


@pytest.mark.asyncio
async def test_semantic_leg_failure_degrades_to_keyword_results(mock_db, mock_emb, monkeypatch):
    await _seed_fts_hit(mock_db)

    async def _missing(*_a, **_kw):
        raise RerankerNotInstalledError("test")

    monkeypatch.setattr(retrieval, "rerank", _missing)

    out = await retrieval.hybrid_retrieve(
        "turbulence", mock_db, mock_emb, _lance_semantic_raises(), k=5, use_reranker=False
    )

    assert [r["file_path"] for r in out] == ["d:/n/a.md"], "FTS hit must survive"
    assert all(r.get("_degraded") for r in out), "answer must say it is degraded"
    # ...and the degraded result is not replayed from the retrieval cache (b1's rule).
    assert not retrieval._retrieval_cache


# --- A9-07 / A3-13: the rerank deadline does not count other queries' turns ------


@pytest.mark.asyncio
async def test_rerank_deadline_counts_own_inference_not_queue_time(monkeypatch):
    worker = asyncio.Lock()  # stands in for the single-slot ONNX executor

    async def one_at_a_time(query, results, top_k, text_key):
        async with worker:
            await asyncio.sleep(0.3)
        for r in results:
            r["rerank_score"] = 1.0
        return results

    async def short_deadline(aw, timeout):
        # 0.3 s of inference fits in 0.5 s; three queued behind it (0.9 s) do not.
        return await _REAL_WAIT_FOR(aw, 0.5)

    monkeypatch.setattr(retrieval, "rerank", one_at_a_time)
    monkeypatch.setattr(retrieval.asyncio, "wait_for", short_deadline)

    async def one(i):
        return await retrieval._apply_reranker_if_needed(
            [{"chunk_id": i, "text": "a"}, {"chunk_id": i + 100, "text": "b"}], "q", True, k=2
        )

    t0 = time.perf_counter()
    outs = await asyncio.gather(one(1), one(2), one(3))
    assert time.perf_counter() - t0 >= 0.85, "reranks must run one after another"
    assert not any(r.get("_degraded") for out in outs for r in out)


# --- A3-19: challenge mode only reports conflicts it actually found -------------


def _challenge_lance(chunk_ids_for):
    lance = MagicMock()

    async def semantic_search(query_emb, k=10, where_filter=None):
        ids = chunk_ids_for(query_emb)
        return {
            "ids": [[str(i) for i in ids]],
            "distances": [[0.1 + 0.01 * n for n in range(len(ids))]],
            "metadatas": [[{"file_path": "d:/n/a.md", "folder_tag": "n"} for _ in ids]],
        }

    lance.semantic_search = semantic_search
    lance.search_summaries = AsyncMock(
        return_value={"ids": [[]], "distances": [[]], "metadatas": [[]]}
    )
    lance.search_cache = AsyncMock(return_value=None)
    lance.add_query_cache = AsyncMock()
    lance.cache_scope = MagicMock(return_value="|")
    return lance


async def _challenge_frames(mock_db, mock_emb, lance, monkeypatch):
    async def _missing(*_a, **_kw):
        raise RerankerNotInstalledError("test")

    monkeypatch.setattr(retrieval, "rerank", _missing)
    file_id = await mock_db.insert_file(
        {
            "path": "d:/n/a.md",
            "size": 10,
            "modified_at": "2026-03-03T12:00:00",
            "type": ".md",
            "folder_tag": "n",
            "summary": "s",
        }
    )
    await mock_db.insert_chunks_bulk(
        [
            {
                "file_id": file_id,
                "start_offset": i * 100,
                "end_offset": i * 100 + 99,
                "text_preview": f"lorem ipsum dolor sit amet {i} " * 4,
            }
            for i in range(2)
        ]
    )

    async def embed_query(text):
        return [0.9] * 384 if text.startswith("Contradictions") else [0.1] * 384

    mock_emb.embed_query = AsyncMock(side_effect=embed_query)
    mock_db.save_query = AsyncMock(return_value=1)
    mock_db.save_telemetry = AsyncMock()

    llm = MagicMock()
    llm.get_model_class = MagicMock(return_value="7b_local")

    async def fake_stream(*_a, **_kw):
        yield "answer"

    llm.stream_answer = fake_stream
    llm.generate_answer = AsyncMock(return_value="x")

    frames = [
        f
        async for f in retrieval.stream_rag(
            "quantum entanglement?",
            mock_db,
            mock_emb,
            lance,
            llm,
            QueryPlanner(),
            k=1,
            mode="challenge",
        )
    ]
    return next(f for f in frames if f["type"] == "sources")


@pytest.mark.asyncio
async def test_challenge_mode_without_new_sources_reports_no_conflict(
    mock_db, mock_emb, monkeypatch
):
    # The negated query returns the same single chunk the answer already has.
    lance = _challenge_lance(lambda emb: [1])
    sources = await _challenge_frames(mock_db, mock_emb, lance, monkeypatch)

    assert sources["contradiction_sources"] == []
    assert sources["contradictions_found"] is False


@pytest.mark.asyncio
async def test_challenge_mode_with_new_source_still_reports_conflict(
    mock_db, mock_emb, monkeypatch
):
    lance = _challenge_lance(lambda emb: [2] if emb[0] > 0.5 else [1])
    sources = await _challenge_frames(mock_db, mock_emb, lance, monkeypatch)

    assert sources["contradiction_sources"] == [2]
    assert sources["contradictions_found"] is True


def test_rerank_turn_lock_works_across_event_loops(monkeypatch):
    """A module-level asyncio.Lock raised 'bound to a different event loop' on the 2nd loop."""

    async def slow(query, results, top_k, text_key):
        await asyncio.sleep(0.05)
        return results

    monkeypatch.setattr(retrieval, "rerank", slow)

    async def contend():
        outs = await asyncio.gather(
            *(
                retrieval._apply_reranker_if_needed(
                    [{"chunk_id": i, "text": "a"}, {"chunk_id": i + 9, "text": "b"}], "q", True, 2
                )
                for i in range(3)
            )
        )
        return [r for out in outs for r in out]

    for _ in range(2):  # two different loops, one after the other
        results = asyncio.run(contend())
        assert len(results) == 6
        assert not any(r.get("_degraded") for r in results)


# --- pinned context (forced_chunk_ids) never touches the persistent semantic cache -


async def _stream_pinned(mock_db, mock_emb, monkeypatch, forced, cache_hit=None):
    async def _missing(*_a, **_kw):
        raise RerankerNotInstalledError("test")

    monkeypatch.setattr(retrieval, "rerank", _missing)
    file_id = await mock_db.insert_file(
        {
            "path": "d:/n/a.md",
            "size": 10,
            "modified_at": "2026-03-03T12:00:00",
            "type": ".md",
            "folder_tag": "n",
            "summary": "s",
        }
    )
    await mock_db.insert_chunks_bulk(
        [
            {
                "file_id": file_id,
                "start_offset": 0,
                "end_offset": 99,
                "text_preview": "lorem ipsum dolor sit amet " * 4,
            }
        ]
    )
    mock_db.save_query = AsyncMock(return_value=1)
    mock_db.save_telemetry = AsyncMock()
    lance = _challenge_lance(lambda emb: [1])
    lance.search_cache = AsyncMock(return_value=cache_hit)
    llm = MagicMock()
    llm.get_model_class = MagicMock(return_value="7b_local")

    async def fake_stream(*_a, **_kw):
        yield "fresh answer"

    llm.stream_answer = fake_stream
    frames = [
        f
        async for f in retrieval.stream_rag(
            "quantum entanglement?",
            mock_db,
            mock_emb,
            lance,
            llm,
            QueryPlanner(),
            forced_chunk_ids=forced,
        )
    ]
    await asyncio.sleep(0)
    await asyncio.gather(*list(retrieval.state.bg_tasks), return_exceptions=True)
    return frames, lance


@pytest.mark.asyncio
async def test_pinned_query_does_not_read_or_write_the_semantic_cache(
    mock_db, mock_emb, monkeypatch
):
    frames, lance = await _stream_pinned(
        mock_db, mock_emb, monkeypatch, [1], cache_hit={"response_text": "UNPINNED CACHED"}
    )

    lance.search_cache.assert_not_awaited()
    assert "UNPINNED CACHED" not in "".join(f.get("text", "") for f in frames)
    lance.add_query_cache.assert_not_called()


@pytest.mark.asyncio
async def test_unpinned_query_still_uses_the_semantic_cache(mock_db, mock_emb, monkeypatch):
    # Control: the same flow with no pinned chunks reads the cache and writes to it.
    frames, lance = await _stream_pinned(mock_db, mock_emb, monkeypatch, None)
    lance.search_cache.assert_awaited()
    lance.add_query_cache.assert_called_once()

    frames, _ = await _stream_pinned(
        mock_db, mock_emb, monkeypatch, None, cache_hit={"response_text": "CACHED"}
    )
    assert "CACHED" in "".join(f.get("text", "") for f in frames)
