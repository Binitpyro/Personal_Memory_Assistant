"""Regression tests for the boot-time split-brain back-fill in ``app.main``.

The back-fill had two independent defects and no coverage at all: ``conftest``
builds its client without a lifespan context, so startup never runs in tests.
"""

import asyncio

import numpy as np
import pytest

from app import main as main_mod
from app import state
from app.config import settings
from app.storage.db import DatabaseManager


class _FakeEmbedder:
    """Only the surface ``_split_brain_sync`` is allowed to touch.

    Deliberately a plain class: reading ``.model`` on it raises AttributeError,
    exactly as it did on the real ``EmbeddingService``.
    """

    def __init__(self, dim: int = 4):
        self.dim = dim
        self.embedded: list[str] = []

    def wait_until_ready(self, timeout: float = 120.0) -> bool:
        return True

    @property
    def is_ready(self) -> bool:
        return True

    def embed_texts_sync(self, texts):
        self.embedded.extend(texts)
        return np.zeros((len(texts), self.dim), dtype=np.float32)


class _FakeLanceDB:
    def __init__(self):
        self.added: list[str] = []

    def get_max_id(self, table):
        return 0

    def count_rows(self, table):
        return 0

    def get_all_ids(self, table):
        return set()

    async def add_documents(self, ids, embs, metas):
        self.added.extend(ids)

    async def delete_documents(self, ids):
        return None


async def _seed_chunks(db: DatabaseManager, n: int) -> None:
    conn = db._get_conn()
    await conn.execute(
        "INSERT INTO files (path, size, modified_at, type) VALUES ('C:/seed.txt', 1, 'now', '.txt')"
    )
    for i in range(n):
        await conn.execute(
            "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview) "
            "VALUES (1, ?, ?, ?)",
            (i, i + 1, f"chunk {i}"),
        )
    await conn.commit()


async def _unembedded_ids(db: DatabaseManager) -> list[int]:
    conn = db._get_conn()
    async with conn.execute(
        "SELECT c.id FROM chunks c "
        "LEFT JOIN chunk_embeddings ce ON ce.chunk_id = c.id "
        "WHERE ce.chunk_id IS NULL ORDER BY c.id"
    ) as cur:
        return [r[0] for r in await cur.fetchall()]


@pytest.fixture
async def sb_db(monkeypatch):
    monkeypatch.setattr(settings, "lancedb_mode", "split_brain")
    monkeypatch.setattr(main_mod, "_BACKFILL_BATCH", 5)
    db = DatabaseManager(":memory:")
    await db.connect()
    await db.init_db()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_backfill_embeds_every_chunk_across_pages(sb_db):
    """Paging a self-consuming predicate with OFFSET skipped every other page.

    23 chunks at a batch of 5 is five pages - enough for the offset to outrun
    the shrinking result set, which is what silently lost half the corpus.
    """
    await _seed_chunks(sb_db, 23)
    assert len(await _unembedded_ids(sb_db)) == 23

    emb = _FakeEmbedder()
    await main_mod._split_brain_sync(sb_db, _FakeLanceDB(), emb)

    assert await _unembedded_ids(sb_db) == []
    assert len(emb.embedded) == 23
    assert state.split_brain_sync_status == "done"


@pytest.mark.asyncio
async def test_backfill_uses_the_real_embedder_readiness_api(sb_db):
    """``EmbeddingService`` has no ``.model``; reading it raised AttributeError.

    The handler in ``_split_brain_sync`` swallows that into a generic failure,
    so the symptom was a red banner and an untouched corpus.
    """
    await _seed_chunks(sb_db, 3)

    await main_mod._split_brain_sync(sb_db, _FakeLanceDB(), _FakeEmbedder())

    assert state.split_brain_sync_status == "done"
    assert await _unembedded_ids(sb_db) == []


@pytest.mark.asyncio
async def test_backfill_stops_when_a_page_repeats(sb_db, monkeypatch):
    """Without OFFSET only the insert shrinks the set, so a no-op insert loops.

    Guarded explicitly: this runs on the boot path and must not hang it.
    """
    await _seed_chunks(sb_db, 12)

    async def _noop_insert(data, auto_commit=True):
        return None

    monkeypatch.setattr(sb_db, "insert_chunk_embeddings_bulk", _noop_insert)

    await asyncio.wait_for(
        main_mod._split_brain_sync(sb_db, _FakeLanceDB(), _FakeEmbedder()), timeout=10
    )
    assert len(await _unembedded_ids(sb_db)) == 12
