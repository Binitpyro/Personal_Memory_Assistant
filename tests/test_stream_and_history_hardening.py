"""Bounds on the NDJSON stream, and durability of the user-visible history row."""

import asyncio
import json
import os

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


@pytest.mark.asyncio
async def test_the_ndjson_stream_opts_out_of_gzip(client: AsyncClient, monkeypatch):
    """Cheap guard on the header that keeps GZipMiddleware off this route.

    The expensive property - that bytes actually leave early - is asserted by
    the test below. This one just pins the mechanism, because it is a single
    kwarg in app/api/search.py that a later edit could drop without any content
    assertion in this file noticing.
    """

    async def _one_record(*args, **kwargs):
        yield {"type": "content", "text": "hello"}

    monkeypatch.setattr("app.search.retrieval.stream_rag", _one_record)

    response = await client.post("/api/query/stream", json={"question": "hi"})
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "identity"


@pytest.mark.asyncio
async def test_first_token_is_sent_before_the_generator_finishes(client, monkeypatch):
    """The NDJSON stream must actually stream, not arrive as one block.

    GZipMiddleware (app/main.py) compresses any streaming response whose content
    type it does not exempt, and it exempts only text/event-stream. Its
    GZipResponder writes each chunk into a GzipFile without flushing, so deflate
    holds every token until the generator closes. Content is unaffected - which
    is why every other test in this file passed throughout - so only arrival
    *order* can catch it.

    Driven against the ASGI app directly rather than through the `client`
    fixture, because httpx's ASGITransport cannot express this: it awaits the
    app to completion and accumulates the body before constructing the Response
    (httpx/_transports/asgi.py:170,185). Nothing routed through that transport
    can observe streaming at all.

    Asserted without wall-clock thresholds. The fake provider blocks on
    `release` after its first record, and only this test sets `release` - and
    only once a *decodable* record has actually been sent. Buffered, no record
    is ever sent, `release` is never set, the provider never returns, and
    wait_for trips.

    "Decodable" rather than "non-empty" for a reason the negative control
    caught: gzip emits its 10-byte header on the first write, so a non-empty
    check passes even when the payload is fully buffered.

    Negative control: drop headers={"Content-Encoding": "identity"} from the
    StreamingResponse in app/api/search.py and this fails with the buffered
    message below.
    """
    from app.main import app

    release = asyncio.Event()
    first_body = asyncio.Event()

    async def _blocks_until_its_first_record_is_sent(*args, **kwargs):
        yield {"type": "content", "text": "first"}
        await release.wait()
        yield {"type": "content", "text": "second"}

    monkeypatch.setattr("app.search.retrieval.stream_rag", _blocks_until_its_first_record_is_sent)

    payload = json.dumps({"question": "does this stream?"}).encode()
    token = os.environ.get("X_LOCAL_ACCESS_TOKEN", "test_token")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/query/stream",
        "raw_path": b"/api/query/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
            (b"x-local-access-token", token.encode()),
            # What every browser sends and cannot be told not to send.
            (b"accept-encoding", b"gzip, deflate"),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "state": {},
    }

    sent: list[dict] = []
    body_delivered = False
    still_connected = asyncio.Event()

    async def receive():
        # The body once, then block, which is what a live connection does.
        # Returning http.request twice trips BaseHTTPMiddleware's wrapped_receive,
        # and returning http.disconnect makes the endpoint abandon generation.
        nonlocal body_delivered
        if not body_delivered:
            body_delivered = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await still_connected.wait()
        return {"type": "http.disconnect"}

    streamed = bytearray()

    async def send(message):
        sent.append(message)
        if message["type"] != "http.response.body":
            return
        streamed.extend(message.get("body", b""))
        # A non-empty body message is not enough: gzip emits its 10-byte header
        # on the first write, so the naive check passes even when fully buffered.
        # The bar is a record the client could actually decode.
        head, sep, _ = bytes(streamed).partition(b"\n")
        if not sep:
            return
        try:
            json.loads(head)
        except (UnicodeDecodeError, ValueError):
            return
        first_body.set()

    task = asyncio.ensure_future(app(scope, receive, send))
    try:
        try:
            await asyncio.wait_for(first_body.wait(), timeout=10)
        except TimeoutError:  # pragma: no cover - only on regression
            pytest.fail(
                "No response body was sent while the provider was still "
                "producing: the NDJSON stream is buffered end-to-end."
            )
        finally:
            release.set()
        await asyncio.wait_for(task, timeout=10)
    finally:
        release.set()
        still_connected.set()
        task.cancel()

    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert headers.get("content-encoding") == "identity"

    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    records = [json.loads(line) for line in body.decode().splitlines() if line.strip()]
    texts = [r.get("text") for r in records if r.get("type") == "content"]
    assert texts == ["first", "second"], records
