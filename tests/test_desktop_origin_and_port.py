"""W0-b: packaged-Tauri origin/CSP and sidecar port handoff (A7-02, A8-01)."""

import argparse
import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parent.parent


def _preflight(origin: str):
    # Do not enter the lifespan: preflight is answered by CORSMiddleware alone.
    return TestClient(app).options(
        "/api/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-local-access-token",
        },
    )


def test_cors_preflight_allows_http_tauri_localhost():
    r = _preflight("http://tauri.localhost")
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_cors_preflight_still_rejects_foreign_origin():
    r = _preflight("http://evil.example")
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


def test_tauri_csp_connect_src_allows_loopback_ip():
    conf = json.loads((ROOT / "frontend/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    csp = conf["app"]["security"]["csp"]
    connect = next(d for d in csp.split(";") if d.strip().startswith("connect-src"))
    assert "http://127.0.0.1:*" in connect.split()


def _run_server_port(monkeypatch, *, cli_port, env):
    spec = importlib.util.spec_from_file_location("pma_entry", ROOT / "__main__.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    for k in ("PORT", "PMA_PORT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    seen = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda **kw: seen.update(kw))
    entry.run_server(argparse.Namespace(host=None, port=cli_port, workers=1, reload=False))
    return seen["port"]


def test_run_server_honours_tauri_port_env(monkeypatch):
    assert _run_server_port(monkeypatch, cli_port=None, env={"PORT": "54321"}) == 54321


def test_run_server_cli_port_beats_env(monkeypatch):
    assert _run_server_port(monkeypatch, cli_port=9001, env={"PORT": "54321"}) == 9001
