"""Vector-only re-embed: the repair for an embedding-model signature mismatch.

`clear_vectors_only()` existed in db.py for a long time with no route calling
it, so a user whose model changed had a permanently wrong index and no way to
fix it from the app.
"""

from pathlib import Path

import pytest
from httpx import AsyncClient

from app import state
from app.indexing import reembed as reembed_mod
from app.indexing import service as indexing_mod
from app.indexing.reembed import ReembedError, reembed_all
from app.indexing.summarizer import summary_embedding_text
from app.project_constants import build_context_prefix, chunk_embedding_text


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


class _PagedConn(_Conn):
    """Serves one page of chunk rows and one of summary rows, then runs dry."""

    def __init__(self, chunk_rows, summary_rows):
        super().__init__(chunks=len(chunk_rows), summary_files=len(summary_rows))
        self._chunk_rows, self._summary_rows_data = chunk_rows, summary_rows
        self._chunks_served = self._summaries_served = False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "FROM chunks c JOIN files f" in s:
            rows = [] if self._chunks_served else self._chunk_rows
            self._chunks_served = True
            return _Cursor(rows)
        if s.startswith("SELECT id, path, folder_tag, summary"):
            rows = [] if self._summaries_served else self._summary_rows_data
            self._summaries_served = True
            return _Cursor(rows)
        return super().execute(sql, params)


class _CapturingEmb(_Emb):
    def __init__(self):
        self.seen: list[str] = []

    async def embed_texts(self, texts, progress_callback=None):
        self.seen.extend(texts)
        return [[0.0, 0.0] for _ in texts]


class TestEmbedLoopsAgree:
    """This module's own docstring says a second copy of a rebuild loop is
    'exactly the kind of drift that produces a subtly wrong vector store'. It
    drifted anyway, on the summary leg, and nothing caught it - because nothing
    asserted on the TEXT either loop embeds, only on the calls they make."""

    PATH = "/corpus/notes.md"
    BODY = "curl noise is divergence free by construction."
    SUMMARY = "[MD: notes.md] Structure: intro > method"

    async def _run(self, emb):
        conn = _PagedConn(
            [(1, build_context_prefix(self.PATH) + self.BODY, self.PATH, "docs")],
            [(1, self.PATH, "docs", self.SUMMARY)],
        )
        calls: list[str] = []
        await reembed_all(_Db(conn, calls), emb, _Lance(summary_rows=1, calls=calls))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("keep_prefix", [True, False])
    async def test_chunk_text_matches_the_ingest_convention(self, monkeypatch, keep_prefix):
        """Both directions, because a rebuild that disagrees with ingest in
        EITHER direction leaves the store half in one convention."""
        monkeypatch.setattr(reembed_mod.settings, "embed_chunk_prefix", keep_prefix)
        emb = _CapturingEmb()
        await self._run(emb)
        expected = chunk_embedding_text(
            build_context_prefix(self.PATH) + self.BODY, self.PATH, keep_prefix
        )
        assert emb.seen[0] == expected

    @pytest.mark.asyncio
    async def test_summary_is_de_scaffolded_exactly_like_ingest(self, monkeypatch):
        """The shipped drift: this loop embedded the raw display string, so any
        user-initiated rebuild replaced every summary vector with the form
        measured at recall 0.819 against 0.972."""
        monkeypatch.setattr(reembed_mod.settings, "embed_chunk_prefix", True)
        emb = _CapturingEmb()
        await self._run(emb)
        assert summary_embedding_text(self.PATH, self.SUMMARY) in emb.seen
        assert self.SUMMARY not in emb.seen, "the display scaffold must never be embedded"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("keep_prefix", [True, False])
    async def test_ingest_text_matches_the_same_convention(self, monkeypatch, keep_prefix):
        """The OTHER side of the agreement, and it was untested.

        Reverting `_process_embed_stream_batch` to embed the raw `text_preview`
        passed the entire 1040-test suite - nothing anywhere asserted on the text
        the ingest path hands the embedder, only on the calls it makes. That is
        the same blind spot that let the summary leg drift unnoticed.
        """
        monkeypatch.setattr(indexing_mod.settings, "embed_chunk_prefix", keep_prefix)
        preview = build_context_prefix(self.PATH) + self.BODY
        emb = _CapturingEmb()

        svc = indexing_mod.IndexingService.__new__(indexing_mod.IndexingService)
        svc.embedding_service = emb
        items = [{"chunk": {"text_preview": preview}, "path": Path(self.PATH)}]
        await svc._process_embed_stream_batch(items, update_progress=False)

        assert emb.seen == [chunk_embedding_text(preview, self.PATH, keep_prefix)]
        # and the stored preview is untouched - only the vector input changed
        assert items[0]["chunk"]["text_preview"] == preview
