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


# ── Embedding-model signature ────────────────────────────────────────────────
#
# The mismatch branch used to call set_system_state(current_sig) right after
# logging the warning, marking the index consistent while its vectors were
# still from the old model. Two restarts and the discrepancy had erased itself
# while semantic search kept returning results from the wrong vector space.
# There was no test on this path at all.


@pytest.fixture(autouse=True)
def restore_signature():
    original = dict(state.embedding_signature)
    yield
    state.embedding_signature.clear()
    state.embedding_signature.update(original)


class _Emb:
    is_ready = True
    has_failed = False

    def __init__(self, signature: str):
        self.model_signature = signature

    def wait_until_ready(self, _timeout):
        return True


class _Db:
    """Records writes so the test can assert the absence of one."""

    def __init__(self, stored):
        self._stored = stored
        self.writes: list[tuple[str, str]] = []

    async def get_system_state(self, key):
        return self._stored

    async def set_system_state(self, key, value):
        self.writes.append((key, value))


@pytest.mark.asyncio
async def test_mismatch_does_not_overwrite_the_stored_signature():
    db = _Db("old-signature")

    await main.check_model_signature(db, _Emb("new-signature"))

    assert db.writes == [], (
        "the mismatch overwrote the stored signature, which erases the evidence "
        "and makes the stale vectors permanent and invisible"
    )
    assert state.embedding_signature["mismatch"] is True
    assert state.embedding_signature["stored"] == "old-signature"
    assert state.embedding_signature["current"] == "new-signature"


@pytest.mark.asyncio
async def test_first_boot_records_the_signature():
    db = _Db(None)

    await main.check_model_signature(db, _Emb("sig-a"))

    assert db.writes == [("embedding_model_signature", "sig-a")]
    assert state.embedding_signature["mismatch"] is False


@pytest.mark.asyncio
async def test_matching_signature_is_not_a_fault_and_writes_nothing():
    db = _Db("sig-a")

    await main.check_model_signature(db, _Emb("sig-a"))

    assert db.writes == []
    assert state.embedding_signature["mismatch"] is False


@pytest.mark.asyncio
async def test_health_carries_the_signature_and_real_indexing_state(client: AsyncClient):
    state.set_embedding_signature("old", "new")

    body = (await client.get("/api/health")).json()

    assert body["embedding_signature"]["mismatch"] is True
    assert body["embedding_signature"]["stored"] == "old"
    # `indexing` was the string literal "idle" regardless of what was happening.
    assert "indexing" in body


@pytest.mark.asyncio
async def test_health_does_not_alias_the_signature_dict(client: AsyncClient):
    """Same discipline the subsystems map already follows."""
    state.set_embedding_signature("a", "b")
    body = (await client.get("/api/health")).json()
    body["embedding_signature"]["stored"] = "mutated"
    assert state.embedding_signature["stored"] == "a"
