"""A stream that ends without an answer must not be saved, annotated or cached as one.

Batch 3 of the local-AI sweep (DECISIONS.md, F6'' / F7''):

* `stream_rag` stops reading at a `provider_error` frame, closes the provider
  stream, skips the annotator and the cache, and saves only a non-blank partial.
  A clean-but-blank stream is a typed `empty_answer` error and is saved nowhere.
* `generate_answer` reports a dead chain as prose; `LLM_UNAVAILABLE_PREFIX` lets
  the annotator and `full_rag` refuse to treat that prose as an answer.
* A provider that sends an error object inside an HTTP 200 stream (LM Studio on a
  context overflow) raises before content, so the fallback loop sees it, and
  ends the stream with a warning after content.

Everything is mocked; no network. The LM Studio line is the byte shape measured
in M4_lm_studio.md (L3). The Ollama in-stream `{"error": ...}` shape is RECALLED,
not measured, so its tests are synthetic.
"""

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.providers import create_provider
from app.providers.base import ProviderStreamError
from app.search import retrieval
from app.search.llm_client import LLM_UNAVAILABLE_PREFIX, LLMClient
from app.search.planner import QueryPlanner

pytestmark = pytest.mark.asyncio

# --- stream_rag -------------------------------------------------------------

_ERR = json.dumps({"control": "provider_error", "code": "context_overflow", "message": "too long"})
_USAGE = json.dumps({"control": "usage", "prompt_tokens": 1, "completion_tokens": 1})


@pytest.fixture(autouse=True)
def _clean_caches():
    retrieval.clear_retrieval_cache()
    yield
    retrieval.clear_retrieval_cache()


async def _seed(mock_db, mock_lancedb):
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
    text = "import os\ndef main():\n    print('Hello World')\n# long enough to satisfy snippets"
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


def _llm(chunks: list[str], annotation: str = "x, y") -> tuple[MagicMock, dict[str, bool]]:
    state = {"closed": False, "exhausted": False}
    llm = MagicMock()
    llm.get_model_class = MagicMock(return_value="7b_local")
    llm.generate_answer = AsyncMock(return_value=annotation)

    async def fake_stream(*args, **kwargs):
        try:
            for c in chunks:
                yield c
            state["exhausted"] = True
        finally:
            state["closed"] = True

    llm.stream_answer = fake_stream
    return llm, state


async def _run(mock_db, mock_emb, mock_lancedb, llm) -> list[dict[str, Any]]:
    await _seed(mock_db, mock_lancedb)
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


async def test_provider_error_stops_the_stream_and_skips_everything_after(
    mock_db, mock_emb, mock_lancedb
):
    llm, state = _llm([_ERR, _USAGE, "never read"])
    frames = await _run(mock_db, mock_emb, mock_lancedb, llm)

    assert [f["type"] for f in frames] == ["sources", "error"]
    assert frames[1]["code"] == "context_overflow"
    assert state["closed"] and not state["exhausted"], "generator must be closed, not drained"
    llm.generate_answer.assert_not_called()  # annotator
    mock_db.save_query.assert_not_called()  # blank answer is not history
    mock_lancedb.add_query_cache.assert_not_called()


async def test_provider_error_after_partial_saves_the_partial_but_never_caches_it(
    mock_db, mock_emb, mock_lancedb
):
    llm, state = _llm(["half an ans", _ERR, _USAGE])
    frames = await _run(mock_db, mock_emb, mock_lancedb, llm)

    assert [f["type"] for f in frames] == ["sources", "content", "error"]
    assert state["closed"] and not state["exhausted"]
    llm.generate_answer.assert_not_called()
    mock_db.save_query.assert_awaited_once()
    assert mock_db.save_query.await_args.args[1] == "half an ans"
    mock_lancedb.add_query_cache.assert_not_called()


async def test_a_blank_clean_stream_is_an_empty_answer_error_and_saved_nowhere(
    mock_db, mock_emb, mock_lancedb
):
    llm, _ = _llm(["  \n", _USAGE])
    frames = await _run(mock_db, mock_emb, mock_lancedb, llm)

    assert frames[-1]["type"] == "error"
    assert frames[-1]["code"] == "empty_answer"
    assert frames[-1]["text"] == "The model returned an empty reply."
    llm.generate_answer.assert_not_called()
    mock_db.save_query.assert_not_called()
    mock_lancedb.add_query_cache.assert_not_called()


