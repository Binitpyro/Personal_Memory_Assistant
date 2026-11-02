"""L2 b4: split-brain id-set sync, .env.example, watcher health, rate-limit key,
HTML security headers, WebSocket token compare.

A2-07/A10-10/A2-08, A10-08, A10-14, A5-05, A5-13, modules.py review follow-up.
"""

import argparse
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import main as main_mod
from app import state
from app.config import Settings, settings
from app.main import app
from app.storage.db import DatabaseManager

ROOT = Path(__file__).resolve().parent.parent


# ── Split-brain sync ─────────────────────────────────────────────────


class _Embedder:
    dim = 4
    is_ready = True

    def wait_until_ready(self, timeout=120.0):
        return True

    def embed_texts_sync(self, texts):
        return np.zeros((len(texts), self.dim), dtype=np.float32)


class _StatefulLance:
    """Keeps the id set, so the sync's effect is observable (unlike a recorder)."""

    def __init__(self, ids, listing_fails=False):
        self.ids = {str(i) for i in ids}
        self.listing_fails = listing_fails
        self.added: list[str] = []

    def get_max_id(self, table):
        return max((int(i) for i in self.ids), default=0)

    def count_rows(self, table):
        return len(self.ids)

    def get_all_ids(self, table):
        return set() if self.listing_fails else set(self.ids)

    async def add_documents(self, ids, embs, metas):
        self.added.extend(ids)
        self.ids.update(ids)

    async def delete_documents(self, ids):
        self.ids.difference_update(ids)


@pytest.fixture
async def sb_db(monkeypatch):
    # .env on the dev box sets split_brain; be explicit either way.
    monkeypatch.setattr(settings, "lancedb_mode", "split_brain")
    db = DatabaseManager(":memory:")
    await db.connect()
    await db.init_db()
    yield db
    await db.close()


async def _seed(db, n, embedded):
    conn = db._get_conn()
    await conn.execute(
        "INSERT INTO files (path, size, modified_at, type) VALUES ('C:/s.txt', 1, 'now', '.txt')"
    )
    for i in range(1, n + 1):
        await conn.execute(
            "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview) "
            "VALUES (1, ?, ?, ?)",
            (i, i + 1, f"chunk {i}"),
        )
    blob = np.zeros(4, dtype=np.float16).tobytes()
    for i in embedded:
        await conn.execute(
            "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)", (i, blob)
        )
    await conn.commit()


async def _embedded_ids(db):
    async with db._get_conn().execute("SELECT chunk_id FROM chunk_embeddings") as cur:
        return {r[0] for r in await cur.fetchall()}


@pytest.mark.asyncio
async def test_sync_fills_holes_below_the_lancedb_max_id(sb_db):
    """A2-07(a)/A10-10: LanceDB [1,2,3,8,9,10] used to stay that way, status 'done'."""
    await _seed(sb_db, 10, range(1, 11))
    lance = _StatefulLance([1, 2, 3, 8, 9, 10])
    await main_mod._split_brain_sync(sb_db, lance, _Embedder())
    assert lance.ids == {str(i) for i in range(1, 11)}
    assert state.split_brain_sync_status == "done"


@pytest.mark.asyncio
async def test_sync_resumes_a_partial_backfill(sb_db):
    """A2-07(b): back-fill killed after 5/10 left chunks 6-10 un-embedded forever."""
    await _seed(sb_db, 10, range(1, 6))
    lance = _StatefulLance(range(1, 6))
    await main_mod._split_brain_sync(sb_db, lance, _Embedder())
    assert await _embedded_ids(sb_db) == set(range(1, 11))
    assert lance.ids == {str(i) for i in range(1, 11)}


@pytest.mark.asyncio
async def test_ghost_removed_even_when_row_counts_match(sb_db):
    """A2-08: ghost 99 + missing 4 -> counts 10 == 10 skipped reconciliation."""
    await _seed(sb_db, 10, range(1, 11))
    lance = _StatefulLance([1, 2, 3, 5, 6, 7, 8, 9, 10, 99])
    assert lance.count_rows("pma_chunks") == 10
    await main_mod._split_brain_sync(sb_db, lance, _Embedder())
    assert lance.ids == {str(i) for i in range(1, 11)}


@pytest.mark.asyncio
async def test_failed_id_listing_does_not_duplicate_every_vector(sb_db):
    """get_all_ids swallows errors as set(); that must not read as 'LanceDB is empty'."""
    await _seed(sb_db, 5, range(1, 6))
    lance = _StatefulLance(range(1, 6), listing_fails=True)
    await main_mod._split_brain_sync(sb_db, lance, _Embedder())
    assert lance.added == []
    assert state.split_brain_sync_status == "error"


# ── A10-08 ───────────────────────────────────────────────────────────


def test_copied_env_example_changes_no_default(monkeypatch):
    import os

    for k in [k for k in os.environ if k.startswith("PMA_")]:
        monkeypatch.delenv(k)
    defaults = Settings(_env_file=None)
    copied = Settings(_env_file=ROOT / ".env.example")
    diff = {
        f: (getattr(defaults, f), getattr(copied, f))
        for f in Settings.model_fields
        if getattr(defaults, f) != getattr(copied, f)
    }
    assert diff == {}
    assert (copied.chunk_size, copied.chunk_overlap) == (1024, 102)


# ── A10-14 ───────────────────────────────────────────────────────────


