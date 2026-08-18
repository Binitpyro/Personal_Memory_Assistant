"""The health payload reports optional-subsystem startup outcomes.

OCR, the folder watcher and the reranker each start inside a `try` whose
`except` only logs. That policy is right — a broken OCR install must not stop
the server coming up — but it left the state invisible outside the console.
"""

import pytest
from httpx import AsyncClient

from app import main, state


@pytest.fixture(autouse=True)
def restore_subsystems():
    """`state.subsystems` is process-global; leaking it poisons later tests."""
    original = {k: dict(v) for k, v in state.subsystems.items()}
    yield
    state.subsystems.clear()
    state.subsystems.update(original)


@pytest.mark.asyncio
async def test_health_reports_every_optional_subsystem(client: AsyncClient):
    response = await client.get("/api/health")
    assert response.status_code == 200

    subsystems = response.json()["subsystems"]
    assert set(subsystems) == {"ocr", "watcher", "reranker"}
    for info in subsystems.values():
        assert set(info) == {"state", "detail"}


@pytest.mark.asyncio
async def test_a_down_subsystem_does_not_change_status(client: AsyncClient):
    """`status` covers the embedder and the DB only.

    ocr_enabled defaults to False, so folding subsystems into `status` would
    report a healthy stock install as degraded. Asserted as invariance rather
    than against a literal: health() resolves the embedder via get_emb()
    directly rather than through DI, so `status` is "degraded" under the test
    client no matter what the subsystems say.
    """
    state.set_subsystem("ocr", "up")
    before = (await client.get("/api/health")).json()["status"]

    state.set_subsystem("ocr", "down", "worker venv missing")
    body = (await client.get("/api/health")).json()

    assert body["subsystems"]["ocr"]["state"] == "down"
    assert body["subsystems"]["ocr"]["detail"] == "worker venv missing"
    assert body["status"] == before


def test_the_payload_does_not_alias_live_process_state(mock_db):
    """A caller mutating the payload must not reach into `state.subsystems`.

    Asserted against `health()` in-process, not over HTTP: `response.json()`
    always hands back a freshly parsed object, so the HTTP route cannot tell an
    aliased dict from a copied one — a test written that way passes either way.
    """
    payload = main.health(mock_db)
    payload["subsystems"]["reranker"]["state"] = "tampered"

    assert state.subsystems["reranker"]["state"] != "tampered"


def test_set_subsystem_ignores_unknown_names_and_bounds_detail():
    state.set_subsystem("not-a-subsystem", "down", "should be dropped")
    assert "not-a-subsystem" not in state.subsystems

    state.set_subsystem("watcher", "down", "x" * 500)
    assert len(state.subsystems["watcher"]["detail"]) == 200
