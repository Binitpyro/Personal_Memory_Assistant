"""Vector-only re-embed: the repair for an embedding-model signature mismatch.

`clear_vectors_only()` existed in db.py for a long time with no route calling
it, so a user whose model changed had a permanently wrong index and no way to
fix it from the app.
"""

import pytest
from httpx import AsyncClient

from app import state
from app.indexing.reembed import ReembedError, reembed_all


@pytest.fixture(autouse=True)
def restore_signature():
    original = dict(state.embedding_signature)
    yield
    state.embedding_signature.clear()
    state.embedding_signature.update(original)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class _Conn:
    """Answers the three queries reembed_all issues, by shape."""

    def __init__(self, chunks=0, summary_files=0):
        self._chunks = chunks
        self._summary_files = summary_files

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT COUNT(*) FROM chunks"):
            return _Cursor([(self._chunks,)])
        if s.startswith("SELECT COUNT(*) FROM files WHERE summary"):
            return _Cursor([(self._summary_files,)])
        if "FROM folder_profiles" in s:
            return _Cursor([])
        return _Cursor([])


class _Db:
    def __init__(self, conn, calls=None):
        self._conn = conn
        # Shared with _Lance so ordering ACROSS the two is observable. With a
        # list each, a swapped call order still leaves both at index 0 and the
        # test proves nothing.
        self.calls: list[str] = [] if calls is None else calls

    def _get_conn(self):
        return self._conn

    async def clear_vectors_only(self):
        self.calls.append("db.clear_vectors_only")

    async def insert_chunk_embeddings_bulk(self, rows):
        self.calls.append("db.insert_bulk")


class _Lance:
    def __init__(self, summary_rows=0, calls=None):
        self.calls: list[str] = [] if calls is None else calls
        self._summary_rows = summary_rows

    async def clear_all(self):
        self.calls.append("lance.clear_all")

    async def add_documents(self, ids, embs, metas):
        self.calls.append("lance.add_documents")

    async def add_summaries_batch(self, rows):
        self.calls.append("lance.add_summaries_batch")

    def count_rows(self, table):
        return self._summary_rows


class _Emb:
    model_signature = "sig-new"

    async def embed_texts(self, texts):
        return [[0.0, 0.0] for _ in texts]


@pytest.mark.asyncio
async def test_lancedb_is_cleared_before_the_sqlite_mirror():
    """Order is load-bearing and stated in the clear_vectors_only docstring.

    It touches SQLite `chunk_embeddings` only; callers must clear LanceDB
    separately or the vector store keeps rows the mirror no longer has.
    """
    calls: list[str] = []
    db, lance = _Db(_Conn(), calls), _Lance(calls=calls)

    await reembed_all(db, _Emb(), lance)

    assert calls[:2] == ["lance.clear_all", "db.clear_vectors_only"], calls


@pytest.mark.asyncio
async def test_empty_summaries_after_rebuild_is_an_error_not_a_warning():
    """Half a rebuild is worse than none.

    lance.clear_all() drops pma_summaries too. If the summary leg is not
    repopulated, document routing contributes nothing to retrieval while
    everything still appears to work.
    """
    calls: list[str] = []
    db = _Db(_Conn(chunks=0, summary_files=3), calls)
    lance = _Lance(summary_rows=0, calls=calls)

    with pytest.raises(ReembedError, match="document-routing signal"):
        await reembed_all(db, _Emb(), lance)


@pytest.mark.asyncio
async def test_reembed_refuses_without_confirmation(client: AsyncClient):
    response = await client.post("/api/index/reembed", json={})

    assert response.status_code == 400
    assert "confirm" in response.json()["error"]


@pytest.mark.asyncio
async def test_reembed_refuses_while_one_is_already_running(client: AsyncClient):
    state.embedding_signature["reembed"] = "running"

    response = await client.post("/api/index/reembed", json={"confirm": True})

    assert response.status_code == 409