async def _run_lifespan(monkeypatch, enabled, order=None):
    """Drive `_lifespan` with every collaborator patched; return the watcher state."""
    monkeypatch.setattr(settings, "watcher_enabled", enabled)
    monkeypatch.setattr(settings, "lancedb_mode", "portable")
    monkeypatch.setenv("X_LOCAL_ACCESS_TOKEN", "l2-token")
    saved = {k: dict(v) for k, v in state.subsystems.items()}
    order = [] if order is None else order

    async def _ocr_stop():
        order.append("ocr")

    async def _index_stop():
        order.append("index")

    db = DatabaseManager(":memory:")
    await db.connect()
    ocr = MagicMock()
    ocr.start = AsyncMock()
    ocr.stop = _ocr_stop
    try:
        with (
            patch("app.api.deps.get_db", AsyncMock(return_value=db)),
            patch.object(db, "init_db", AsyncMock()),
            patch("app.api.deps.get_emb", MagicMock()),
            patch("app.api.deps.get_lancedb", MagicMock(return_value=MagicMock())),
            patch("app.api.deps.get_ocr", AsyncMock(return_value=ocr)),
            patch("app.ocr.settings.load_persisted_state"),
            patch.object(main_mod, "_log_admin_status"),
            patch.object(main_mod, "_split_brain_sync", AsyncMock()),
            patch.object(main_mod, "check_model_signature", AsyncMock()),
            patch.object(main_mod, "_bg_auto_vacuum", AsyncMock()),
            patch("app.search.reranker.preload_reranker"),
            patch(
                "app.search.reranker.reranker_status",
                return_value={"available": False, "reason": "t"},
            ),
            patch("app.indexing.watcher.FolderWatcher.stop", AsyncMock()),
            patch("app.indexing.service.shutdown_executors"),
            patch("app.api.indexing.stop_indexing_for_shutdown", _index_stop),
        ):
            async with main_mod._lifespan(app):
                return state.subsystems["watcher"]["state"]
    finally:
        state.subsystems.clear()
        state.subsystems.update(saved)


@pytest.mark.asyncio
@pytest.mark.parametrize(("enabled", "expected"), [(False, "disabled"), (True, "up")])
async def test_health_watcher_state_follows_watcher_enabled(monkeypatch, enabled, expected):
    assert await _run_lifespan(monkeypatch, enabled) == expected


@pytest.mark.asyncio
async def test_shutdown_stops_the_index_run_before_the_ocr_manager(monkeypatch):
    """The index run's tail kicks OCR, so OCR must outlive it."""
    order: list[str] = []
    await _run_lifespan(monkeypatch, False, order)
    assert order == ["index", "ocr"]


# ── A5-05 ────────────────────────────────────────────────────────────


def test_server_ignores_proxy_headers_so_rate_limit_key_is_the_socket_peer(monkeypatch):
    import uvicorn

    seen: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda **kw: seen.update(kw))
    spec = importlib.util.spec_from_file_location("pma_entry_l2b4", ROOT / "__main__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for reload in (False, True):
        mod.run_server(argparse.Namespace(host=None, port=None, workers=1, reload=reload))
        assert seen["proxy_headers"] is False


# ── A5-13 ────────────────────────────────────────────────────────────


@pytest.fixture
def react_dir(tmp_path, monkeypatch):
    d = tmp_path / "react"
    d.mkdir()
    (d / "index.html").write_text("<html><head></head><body>spa</body></html>", encoding="utf-8")
    (d / "other.html").write_text("<html><body>other</body></html>", encoding="utf-8")
    monkeypatch.setattr(main_mod, "_REACT_DIR", d)
    monkeypatch.setattr(main_mod, "_REACT_INDEX", d / "index.html")
    monkeypatch.setenv("X_LOCAL_ACCESS_TOKEN", "l2-token")
    # TestClient's peer is "testclient"; token injection is loopback-only.
    monkeypatch.setattr(main_mod, "_is_loopback", lambda request: True)
    return d


@pytest.mark.parametrize(
    ("path", "token_injected"),
    [
        ("/", True),
        ("/search", True),  # deep link -> catch-all -> _serve_index
        ("/other.html", False),  # catch-all FileResponse branch
        ("/static/react/index.html", False),  # StaticFiles mount
    ],
)
def test_every_html_route_carries_csp_and_security_headers(react_dir, path, token_injected):
    r = TestClient(app, headers={"Host": "127.0.0.1:8000"}).get(
        path, headers={"Accept": "text/html"}
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self' 'nonce-" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert ("__PMA_TOKEN__" in r.text) is token_injected
    if token_injected:  # the inline script's nonce is the one in the header
        nonce = csp.split("'nonce-")[1].split("'")[0]
        assert f'nonce="{nonce}"' in r.text


# ── modules.py WebSocket handshake ───────────────────────────────────


@pytest.mark.asyncio
async def test_websocket_non_ascii_token_is_refused_not_crashed(monkeypatch):
    """Driven as raw ASGI: TestClient cannot send a non-ASCII header value."""
    monkeypatch.setenv("X_LOCAL_ACCESS_TOKEN", "l2-token")
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": "/api/modules/ws",
        "raw_path": b"/api/modules/ws",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1:8000"), (b"x-local-access-token", b"caf\xe9")],
        "client": ("127.0.0.1", 5000),
        "server": ("127.0.0.1", 8000),
        "subprotocols": [],
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    assert sent and sent[0]["type"] == "websocket.close" and sent[0]["code"] == 1008