async def test_a_good_answer_is_still_annotated_saved_and_cached(mock_db, mock_emb, mock_lancedb):
    """Control: the guards above must not have turned the happy path off."""
    llm, _ = _llm(["fine answer", _USAGE], annotation="a, b")
    frames = await _run(mock_db, mock_emb, mock_lancedb, llm)

    types = [f["type"] for f in frames]
    assert "error" not in types
    assert next(f for f in frames if f["type"] == "metadata")["pattern_annotations"] == ["a", "b"]
    mock_db.save_query.assert_awaited_once()
    mock_lancedb.add_query_cache.assert_called_once()


async def test_an_annotator_that_returns_the_unavailable_prose_yields_no_chips(
    mock_db, mock_emb, mock_lancedb
):
    llm, _ = _llm(["fine answer", _USAGE], annotation=f"{LLM_UNAVAILABLE_PREFIX}: chain failed")
    frames = await _run(mock_db, mock_emb, mock_lancedb, llm)

    assert not [f for f in frames if f["type"] == "metadata"]
    mock_db.save_query.assert_awaited_once()  # the real answer is unaffected


# --- full_rag / cache -------------------------------------------------------


async def test_full_rag_does_not_cache_the_unavailable_prose(mock_db, mock_emb, mock_lancedb):
    await _seed(mock_db, mock_lancedb)
    llm = MagicMock()
    llm.get_model_class = MagicMock(return_value="7b_local")
    llm.generate_answer = AsyncMock(return_value=f"{LLM_UNAVAILABLE_PREFIX}: No providers.")

    result = await retrieval.full_rag(
        "How does the main function work?",
        mock_db,
        mock_emb,
        mock_lancedb,
        llm,
        QueryPlanner(),
        file_type=".py",
        folder_tag="test_tag",
    )

    assert result["_is_error"] is True
    mock_lancedb.add_query_cache.assert_not_called()


async def test_generate_answer_failure_prose_starts_with_the_prefix():
    """The prefix constant must be what `generate_answer` really returns."""
    from app.search.llm_client import ProviderNotConfiguredError

    client = LLMClient()
    client._resolve_provider_by_id = AsyncMock(side_effect=ProviderNotConfiguredError("nope"))
    ans = await client.generate_answer("q", "c")

    assert LLM_UNAVAILABLE_PREFIX == "LLM unavailable"
    assert ans.startswith(LLM_UNAVAILABLE_PREFIX)


async def test_add_query_cache_ignores_a_blank_response():
    from app.vector_store.lancedb_client import LanceDBClient

    client = LanceDBClient.__new__(LanceDBClient)
    client.connect = MagicMock(side_effect=AssertionError("must return before touching the table"))
    for blank in ("", "  \n\t"):
        await client.add_query_cache([0.1] * 4, "q", blank, 0.0)


# --- provider streams -------------------------------------------------------

_MSG = [{"role": "user", "content": "hi"}]

# Measured shape (M4_lm_studio.md L3): HTTP 200, `event: error`, then a data line.
_LMS_INNER = {
    "error": {
        "code": 400,
        "message": "request (8596 tokens) exceeds the available context size (8192 tokens), "
        "try increasing it",
        "type": "exceed_context_size_error",
        "n_prompt_tokens": 8596,
        "n_ctx": 8192,
    }
}
_LMS_MSG = "Engine protocol predict request returned 400: " + json.dumps(_LMS_INNER)
_LMS_ERROR_LINES = [
    "event: error",
    "data: " + json.dumps({"error": {"message": _LMS_MSG}, "message": _LMS_MSG}),
]


class _FakeStream:
    def __init__(self, lines: list[str]):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _delta(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})


def _lm_studio():
    return create_provider("lm_studio", base_url="http://localhost:1234/v1", default_model="m")


