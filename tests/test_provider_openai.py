from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.providers import create_provider


@pytest.mark.asyncio
async def test_openai_provider_validate_success():
    provider = create_provider(
        "openai",
        api_key="sk-testopenaiapikey123",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
    )

    mock_response = httpx.Response(
        200,
        json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]},
        headers={"Date": "Mon, 01 Jan 2026 00:00:00 GMT"},
        request=httpx.Request("GET", "https://api.openai.com/v1/models"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        res = await provider.validate()

        assert res["ok"] is True
        assert len(res["models"]) == 2
        assert res["models"][0]["id"] == "gpt-4o"
        assert res["server_time"] == "Mon, 01 Jan 2026 00:00:00 GMT"

    await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_validate_auth_failure():
    provider = create_provider(
        "openai", api_key="sk-invalid", base_url="https://api.openai.com/v1", default_model="gpt-4o"
    )

    mock_response = httpx.Response(
        401,
        text="Unauthorized",
        headers={"Date": "Mon, 01 Jan 2026 00:00:00 GMT"},
        request=httpx.Request("GET", "https://api.openai.com/v1/models"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        res = await provider.validate()

        assert res["ok"] is False
        assert res["error_code"] == "auth_failed"
        assert "Key is invalid" in res["error"]

    await provider.close()


# ── Defect 9: a public model catalogue makes validation vacuous ───────────────
# https://integrate.api.nvidia.com/v1/models returns 200 and 82 models with no
# Authorization header, so validate() stopping at the listing reported ok=True
# for a fabricated key. Measured 2026-09-03; NIM is alone in this among the
# cloud providers here. Distinct api_keys per test because validation_cache is
# keyed on (provider, base_url, api_key).


def _nim(api_key: str):
    return create_provider(
        "nvidia_nim", api_key=api_key, base_url="https://integrate.api.nvidia.com/v1"
    )


def _models_ok():
    return httpx.Response(
        200,
        json={"data": [{"id": "meta/llama-3.3-70b-instruct"}]},
        request=httpx.Request("GET", "https://integrate.api.nvidia.com/v1/models"),
    )


def _chat(status: int, text: str = ""):
    return httpx.Response(
        status,
        text=text or "{}",
        request=httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions"),
    )


@pytest.mark.asyncio
async def test_nim_rejects_a_key_the_public_catalogue_accepts():
    """The defect itself: listing succeeds, inference is forbidden."""
    provider = _nim("nvapi-" + "d9rejects" + "x" * 24)
    with (
        patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get,
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
    ):
        get.return_value = _models_ok()
        post.return_value = _chat(403, '{"status":403,"detail":"Authorization failed"}')
        res = await provider.validate()

    assert res["ok"] is False, "a key that cannot generate must not validate"
    assert res["error_code"] == "auth_failed"
    await provider.close()


@pytest.mark.asyncio
async def test_nim_accepts_a_key_that_can_actually_generate():
    provider = _nim("nvapi-" + "d9accepts" + "x" * 24)
    with (
        patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get,
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
    ):
        get.return_value = _models_ok()
        post.return_value = _chat(200)
        res = await provider.validate()

    assert res["ok"] is True
    assert res["error_code"] is None
    await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [_chat(500), _chat(429), _chat(404), httpx.ConnectError("x")])
async def test_the_probe_never_invents_a_failure(outcome):
    """One-directional by design. Telling someone their working key is broken
    because a probe hit a 500 would be a worse bug than the one being fixed."""
    provider = _nim("nvapi-" + f"d9inconclusive{id(outcome)}"[:9] + "x" * 24)
    with (
        patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get,
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
    ):
        get.return_value = _models_ok()
        if isinstance(outcome, Exception):
            post.side_effect = outcome
        else:
            post.return_value = outcome
        res = await provider.validate()

    assert res["ok"] is True, "only 401/403 may overturn a passing validation"
    await provider.close()


@pytest.mark.asyncio
async def test_a_provider_whose_listing_authenticates_is_not_probed():
    """No extra request, and so no extra quota, where the listing already proves
    the key. anthropic, gemini, groq and openai all 401/403 unauthenticated."""
    provider = create_provider(
        "openai", api_key="sk-d9noprobe", base_url="https://api.openai.com/v1"
    )
    with (
        patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get,
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
    ):
        get.return_value = httpx.Response(
            200,
            json={"data": [{"id": "gpt-4o"}]},
            request=httpx.Request("GET", "https://api.openai.com/v1/models"),
        )
        res = await provider.validate()

    assert res["ok"] is True
    post.assert_not_called()
    await provider.close()


@pytest.mark.asyncio
async def test_the_probe_looks_past_a_retired_model():
    """NVIDIA answers 410 Gone for a retired model BEFORE checking the key - a
    deliberately invalid key gets 410 too. The first version of this probe used
    the configured default, which had itself been retired, so it learned nothing
    and the defect survived the fix. It tries further candidates now."""
    provider = _nim("nvapi-" + "d9retired" + "x" * 24)
    with (
        patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get,
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
    ):
        get.return_value = httpx.Response(
            200,
            json={"data": [{"id": "retired/model"}, {"id": "live/model"}]},
            request=httpx.Request("GET", "https://integrate.api.nvidia.com/v1/models"),
        )
        post.side_effect = [_chat(410, "Gone"), _chat(403, "Authorization failed")]
        res = await provider.validate()

    assert res["ok"] is False, "a 410 must not stop the probe short"
    assert res["error_code"] == "auth_failed"
    assert post.await_count == 2
    await provider.close()


@pytest.mark.asyncio
async def test_the_probe_stops_after_the_attempt_cap():
    """Bounded, so a provider serving 410 for everything cannot turn one GET into
    an unbounded run of completions."""
    from app.providers.openai_compat import _AUTH_PROBE_MAX_MODELS

    provider = _nim("nvapi-" + "d9capped0" + "x" * 24)
    with (
        patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get,
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
    ):
        get.return_value = httpx.Response(
            200,
            json={"data": [{"id": f"m{i}"} for i in range(10)]},
            request=httpx.Request("GET", "https://integrate.api.nvidia.com/v1/models"),
        )
        post.return_value = _chat(410, "Gone")
        res = await provider.validate()

    assert post.await_count == _AUTH_PROBE_MAX_MODELS
    assert res["ok"] is True, "all-inconclusive must leave the verdict alone"
    await provider.close()
