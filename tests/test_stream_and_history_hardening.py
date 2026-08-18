"""Bounds on the NDJSON stream, and durability of the user-visible history row."""

import asyncio
import json

import pytest
from httpx import AsyncClient

from app.api import search as search_mod
from app.config import settings


@pytest.mark.asyncio
async def test_stream_aborts_past_the_wall_clock_cap(client: AsyncClient, monkeypatch):
    """A provider that stops producing without erroring streamed pings forever.

    is_disconnected() only resolves once the ASGI server delivers the
    disconnect, so a buffering proxy could hide a dead client for minutes.
    """
    monkeypatch.setattr(settings, "query_stream_timeout_s", 1)
    monkeypatch.setattr(search_mod, "_KEEPALIVE_SECONDS", 0.05)

    async def _never_produces(*args, **kwargs):
        await asyncio.sleep(30)
        yield {"type": "content", "text": "unreachable"}

    monkeypatch.setattr("app.search.retrieval.stream_rag", _never_produces)

    response = await client.post("/api/query/stream", json={"question": "hi"})
    assert response.status_code == 200

    records = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    errors = [r for r in records if r.get("type") == "error"]
    assert errors, records[:5]
    assert "timed out" in errors[0]["text"].lower()


@pytest.mark.asyncio
async def test_history_row_survives_a_telemetry_failure(client: AsyncClient, mock_db):
    """Telemetry is best-effort; the history row is not."""
    saved = {}

    async def _save_query(question, answer, retrieved, latency):
        saved["question"] = question
        return 42

    async def _save_telemetry(**kwargs):
        raise RuntimeError("telemetry table is wedged")

    mock_db.save_query = _save_query
    mock_db.save_telemetry = _save_telemetry

    response = await client.post("/api/query", json={"question": "remember this"})
    assert response.status_code == 200
    assert saved["question"] == "remember this"


@pytest.mark.asyncio
async def test_a_history_write_failure_does_not_cost_the_caller_its_answer(
    client: AsyncClient, mock_db
):
    """The row is written inline now, so its failure must be contained."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("disk full")

    telemetry_calls = []

    async def _save_telemetry(**kwargs):
        telemetry_calls.append(kwargs)

    mock_db.save_query = _boom
    mock_db.save_telemetry = _save_telemetry

    response = await client.post("/api/query", json={"question": "still answer me"})
    assert response.status_code == 200
    # No query_id, so nothing to hang telemetry off.
    assert telemetry_calls == []


@pytest.mark.asyncio
async def test_injected_token_script_carries_a_non_empty_nonce(client: AsyncClient):
    """`getattr(request.state, "csp_nonce", "")` degrades to nonce="".

    Reorder the middleware so the nonce is never stamped and the page still
    renders, but the token script silently stops executing and every API call
    401s. Cheap to assert, expensive to debug.
    """
    response = await client.get("/")
    assert response.status_code == 200
    assert 'nonce=""' not in response.text
    assert "__PMA_TOKEN__" in response.text

    csp = response.headers["content-security-policy"]
    assert "'nonce-'" not in csp