async def test_openai_compat_error_before_content_raises_with_overflow_code():
    provider = _lm_studio()
    with (
        patch("httpx.AsyncClient.stream", return_value=_FakeStream(_LMS_ERROR_LINES)),
        pytest.raises(ProviderStreamError) as exc,
    ):
        [c async for c in provider.stream(_MSG)]

    assert exc.value.code == "context_overflow"
    assert len(str(exc.value)) <= 500
    assert isinstance(exc.value, httpx.RequestError)  # what the llm_client loop catches
    await provider.close()


async def test_openai_compat_unrecognised_error_has_no_code_and_is_truncated():
    provider = _lm_studio()
    lines = ["data: " + json.dumps({"error": {"message": "boom " * 400}})]
    with (
        patch("httpx.AsyncClient.stream", return_value=_FakeStream(lines)),
        pytest.raises(ProviderStreamError) as exc,
    ):
        [c async for c in provider.stream(_MSG)]

    assert exc.value.code is None
    assert len(str(exc.value)) == 500
    await provider.close()


async def test_openai_compat_error_after_content_warns_and_ends_cleanly(caplog):
    provider = _lm_studio()
    lines = [_delta("par"), _delta("tial"), *_LMS_ERROR_LINES, _delta("never")]
    with (
        patch("httpx.AsyncClient.stream", return_value=_FakeStream(lines)),
        caplog.at_level(logging.WARNING, logger="app.providers.openai_compat"),
    ):
        got = [c async for c in provider.stream(_MSG)]

    assert got == ["par", "tial"]
    assert any("after content" in r.getMessage() for r in caplog.records)
    await provider.close()


async def test_the_overflow_code_reaches_the_provider_error_frame():
    provider = _lm_studio()
    client = LLMClient()
    with (
        patch.object(LLMClient, "_ensure_token_loaded", new=AsyncMock()),
        patch.object(LLMClient, "_resolve_provider_by_id", new=AsyncMock(return_value=provider)),
        patch(
            "app.search.llm_client._get_effective_fallback_chain_async",
            new=AsyncMock(return_value=["lm_studio"]),
        ),
        patch(
            "app.search.capability_detector.capability_detector.detect_capabilities",
            new=AsyncMock(return_value=False),
        ),
        patch("httpx.AsyncClient.stream", return_value=_FakeStream(_LMS_ERROR_LINES)),
    ):
        chunks = [c async for c in client.stream_answer("q", "ctx")]

    frames = [json.loads(c) for c in chunks if c.startswith('{"control":')]
    err = [f for f in frames if f["control"] == "provider_error"]
    assert err and err[0]["code"] == "context_overflow"
    assert "exceeds the available context size" in err[0]["message"]


# Ollama's in-stream error line is RECALLED (`{"error": "..."}`), not measured:
# these tests are synthetic.
def _ollama():
    return create_provider(
        "ollama", base_url="http://localhost:11434", default_model="gemma4-local"
    )


def _ol_content(text: str) -> str:
    return json.dumps({"message": {"role": "assistant", "content": text}, "done": False})


async def test_ollama_error_before_content_raises_with_overflow_code_synthetic():
    provider = _ollama()
    lines = [json.dumps({"error": "the request exceeds the available context size"})]
    with (
        patch("httpx.AsyncClient.stream", return_value=_FakeStream(lines)),
        pytest.raises(ProviderStreamError) as exc,
    ):
        [c async for c in provider.stream(_MSG)]

    assert exc.value.code == "context_overflow"
    await provider.close()


async def test_ollama_error_after_content_warns_and_ends_without_replay_synthetic(caplog):
    provider = _ollama()
    lines = [_ol_content("par"), json.dumps({"error": "model crashed"}), _ol_content("never")]
    with (
        patch("httpx.AsyncClient.stream", return_value=_FakeStream(lines)) as stream,
        caplog.at_level(logging.WARNING, logger="app.providers.ollama"),
    ):
        got = [c async for c in provider.stream(_MSG)]

    assert got == ["par"]
    assert stream.call_count == 1
    assert any("after content" in r.getMessage() for r in caplog.records)
    await provider.close()
