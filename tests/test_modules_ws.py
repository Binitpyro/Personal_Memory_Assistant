import pytest
from fastapi.testclient import TestClient

from app.main import app

TOKEN = "test-token"  # noqa: S105  # the default set in conftest.py
AUTH = {"X-Local-Access-Token": TOKEN}


def test_websocket_connection_unauthorized():
    """Verify that websocket connection without a valid token is rejected."""
    client = TestClient(app)
    # 1. No token provided
    # TestClient raises WebSocketDisconnect or RuntimeError
    with pytest.raises(Exception):  # noqa: B017, SIM117
        with client.websocket_connect("/api/modules/ws"):
            pass

    # 2. Invalid token in the header
    with pytest.raises(Exception):  # noqa: B017, SIM117
        with client.websocket_connect(
            "/api/modules/ws", headers={"X-Local-Access-Token": "invalid_token"}
        ):
            pass


def test_websocket_rejects_a_valid_token_in_the_query_string():
    """The token is header-only, as it already is on every /api/ HTTP route.

    This socket kept a ?token= fallback after that fallback was removed
    everywhere else, because it does not traverse the auth middleware in
    app/main.py and so was not covered by the change that removed it there.

    The token is *correct* here - the connection must still be refused, which
    is what distinguishes this from the invalid-token case above.
    """
    client = TestClient(app)
    with pytest.raises(Exception):  # noqa: B017, SIM117
        with client.websocket_connect(f"/api/modules/ws?token={TOKEN}"):
            pass


def test_websocket_connection_authorized():
    """Verify that websocket connection with a valid token is accepted and handles messages."""
    client = TestClient(app)

    with client.websocket_connect("/api/modules/ws", headers=AUTH) as websocket:
        websocket.send_json({"action": "ping"})
        response = websocket.receive_json()
        assert response == {"status": "pong"}

        websocket.send_json({"action": "hello", "data": "world"})
        response = websocket.receive_json()
        assert response["status"] == "error"
        assert "Unknown action 'hello'" in response["message"]


def test_websocket_new_actions():
    """Verify session.hello, corpus.stats and rate limiting."""
    client = TestClient(app)

    with client.websocket_connect("/api/modules/ws", headers=AUTH) as websocket:
        # 1. session.hello
        websocket.send_json({"action": "session.hello"})
        response = websocket.receive_json()
        assert response["status"] == "ok"
        assert response["version"] == "0.1"
        assert "capabilities" in response

        # 2. corpus.stats (should return stats)
        websocket.send_json({"action": "corpus.stats"})
        response = websocket.receive_json()
        assert response["status"] in ["ok", "error"]
        if response["status"] == "ok":
            assert "data" in response

        # 3. Rate limiting
        # Send 50 pings which should pass
        for _ in range(48):  # We already sent 2 above (hello, stats)
            websocket.send_json({"action": "ping"})
            res = websocket.receive_json()
            assert res["status"] == "pong"

        # 51st request should be rate limited
        websocket.send_json({"action": "ping"})
        res = websocket.receive_json()
        assert res["status"] == "error"
        assert "Rate limit exceeded" in res["message"]
