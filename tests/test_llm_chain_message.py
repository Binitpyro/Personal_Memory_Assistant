"""A one-provider failure names the provider; "fallback chain" needs a chain.

The UI shows this text. "All providers in fallback chain failed" for a user who
configured exactly one provider sends them looking for a chain they never built.
`LLM_UNAVAILABLE_PREFIX` stays first: callers test `startswith` on it.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.search.llm_client import LLM_UNAVAILABLE_PREFIX, LLMClient

pytestmark = pytest.mark.asyncio

CHAIN_TEXT = "All providers in fallback chain failed"


def _patched(chain: list[str]):
    return (
        patch.object(LLMClient, "_ensure_token_loaded", new=AsyncMock()),
        patch.object(
            LLMClient, "_resolve_provider_by_id", new=AsyncMock(side_effect=RuntimeError("boom"))
        ),
        patch(
            "app.search.llm_client._get_effective_fallback_chain_async",
            new=AsyncMock(return_value=chain),
        ),
        patch(
            "app.search.capability_detector.capability_detector.detect_capabilities",
            new=AsyncMock(return_value=False),
        ),
    )


async def _stream_error(client: LLMClient, **kw) -> str:
    chunks = [c async for c in client.stream_answer("q", "ctx", **kw)]
    err = [json.loads(c) for c in chunks if c.startswith('{"control":')]
    return next(c["message"] for c in err if c["control"] == "provider_error")


async def test_generate_answer_names_the_single_provider():
    p = _patched([])
    with p[0], p[1], p[2], p[3]:
        ans = await LLMClient().generate_answer("q", "c", override_provider="gemini")
    assert ans == f"{LLM_UNAVAILABLE_PREFIX}: gemini failed: boom"
    assert CHAIN_TEXT not in ans


async def test_generate_raw_names_the_single_provider():
    p = _patched([])
    with p[0], p[1], p[2], p[3]:
        ans = await LLMClient().generate_raw([{"role": "user", "content": "x"}], "ollama")
    assert ans == "LLM unavailable: ollama failed: boom"


async def test_stream_names_the_single_provider():
    p = _patched([])
    with p[0], p[1], p[2], p[3]:
        msg = await _stream_error(LLMClient(), override_provider="groq")
    assert msg == "groq failed: boom"


async def test_two_or_more_providers_keep_the_chain_wording():
    p = _patched(["gemini", "groq"])
    with p[0], p[1], p[2], p[3]:
        client = LLMClient()
        ans = await client.generate_answer("q", "c")
        raw = await client.generate_raw([{"role": "user", "content": "x"}])
        msg = await _stream_error(client)
    assert ans.startswith(LLM_UNAVAILABLE_PREFIX)
    assert f"{CHAIN_TEXT}. Last error: boom" in ans
    assert raw.startswith(LLM_UNAVAILABLE_PREFIX) and CHAIN_TEXT in raw
    assert msg == f"{CHAIN_TEXT}. Last error: boom"
