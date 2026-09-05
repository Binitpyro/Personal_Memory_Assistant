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
from app.providers.ollama import _OLLAMA_DEFAULT_NUM_CTX, _required_num_ctx

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


class TestNumCtxIsSetSoOllamaStopsDiscardingContext:
    """PMA assembled a context budget and Ollama threw most of it away.

    `_chat_payload` never sent `num_ctx`, so every request ran at Ollama's
    server default. Measured live 2026-09-04 against `gemma4-local`, whose
    DECLARED window is 131,072:

        ~4,000-token prompt, num_ctx unset -> prompt_eval_count 4015  (whole)
        ~4,200-token prompt, num_ctx unset -> prompt_eval_count 2051  (cliff)
        ~8,000-token prompt, num_ctx unset -> prompt_eval_count 2051
        ~8,000-token prompt, num_ctx set   -> prompt_eval_count 8022

    The cliff is 4096 and past it Ollama discards roughly HALF the prompt, not
    just the overflow. `compute_context_budget` gives `7b_local` ~8,520 tokens,
    so the class with the largest budget was losing the most - about 3.9x.

    This also retracts CLAUDE.md 8.7f's reading of the ~4,099 cap as a property
    of `gemma2-2b`: `gemma4-local` does the same with a 131,072 window, so the
    limit is the server default, not the model.
    """

    def test_a_short_prompt_does_not_set_it(self):
        """Below the default, behaviour must be byte-identical to before."""
        provider = _provider()
        payload = provider._chat_payload(
            [{"role": "user", "content": "hi"}], "m", 0.2, 256, stream=False
        )
        assert "num_ctx" not in payload["options"]
        assert payload["options"] == {"temperature": 0.2, "num_predict": 256}

    def test_a_long_prompt_sets_it_above_the_default(self):
        provider = _provider()
        # ~8,000 tokens of content, the size 7b_local is actually given.
        payload = provider._chat_payload(
            [{"role": "user", "content": "token " * 8000}], "m", 0.2, 256, stream=False
        )
        assert payload["options"]["num_ctx"] > _OLLAMA_DEFAULT_NUM_CTX

    def test_it_covers_the_prompt_and_the_reply(self):
        """num_ctx has to hold BOTH, or generation truncates instead."""
        msgs = [{"role": "user", "content": "x" * 40000}]  # ~10,000 tokens
        need = _required_num_ctx(msgs, max_tokens=4096)
        assert need >= 10000 + 4096

    def test_the_estimate_over_counts_rather_than_under(self):
        """4 chars/token against a corpus measured at 5.09 (CLAUDE.md 6).
        Over-estimating costs KV cache; under-estimating silently loses context."""
        msgs = [{"role": "user", "content": "x" * 5090}]  # ~1,000 real tokens
        assert _required_num_ctx(msgs, max_tokens=0) > 1000

    def test_every_message_counts_not_just_the_last(self):
        """A chat history is part of the prompt Ollama has to hold."""
        one = _required_num_ctx([{"role": "user", "content": "x" * 20000}], 0)
        many = _required_num_ctx([{"role": "user", "content": "x" * 20000}] * 3, 0)
        assert many > one

    def test_a_missing_or_null_content_does_not_raise(self):
        assert _required_num_ctx([{"role": "user"}], 0) > 0
        assert _required_num_ctx([{"role": "user", "content": None}], 0) > 0

    def test_the_streaming_path_sets_it_too(self):
        """The bug was in the shared payload builder, so both paths inherit the
        fix - pinned because a future refactor could split them."""
        provider = _provider()
        payload = provider._chat_payload(
            [{"role": "user", "content": "token " * 8000}], "m", 0.2, 256, stream=True
        )
        assert payload["stream"] is True
        assert payload["options"]["num_ctx"] > _OLLAMA_DEFAULT_NUM_CTX
