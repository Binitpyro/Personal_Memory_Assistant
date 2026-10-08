"""L2 b3: startup teardown, Host/CORS hardening, token compare, docs, workers.

A10-05, A5-01, A5-12, A10-06/A5-04, A5-03, A5-09.
"""

import argparse
import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("X_LOCAL_ACCESS_TOKEN", "l2-token")


def _client(host: str) -> TestClient:
    # Not entered as a context manager: no lifespan, no DB.
    return TestClient(app, headers={"Host": host})


@pytest.mark.parametrize(
    "host", ["127.0.0.1:8000", "localhost:5173", "[::1]:8000", "tauri.localhost"]
)
def test_loopback_hosts_are_served(host):
    r = _client(host).get("/")
    assert r.status_code == 200


@pytest.mark.parametrize(
    "host", ["evil.example", "evil.example:8000", "127.0.0.1.evil.example", ""]
)
def test_foreign_host_header_is_refused_before_token_injection(host):
    r = _client(host).get("/", headers={"Accept": "text/html"})
    assert r.status_code == 400
    assert "__PMA_TOKEN__" not in r.text


def test_bad_host_is_refused_before_the_token_check():
    """_HostGuard must wrap the @app.middleware token check: 400, not 401."""
    r = _client("evil.example").get("/api/index/status")
    assert r.status_code == 400


def test_cors_allows_only_the_apps_own_localhost_ports():
    def pre(origin):
        return _client("127.0.0.1:8000").options(
            "/api/health",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )

    assert pre("http://localhost:5173").status_code == 200
    assert pre("http://tauri.localhost").status_code == 200
    other = pre("http://localhost:3000")
    assert other.status_code == 400
    assert "access-control-allow-origin" not in other.headers


def test_non_ascii_token_header_is_401_not_500():
    r = _client("127.0.0.1:8000").get(
        "/api/index/status", headers=[(b"X-Local-Access-Token", b"caf\xe9")]
    )
    assert r.status_code == 401
    assert r.json() == {"error": "Unauthorized local access."}


def test_openapi_and_docs_are_off_outside_dev_mode():
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None
    c = _client("127.0.0.1:8000")
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert c.get(path).status_code == 404


def _entry():
    spec = importlib.util.spec_from_file_location("pma_entry_l2", ROOT / "__main__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_workers_above_one_are_clamped(monkeypatch):
    import uvicorn

    seen: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda **kw: seen.update(kw))
    _entry().run_server(argparse.Namespace(host=None, port=None, workers=4, reload=False))
    assert seen["workers"] == 1


def test_startup_failure_after_db_connect_exits_instead_of_hanging(tmp_path):
    bad_db = tmp_path / "corrupt.db"
    bad_db.write_bytes(b"this is not a sqlite database" * 100)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {
        **os.environ,
        "X_LOCAL_ACCESS_TOKEN": "l2-token",
        "PMA_DB_PATH": str(bad_db),
        "PMA_LANCEDB_PERSIST_DIR": str(tmp_path / "lancedb"),
        "PMA_LANCEDB_MODE": "portable",
    }
    code = f"import uvicorn; uvicorn.run('app.main:app', host='127.0.0.1', port={port})"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"process still alive after a failed startup: {exc.stderr[-500:]!r}")
    assert proc.returncode != 0
