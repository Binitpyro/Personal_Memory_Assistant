"""With no LLM provider at all, a question is answered from the passages themselves.

Release-plan blocker B3: a fresh install has no provider, and Ask used to end in
`no_providers_configured`. `full_rag` and `stream_rag` now return the top
retrieved passages as cited snippets and say `llm_available: False`. Every other
provider failure is unchanged. Everything is mocked; no network.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.search import retrieval
from app.search.llm_client import (
    LLM_UNAVAILABLE_PREFIX,
    NO_PROVIDERS_ANSWER,
    LLMClient,
    ProviderNotConfiguredError,
)
from app.search.planner import QueryPlanner

pytestmark = pytest.mark.asyncio

_TEXT = "def main():\n    print('Hello World')  # entry point of the test project"
_NO_PROVIDERS = json.dumps(
    {"control": "provider_error", "code": "no_providers_configured", "message": "none"}
)


@pytest.fixture(autouse=True)
def _clean_caches():
    retrieval.clear_retrieval_cache()
    yield
    retrieval.clear_retrieval_cache()


async def _seed(mock_db, mock_lancedb, text: str = _TEXT):
    file_id = await mock_db.insert_file(
        {
            "path": "d:/test_project/main.py",
            "size": 2048,
            "modified_at": "2026-03-03T12:00:00",
            "type": ".py",
            "folder_tag": "test_tag",
            "summary": "Main entry point file",
        }
    )
    await mock_db.insert_chunks_bulk(
        [{"file_id": file_id, "start_offset": 0, "end_offset": 100, "text_preview": text}]
    )
    mock_lancedb.semantic_search = AsyncMock(
        return_value={
            "ids": [["1"]],
            "distances": [[0.1]],
            "metadatas": [
                [{"file_path": "d:/test_project/main.py", "text": text, "folder_tag": "test_tag"}]
            ],
        }
    )
    mock_lancedb.search_cache = AsyncMock(return_value=None)
    mock_lancedb.add_query_cache = AsyncMock()
    mock_db.save_query = AsyncMock(return_value=1)
    mock_db.save_telemetry = AsyncMock()


def _stream_llm(chunks: list[str]) -> MagicMock:
    llm = MagicMock()
    llm.get_model_class = MagicMock(return_value="3b_local")
    llm.generate_answer = AsyncMock(return_value="x")

    async def fake_stream(*args, **kwargs):
        for c in chunks:
            yield c

    llm.stream_answer = fake_stream
    return llm


async def _stream(mock_db, mock_emb, mock_lancedb, llm):
    return [
        f
        async for f in retrieval.stream_rag(
            "How does the main function work?",
            mock_db,
            mock_emb,
            mock_lancedb,
            llm,
            QueryPlanner(),
            file_type=".py",
            folder_tag="test_tag",
        )
    ]


async def _full(mock_db, mock_emb, mock_lancedb, llm):
    return await retrieval.full_rag(
        "How does the main function work?",
        mock_db,
        mock_emb,
        mock_lancedb,
        llm,
        QueryPlanner(),
        file_type=".py",
        folder_tag="test_tag",
    )


async def test_stream_with_no_provider_answers_with_cited_passages(mock_db, mock_emb, mock_lancedb):
    await _seed(mock_db, mock_lancedb)
    frames = await _stream(mock_db, mock_emb, mock_lancedb, _stream_llm([_NO_PROVIDERS]))

    assert [f["type"] for f in frames] == ["sources", "content"]
    content = frames[1]
    assert content["llm_available"] is False
    assert '<claim sources="[1]">' in content["text"]
    assert "Hello World" in content["text"]
    assert "(main.py) [1]" in content["text"]
    # The passage is the turn's answer in history, but it is never cached.
    mock_db.save_query.assert_awaited_once()
    assert mock_db.save_query.await_args.args[1] == content["text"]
    mock_lancedb.add_query_cache.assert_not_called()


async def test_stream_other_provider_errors_are_still_typed_errors(mock_db, mock_emb, mock_lancedb):
    """Control: only the no-provider code is answered extractively."""
    await _seed(mock_db, mock_lancedb)
    down = json.dumps({"control": "provider_error", "code": "local_provider_down", "message": "x"})
    frames = await _stream(mock_db, mock_emb, mock_lancedb, _stream_llm([down]))

    assert [f["type"] for f in frames] == ["sources", "error"]
    assert frames[1]["code"] == "local_provider_down"
    mock_db.save_query.assert_not_called()


async def test_full_rag_with_no_provider_answers_with_cited_passages(
    mock_db, mock_emb, mock_lancedb
):
    await _seed(mock_db, mock_lancedb)
    llm = MagicMock()
    llm.get_model_class = MagicMock(return_value="3b_local")
    llm.generate_answer = AsyncMock(return_value=NO_PROVIDERS_ANSWER)

    result = await _full(mock_db, mock_emb, mock_lancedb, llm)

    assert result["llm_available"] is False
    assert '<claim sources="[1]">' in result["answer"]
    assert "Hello World" in result["answer"]
    assert result["_is_error"] is True  # so it is never cached
    mock_lancedb.add_query_cache.assert_not_called()


async def test_full_rag_other_unavailable_prose_is_unchanged(mock_db, mock_emb, mock_lancedb):
    """Control: a dead configured chain still reports its own failure."""
    await _seed(mock_db, mock_lancedb)
    prose = f"{LLM_UNAVAILABLE_PREFIX}: ollama failed: Ollama is not running."
    llm = MagicMock()
    llm.get_model_class = MagicMock(return_value="3b_local")
    llm.generate_answer = AsyncMock(return_value=prose)

    result = await _full(mock_db, mock_emb, mock_lancedb, llm)

    assert result["llm_available"] is True
    assert result["answer"] == prose


async def test_passage_text_is_escaped_inside_the_claim_tag():
    out = retrieval._extractive_answer(
        [{"text": "<img src=x onerror=alert(1)> & more", "file_path": "d:/a/<b>.md"}]
    )
    assert "<img" not in out
    assert "&lt;img src=x onerror=alert(1)&gt; &amp; more" in out
    assert "(&lt;b&gt;.md)" in out


async def test_extractive_answer_with_no_passages_says_so():
    assert "no passages matched" in retrieval._extractive_answer([])


async def test_llm_client_with_empty_chain_reports_the_no_provider_signals():
    """The two signals retrieval keys on are what LLMClient really emits."""
    client = LLMClient()
    client._resolve = AsyncMock(side_effect=ProviderNotConfiguredError("none"))
    with (
        patch(
            "app.search.llm_client._get_effective_fallback_chain_async",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.search.llm_client.capability_detector.detect_capabilities",
            AsyncMock(return_value=False),
        ),
    ):
        answer = await client.generate_answer("q", "c", skip_capability_check=True)
        frames = [f async for f in client.stream_answer("q", "c")]

    assert answer == NO_PROVIDERS_ANSWER
    err = json.loads(frames[0])
    assert err["control"] == "provider_error"
    assert err["code"] == "no_providers_configured"
