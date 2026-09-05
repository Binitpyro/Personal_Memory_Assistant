"""A thinking model can burn its whole budget reasoning and answer nothing.

Ollama returns reasoning in `message.thinking`, a field `OllamaProvider` does not
use and the UI never renders. Before 2026-09-04 that meant `content` came back as
the empty string and the user saw a blank answer with no error explaining it.

Not hypothetical: the chunk_size sweep measured **13 of 100 queries** empty on
`gemma4-local` at chunk_size=2048 (CLAUDE.md 8.7h), and it reproduces on demand -
a 6,000-character RAG prompt at num_predict=256 returns content='',
thinking=1113 chars, done_reason='length', while the identical request with
`think: false` returns 1,271 characters of real answer inside the same budget.

These tests are fully mocked and hit no network, per CLAUDE.md section 11. The
behaviour they pin was verified against a live Ollama separately.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.providers import create_provider

_URL = "http://localhost:11434/api/chat"
_MSG = [{"role": "user", "content": "why?"}]


def _reply(content: str = "", thinking: str = "", done_reason: str = "stop") -> httpx.Response:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if thinking:
        message["thinking"] = thinking
    return httpx.Response(
        200,
        json={"message": message, "done": True, "done_reason": done_reason},
        request=httpx.Request("POST", _URL),
    )


def _provider():
    return create_provider(
        "ollama", base_url="http://localhost:11434", default_model="gemma4-local"
    )


class TestChatRecoversFromReasoningOnlyReplies:
    @pytest.mark.asyncio
    async def test_empty_content_with_thinking_retries_without_thinking(self):
        provider = _provider()
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.side_effect = [
                _reply(content="", thinking="a" * 1113, done_reason="length"),
                _reply(content="the real answer"),
            ]
            out = await provider.chat(_MSG, max_tokens=256)

        assert out == "the real answer"
        assert post.call_count == 2, "expected exactly one retry"
        # The retry is what makes the difference, so assert on it specifically.
        first, second = post.call_args_list
        assert "think" not in first.kwargs["json"], "the first call must not pre-disable thinking"
        assert second.kwargs["json"]["think"] is False
        # Same question, same budget - only `think` may differ.
        assert second.kwargs["json"]["messages"] == first.kwargs["json"]["messages"]
        assert second.kwargs["json"]["options"] == first.kwargs["json"]["options"]
        await provider.close()

    @pytest.mark.asyncio
    async def test_empty_content_without_thinking_does_not_retry(self):
        """An empty answer with no reasoning behind it is the model's own choice.

        Retrying would spend a second round trip to get the same nothing, so the
        recovery is gated on evidence that reasoning is what consumed the budget.
        """
        provider = _provider()
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _reply(content="", thinking="")
            out = await provider.chat(_MSG, max_tokens=256)

        assert out == ""
        assert post.call_count == 1
        await provider.close()

    @pytest.mark.asyncio
    async def test_a_normal_answer_costs_exactly_one_request(self):
        """Guards the cost of the fix: the happy path must not double round trips."""
        provider = _provider()
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _reply(content="direct answer")
            out = await provider.chat(_MSG, max_tokens=256)

        assert out == "direct answer"
        assert post.call_count == 1
        assert "think" not in post.call_args.kwargs["json"]
        await provider.close()

    @pytest.mark.asyncio
    async def test_a_still_empty_retry_returns_empty_rather_than_raising(self):
        provider = _provider()
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.side_effect = [
                _reply(content="", thinking="zzz", done_reason="length"),
                _reply(content=""),
            ]
            assert await provider.chat(_MSG, max_tokens=256) == ""
            assert post.call_count == 2
        await provider.close()


class _FakeStream:
    """Minimal stand-in for `client.stream(...)`'s async context manager."""

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


def _line(*, content: str = "", thinking: str = "", done: bool = False) -> str:
    import json

    message: dict[str, Any] = {"role": "assistant"}
    if content:
        message["content"] = content
    if thinking:
        message["thinking"] = thinking
    return json.dumps({"message": message, "done": done})


class TestStreamRecoversFromReasoningOnlyReplies:
    @pytest.mark.asyncio
    async def test_a_reasoning_only_stream_is_replayed_without_thinking(self):
        provider = _provider()
        passes = [
            _FakeStream([_line(thinking="pondering"), _line(thinking="more", done=True)]),
            _FakeStream([_line(content="real "), _line(content="answer", done=True)]),
        ]
        with patch("httpx.AsyncClient.stream", side_effect=passes) as stream:
            got = [c async for c in provider.stream(_MSG, max_tokens=256)]

        assert "".join(got) == "real answer"
        assert stream.call_count == 2
        assert stream.call_args_list[1].kwargs["json"]["think"] is False
        await provider.close()

    @pytest.mark.asyncio
    async def test_reasoning_is_never_yielded_to_the_caller(self):
        """Reasoning text is not an answer and must not reach the UI."""
        provider = _provider()
        passes = [
            _FakeStream([_line(thinking="secret"), _line(content="visible", done=True)]),
        ]
        with patch("httpx.AsyncClient.stream", side_effect=passes) as stream:
            got = [c async for c in provider.stream(_MSG, max_tokens=256)]

        assert "".join(got) == "visible"
        assert "secret" not in "".join(got)
        assert stream.call_count == 1, "content arrived, so no replay is warranted"
        await provider.close()

    @pytest.mark.asyncio
    async def test_an_empty_stream_with_no_reasoning_is_not_replayed(self):
        provider = _provider()
        passes = [_FakeStream([_line(done=True)])]
        with patch("httpx.AsyncClient.stream", side_effect=passes) as stream:
            got = [c async for c in provider.stream(_MSG, max_tokens=256)]

        assert got == []
        assert stream.call_count == 1
        await provider.close()
