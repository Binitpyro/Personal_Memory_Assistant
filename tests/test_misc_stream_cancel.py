"""M-15: a generation stopped mid-stream must still land in query history."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.search import retrieval
from app.search.planner import QueryPlanner


@pytest.fixture
def harness(monkeypatch, mock_db, mock_emb):
    chunk = {
        "chunk_id": 1,
        "text": "lorem ipsum",
        "file_path": "d:/n/a.md",
        "folder_tag": "n",
        "score": 1.0,
    }
    monkeypatch.setattr(retrieval, "_maybe_run_agentic_loop", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(
        retrieval, "_gather_full_rag_inputs", AsyncMock(return_value=([chunk], None, ""))
    )
    monkeypatch.setattr(retrieval, "_extract_knowledge_gaps", AsyncMock(return_value=[]))
    mock_emb.embed_query = AsyncMock(return_value=[0.1] * 384)
    mock_db.save_query = AsyncMock(return_value=7)
    mock_db.save_telemetry = AsyncMock()
    lance = MagicMock()
    lance.search_cache = AsyncMock(return_value=None)
    lance.add_query_cache = AsyncMock()
    lance.cache_scope = MagicMock(return_value="|")

    llm = MagicMock()
    llm.get_model_class = MagicMock(return_value="7b_local")
    llm.generate_answer = AsyncMock(return_value="x")

    async def stalls_after_partial(*_a, **_kw):
        yield "partial answer"
        await asyncio.sleep(60)

    llm.stream_answer = stalls_after_partial

    def gen():
        return retrieval.stream_rag(
            "quantum entanglement?", mock_db, mock_emb, lance, llm, QueryPlanner(), k=1
        )

    return gen, mock_db, lance, llm


async def _drain_bg():
    from app import state

    await asyncio.gather(*list(state.bg_tasks))


def _assert_saved_uncached(db, lance):
    db.save_query.assert_awaited_once()
    assert db.save_query.await_args.args[:2] == ("quantum entanglement?", "partial answer")
    assert db.save_telemetry.await_args.kwargs["response_abandoned"] is True
    lance.add_query_cache.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_mid_generation_saves_partial_history(harness):
    gen, db, lance, _llm = harness
    seen = asyncio.Event()

    async def consume():
        async for f in gen():
            if f["type"] == "content":
                seen.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(seen.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _drain_bg()
    _assert_saved_uncached(db, lance)


@pytest.mark.asyncio
async def test_closing_the_stream_mid_generation_saves_partial_history(harness):
    gen, db, lance, _llm = harness
    g = gen()
    async for f in g:
        if f["type"] == "content":
            break
    await g.aclose()
    await _drain_bg()
    _assert_saved_uncached(db, lance)


@pytest.mark.asyncio
async def test_cancel_before_any_token_saves_nothing(harness):
    gen, db, _lance, llm = harness
    blocked = asyncio.Event()

    async def silent(*_a, **_kw):
        blocked.set()
        await asyncio.sleep(60)
        yield "never"

    llm.stream_answer = silent

    async def consume():
        async for _ in gen():
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(blocked.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _drain_bg()
    db.save_query.assert_not_called()
