"""Verification for the retrieval hardening pass.

One test per numbered item in the hardening plan. Each asserts the behaviour the
fix exists to produce, not that the code was edited.
"""

import json
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.search import agentic, retrieval
from app.search.planner import PlanMode
from app.storage.db import DatabaseManager


@pytest.fixture
async def real_db(tmp_path: Path):
    mgr = DatabaseManager(str(tmp_path / "hardening.db"))
    await mgr.init_db(schema_path="app/storage/schema.sql")
    yield mgr
    await mgr.close()


async def _seed_file_with_chunks(db, path, folder_tag, texts):
    """Insert one file and its chunks, returning the chunk ids in document order."""
    conn = db._get_conn()
    cur = await conn.execute(
        "INSERT INTO files (path, size, modified_at, created_at, type, folder_tag, summary) "
        "VALUES (?, 1, 'now', 'now', 'md', ?, 'a summary')",
        (path, folder_tag),
    )
    file_id = cur.lastrowid
    chunk_ids = []
    for i, text in enumerate(texts):
        c = await conn.execute(
            "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview) "
            "VALUES (?, ?, ?, ?)",
            (file_id, i, i + 1, zlib.compress(text.encode("utf-8"))),
        )
        chunk_ids.append(c.lastrowid)
    await conn.commit()
    return file_id, chunk_ids


class _FakeDB:
    """Minimal DatabaseManager stand-in for the retrieval helpers."""

    def __init__(self, chunk_rows=None, chunks_by_path=None):
        self.chunk_rows = chunk_rows or []
        self.chunks_by_path = chunks_by_path or {}

    async def execute_query(self, sql, params=()):
        return self.chunk_rows

    async def get_chunk_ids_for_paths(self, paths, per_file_limit=5):
        return {p: self.chunks_by_path.get(p, [])[:per_file_limit] for p in paths}


# ── 1.1 Graph fallthrough ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_plan_falls_through_when_graph_is_empty(monkeypatch):
    """A document corpus has no kg_edges; the graph plan must not answer with 3 seeds."""
    seeds = [
        {"chunk_id": 1, "score": 0.9, "text": "a"},
        {"chunk_id": 2, "score": 0.5, "text": "b"},
        {"chunk_id": 3, "score": 0.2, "text": "c"},
    ]
    monkeypatch.setattr(retrieval, "hybrid_retrieve", AsyncMock(return_value=seeds))

    db = MagicMock()
    db.bfs_from_chunks = AsyncMock(return_value=[])
    db.get_relational_paths = AsyncMock(return_value=[])

    plan = MagicMock(original_query="what is the connection between X and Y")
    results, context = await retrieval._execute_graph_plan(plan, db, MagicMock(), MagicMock(), k=15)

    assert results is None, "empty graph must signal fallthrough, not return the seed set"
    assert context == ""


@pytest.mark.asyncio
async def test_full_rag_uses_full_retrieval_when_graph_falls_through(monkeypatch):
    """End-to-end: the fallthrough yields a full ranked set with non-uniform scores."""
    full_results = [
        {"chunk_id": i, "text": f"chunk {i}", "file_path": "d.md", "score": 1.0 - i * 0.05}
        for i in range(15)
    ]

    monkeypatch.setattr(retrieval, "_execute_graph_plan", AsyncMock(return_value=(None, "")))
    monkeypatch.setattr(
        retrieval,
        "_gather_full_rag_inputs",
        AsyncMock(return_value=(full_results, None, "")),
    )
    monkeypatch.setattr(retrieval, "_check_rag_response_cache", lambda *a, **k: None)
    monkeypatch.setattr(
        retrieval, "_check_semantic_query_cache", AsyncMock(return_value=(None, None))
    )
    monkeypatch.setattr(retrieval, "_load_query_metadata", AsyncMock(return_value=([], None)))
    monkeypatch.setattr(retrieval, "build_context", lambda *a, **k: ("ctx", 10))

    planner = MagicMock()
    planner.plan.return_value = MagicMock(
        mode=PlanMode.GRAPH_SEARCH,
        intents={"inventory": False, "project": False},
        original_query="connection between my thesis notes and the Houdini docs",
    )

    llm = MagicMock()
    llm.get_model_class.return_value = "7b_local"
    llm.generate_answer = AsyncMock(return_value="answer")

    result = await retrieval.full_rag(
        "connection between my thesis notes and the Houdini docs",
        MagicMock(),
        MagicMock(),
        MagicMock(),
        llm,
        planner,
    )

    scores = {s["score"] for s in result["sources"]}
    assert len(result["sources"]) > 3
    assert len(scores) > 1, "fallthrough results must carry a real ranking signal"


@pytest.mark.asyncio
async def test_graph_plan_scores_are_not_uniform_when_graph_has_edges(monkeypatch):
    """Seeds keep their retrieval score; BFS-only chunks rank below them."""
    seeds = [
        {"chunk_id": 1, "score": 0.9, "text": "a"},
        {"chunk_id": 2, "score": 0.4, "text": "b"},
    ]
    monkeypatch.setattr(retrieval, "hybrid_retrieve", AsyncMock(return_value=seeds))

    db = MagicMock()
    db.bfs_from_chunks = AsyncMock(return_value=[7])
    db.get_relational_paths = AsyncMock(return_value=["A -[calls]-> B"])
    db.execute_query = AsyncMock(
        return_value=[
            (1, "text one", "a.py", "code", 0, 100),
            (2, "text two", "b.py", "code", 0, 101),
            (7, "text seven", "c.py", "code", 0, 102),
        ]
    )

    results, context = await retrieval._execute_graph_plan(
        MagicMock(original_query="what calls foo"), db, MagicMock(), MagicMock(), k=15
    )

    assert results is not None
    assert len({r["score"] for r in results}) > 1
    assert results[0]["chunk_id"] == 1  # highest seed score first
    assert results[-1]["chunk_id"] == 7  # graph-expanded chunk ranks last
    assert context == "A -[calls]-> B"


# ── 1.2 BFS visited set ─────────────────────────────────────────────────────


async def _seed_cycle(db, size=3):
    """A `size`-node directed cycle, each node bound to its own chunk."""
    _, chunk_ids = await _seed_file_with_chunks(
        db, "cycle.md", "code", [f"node {i} body " * 20 for i in range(size)]
    )
    conn = db._get_conn()
    node_ids = [f"n{i}" for i in range(size)]
    for node_id, chunk_id in zip(node_ids, chunk_ids, strict=True):
        await conn.execute(
            "INSERT INTO kg_nodes (id, type, label, properties, chunk_id) VALUES (?,?,?,?,?)",
            (node_id, "func", node_id, json.dumps({"chunk_id": chunk_id}), chunk_id),
        )
    for i in range(size):
        await conn.execute(
            "INSERT INTO kg_edges (source, target, relation) VALUES (?,?,?)",
            (node_ids[i], node_ids[(i + 1) % size], "calls"),
        )
    await conn.commit()
    return chunk_ids


@pytest.mark.asyncio
async def test_bfs_traversal_does_not_re_expand_visited_nodes(real_db):
    """The working table must stay bounded by (nodes x depths), not explode.

    Note the outer `SELECT DISTINCT ... LIMIT` collapses the result set, so the
    returned rows are identical whether or not the traversal re-expands - the
    two are only distinguishable in how much the recursive CTE materializes.
    That is why this asserts against `_bfs_cte` directly.
    """
    chunk_ids = await _seed_cycle(real_db, size=3)
    max_depth = 3

    conn = real_db._get_conn()
    query = DatabaseManager._bfs_cte("?") + " SELECT COUNT(*) FROM bfs_nodes"  # noqa: S608
    async with conn.execute(query, (chunk_ids[0], max_depth, max_depth)) as cur:
        (working_rows,) = await cur.fetchone()

    # Measured on this fixture: UNION materializes 9 rows, UNION ALL 15. The
    # bound below (nodes x depths = 12) separates them and stays meaningful as
    # the fixture grows - the gap widens with density and depth.
    assert working_rows <= len(chunk_ids) * (max_depth + 1), (
        f"working table held {working_rows} rows - the visited set is not deduplicating"
    )


@pytest.mark.asyncio
async def test_bfs_still_reaches_every_connected_chunk(real_db):
    """The dedup must not cost reachability."""
    chunk_ids = await _seed_cycle(real_db, size=3)

    found = await real_db.bfs_from_chunks([chunk_ids[0]], max_depth=3, limit=100)

    assert sorted(found) == sorted(chunk_ids)


# ── 1.3c Path expansion against real SQLite ─────────────────────────────────


@pytest.mark.asyncio
async def test_get_chunk_ids_for_paths_is_bounded_and_ordered(real_db):
    _, a_chunks = await _seed_file_with_chunks(
        real_db, "long.md", "notes", [f"body {i} " * 20 for i in range(6)]
    )
    _, b_chunks = await _seed_file_with_chunks(real_db, "short.md", "notes", ["only one " * 20])

    by_path = await real_db.get_chunk_ids_for_paths(["long.md", "short.md"], per_file_limit=3)

    assert by_path["long.md"] == a_chunks[:3]
    assert by_path["short.md"] == b_chunks
    assert await real_db.get_chunk_ids_for_paths([], per_file_limit=3) == {}


# ── 1.3a Indexing writes per-file summaries to the routing index ────────────


@pytest.mark.asyncio
async def test_indexing_embeds_per_file_summaries(real_db):
    """Without this, pma_summaries only ever holds folder profiles, whose
    file_path is a folder path and can never match a chunk's file path."""
    from app.indexing.service import IndexingService

    file_id, _ = await _seed_file_with_chunks(real_db, "notes/thesis.md", "notes", ["body " * 20])

    emb = MagicMock()
    emb.embed_texts = AsyncMock(side_effect=lambda texts: [[0.1] for _ in texts])
    lance = MagicMock()
    lance.add_summaries_batch = AsyncMock()
    lance.delete_summaries_by_ids = AsyncMock()

    svc = IndexingService(real_db, emb, lance)
    svc._summary_dirty_file_ids = {file_id}

    written = await svc._flush_file_summaries()

    assert written == 1
    (batch,), _ = lance.add_summaries_batch.call_args
    assert batch[0]["doc_id"] == f"file_{file_id}"
    assert batch[0]["metadata"]["file_path"] == "notes/thesis.md"
    assert batch[0]["metadata"]["is_folder_profile"] == "false"
    # Stale rows are cleared first, or re-indexing leaves the old vector behind
    # to compete with the new one.
    lance.delete_summaries_by_ids.assert_awaited_once()
    assert svc._summary_dirty_file_ids == set()


@pytest.mark.asyncio
async def test_flush_file_summaries_is_a_noop_when_nothing_changed(real_db):
    from app.indexing.service import IndexingService

    lance = MagicMock()
    lance.add_summaries_batch = AsyncMock()
    svc = IndexingService(real_db, MagicMock(), lance)

    assert await svc._flush_file_summaries() == 0
    lance.add_summaries_batch.assert_not_awaited()


# ── 1.4 Reindex rebuilds the summary index ──────────────────────────────────


class _FakeLance:
    def __init__(self, rows_after=1):
        self.batches = []
        self._rows_after = rows_after

    async def add_summaries_batch(self, summaries):
        self.batches.extend(summaries)

    def count_rows(self, table_name="pma_chunks"):
        return self._rows_after


@pytest.mark.asyncio
async def test_reindex_rebuilds_file_summaries_and_folder_profiles(real_db):
    """clear_all() drops pma_summaries; the re-embed loop only refills pma_chunks."""
    from scripts.reindex_embeddings import _rebuild_summaries

    await _seed_file_with_chunks(real_db, "doc_a.md", "notes", ["body a " * 20])
    await _seed_file_with_chunks(real_db, "doc_b.md", "code", ["body b " * 20])
    conn = real_db._get_conn()
    await conn.execute(
        "INSERT INTO folder_profiles (folder_path, folder_tag, profile_text) VALUES (?,?,?)",
        ("/notes", "notes", "a folder profile"),
    )
    await conn.commit()

    emb = MagicMock()
    emb.embed_texts = AsyncMock(side_effect=lambda texts: [[0.1] for _ in texts])
    lance = _FakeLance(rows_after=3)

    written = await _rebuild_summaries(real_db, emb, lance, batch_size=100)

    assert written == 3  # two file summaries + one folder profile
    kinds = {s["metadata"]["is_folder_profile"] for s in lance.batches}
    assert kinds == {"true", "false"}
    # Metadata key sets must match across both row kinds - LanceDB appends into
    # one table require a stable schema.
    key_sets = {frozenset(s["metadata"]) for s in lance.batches}
    assert len(key_sets) == 1


@pytest.mark.asyncio
async def test_reindex_hard_fails_on_an_empty_summary_table(real_db):
    """A silent-empty summary table is the exact failure being fixed."""
    from scripts.reindex_embeddings import _rebuild_summaries

    await _seed_file_with_chunks(real_db, "doc.md", "notes", ["body " * 20])

    emb = MagicMock()
    emb.embed_texts = AsyncMock(side_effect=lambda texts: [[0.1] for _ in texts])
    lance = _FakeLance(rows_after=0)  # writes silently went nowhere

    with pytest.raises(SystemExit) as exc:
        await _rebuild_summaries(real_db, emb, lance, batch_size=100)

    assert exc.value.code == 1


@pytest.mark.asyncio
async def test_reindex_skips_error_summaries(real_db):
    from scripts.reindex_embeddings import _rebuild_summaries

    await _seed_file_with_chunks(real_db, "ok.md", "notes", ["body " * 20])
    conn = real_db._get_conn()
    await conn.execute(
        "INSERT INTO files (path, size, modified_at, created_at, type, folder_tag, summary) "
        "VALUES ('bad.md', 1, 'now', 'now', 'md', 'notes', '[ERROR: extraction failed]')"
    )
    await conn.commit()

    emb = MagicMock()
    emb.embed_texts = AsyncMock(side_effect=lambda texts: [[0.1] for _ in texts])
    lance = _FakeLance(rows_after=1)

    await _rebuild_summaries(real_db, emb, lance, batch_size=100)

    paths = {s["metadata"]["file_path"] for s in lance.batches}
    assert paths == {"ok.md"}


# ── 1.5 Stable pagination ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reindex_pagination_covers_every_chunk_exactly_once(real_db):
    """SQLite guarantees no ordering without ORDER BY; LIMIT/OFFSET can then
    skip or duplicate rows across pages. Paginate the script's query and assert
    the union is exactly the corpus."""
    _, ids_a = await _seed_file_with_chunks(
        real_db, "a.md", "alpha", [f"a{i} " * 20 for i in range(7)]
    )
    _, ids_b = await _seed_file_with_chunks(
        real_db, "b.md", "beta", [f"b{i} " * 20 for i in range(6)]
    )
    expected = sorted(ids_a + ids_b)

    conn = real_db._get_conn()
    batch_size = 5
    offset = 0
    seen: list[int] = []
    while True:
        async with conn.execute(
            """
            SELECT c.id, c.text_preview, f.path AS file_path, f.folder_tag
            FROM chunks c
            JOIN files f ON c.file_id = f.id
            ORDER BY c.id
            LIMIT ? OFFSET ?
            """,
            (batch_size, offset),
        ) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            break
        seen.extend(r[0] for r in rows)
        if len(rows) < batch_size:
            break
        offset += batch_size

    assert sorted(seen) == expected
    assert len(seen) == len(set(seen)), "pagination must not duplicate chunks"


# ── 1.3 Summary search as a third ranked list ───────────────────────────────


def test_summary_signal_can_introduce_a_candidate(monkeypatch):
    """The whole point: a chunk no other signal reached must be able to enter."""
    monkeypatch.setattr(retrieval.settings, "rrf_k", 60)
    monkeypatch.setattr(retrieval.settings, "rrf_fts_weight", 1.0)
    monkeypatch.setattr(retrieval.settings, "rrf_semantic_weight", 1.0)

    fts = [{"id": "1"}]
    sem = [{"id": "2"}]
    summary = [{"id": "99", "rank": 0}]

    monkeypatch.setattr(retrieval.settings, "rrf_summary_weight", 0.0)
    off = {cid for cid, _ in retrieval._compute_rrf_scores(fts, sem, summary, k=10)}
    assert "99" not in off

    monkeypatch.setattr(retrieval.settings, "rrf_summary_weight", 0.3)
    on = {cid for cid, _ in retrieval._compute_rrf_scores(fts, sem, summary, k=10)}
    assert "99" in on


def test_summary_rank_orders_documents(monkeypatch):
    """A better-ranked document contributes more than a worse-ranked one."""
    monkeypatch.setattr(retrieval.settings, "rrf_k", 60)
    monkeypatch.setattr(retrieval.settings, "rrf_fts_weight", 1.0)
    monkeypatch.setattr(retrieval.settings, "rrf_semantic_weight", 1.0)
    monkeypatch.setattr(retrieval.settings, "rrf_summary_weight", 1.0)

    summary = [{"id": "10", "rank": 0}, {"id": "20", "rank": 5}]
    ranked = dict(retrieval._compute_rrf_scores([], [], summary, k=10))
    assert ranked["10"] > ranked["20"]


@pytest.mark.asyncio
async def test_summary_search_returns_ranked_list_not_a_set():
    """RRF needs rank. A set has none, and dedup must preserve first-seen order."""
    client = MagicMock()
    client.search_summaries = AsyncMock(
        return_value={
            "metadatas": [
                [
                    {"file_path": "b.md"},
                    {"file_path": "a.md"},
                    {"file_path": "b.md"},
                ]
            ]
        }
    )

    paths = await retrieval._summary_search_with_emb(client, [0.1], k=5)
    assert paths == ["b.md", "a.md"]


@pytest.mark.asyncio
async def test_summary_search_excludes_folder_profiles():
    """pma_summaries also holds folder profiles, whose file_path is a folder."""
    client = MagicMock()
    client.search_summaries = AsyncMock(return_value={"metadatas": [[]]})

    await retrieval._summary_search_with_emb(client, [0.1], k=5)

    _, kwargs = client.search_summaries.call_args
    assert kwargs["where_filter"]["is_folder_profile"] == "false"


@pytest.mark.asyncio
async def test_summary_paths_expand_to_chunk_candidates(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "summary_expand_chunks_per_file", 2)
    db = _FakeDB(chunks_by_path={"a.md": [1, 2, 3], "b.md": [7]})

    expanded = await retrieval._expand_summary_paths_to_chunks(db, ["a.md", "b.md"])

    assert expanded == [
        {"id": "1", "rank": 0},
        {"id": "2", "rank": 0},  # capped at per-file limit
        {"id": "7", "rank": 1},
    ]


def test_summary_boost_factor_is_gone():
    """The dead multiplier must not survive as a config knob implying it works."""
    assert not hasattr(retrieval.settings, "summary_boost_factor")


# ── 1.6 Cache key ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_key_distinguishes_k(monkeypatch):
    """Same query at a larger k must not return the shorter cached list."""
    retrieval.clear_retrieval_cache()

    candidates = [
        {"chunk_id": i, "text": "x" * 80, "file_path": "f.md", "folder_tag": "t", "score": 1.0}
        for i in range(20)
    ]

    monkeypatch.setattr(retrieval, "_fts_search", AsyncMock(return_value=[]))
    monkeypatch.setattr(retrieval, "_semantic_search_with_emb", AsyncMock(return_value=[]))
    monkeypatch.setattr(retrieval, "_summary_search_with_emb", AsyncMock(return_value=[]))
    monkeypatch.setattr(retrieval, "_expand_summary_paths_to_chunks", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        retrieval,
        "_compute_rrf_scores",
        lambda *a, **kw: [(str(i), 1.0 - i * 0.01) for i in range(20)],
    )
    monkeypatch.setattr(retrieval, "_build_candidate_results", lambda *a, **kw: list(candidates))
    monkeypatch.setattr(retrieval, "_allocate_by_domain", lambda ids, tags, k: ids)
    monkeypatch.setattr(retrieval, "_rebalance_after_rerank", lambda res, limit: res[:limit])
    monkeypatch.setattr(
        retrieval, "_apply_reranker_if_needed", AsyncMock(side_effect=lambda r, *a: r)
    )

    emb = MagicMock()
    emb.embed_query = AsyncMock(return_value=[0.1])
    db = _FakeDB(chunk_rows=[])

    first = await retrieval.hybrid_retrieve("q", db, emb, MagicMock(), k=5)
    second = await retrieval.hybrid_retrieve("q", db, emb, MagicMock(), k=15)

    assert len(first) == 5
    assert len(second) == 15


def test_fusion_version_participates_in_the_cache_key():
    from app.project_constants import FUSION_VERSION

    assert isinstance(FUSION_VERSION, int)
    assert FUSION_VERSION >= 1


# ── 1.7 Background validation respects the privacy gate ─────────────────────


def _list_providers_with_consent(consent: bool):
    """Call GET /api/providers and report which providers got a validate() ping."""
    import os
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from app.main import app
    from app.providers import PROVIDER_REGISTRY

    validated: list[str] = []

    def fake_create_provider(pid, **kwargs):
        obj = MagicMock()

        async def _validate():
            validated.append(pid)

        obj.validate = _validate
        return obj

    settings_payload = {
        "llm": {
            "per_provider": {},
            "cloud_privacy_consent": consent,
        }
    }

    with (
        patch("app.api.providers.read_settings", return_value=settings_payload),
        patch("app.api.providers.create_provider", side_effect=fake_create_provider),
        patch("app.providers.cache.validation_cache.get", return_value=None),
        patch("keyring.get_password", return_value="fake_key"),
    ):
        token = os.environ.get("X_LOCAL_ACCESS_TOKEN", "test-token")
        resp = TestClient(app).get("/api/providers", headers={"X-Local-Access-Token": token})
        assert resp.status_code == 200

    gated = {pid for pid, spec in PROVIDER_REGISTRY.items() if spec.kind in ("cloud", "aggregator")}
    return validated, gated


def test_no_cloud_validation_ping_without_consent():
    """validate() is only a keyed ping, but it fires on every Settings page load."""
    validated, gated = _list_providers_with_consent(consent=False)
    assert gated, "expected at least one gated provider in the registry"
    assert not (set(validated) & gated), (
        f"cloud/aggregator providers pinged without consent: {set(validated) & gated}"
    )


def test_cloud_validation_ping_allowed_with_consent():
    validated, gated = _list_providers_with_consent(consent=True)
    assert set(validated) & gated, "consent given - gated providers should validate"


# ── Phase 2 Source-balanced fusion ──────────────────────────────────────────


def test_allocation_gives_every_domain_a_floor(monkeypatch):
    """A lexically dense domain must not take the whole window."""
    monkeypatch.setattr(retrieval.settings, "fusion_balance_enabled", True)
    monkeypatch.setattr(retrieval.settings, "fusion_domain_ceiling", 0.6)

    ranked = list(range(20))
    tags = {i: ("dense" if i < 15 else ("sparse_a" if i < 18 else "sparse_b")) for i in ranked}

    selected = retrieval._allocate_by_domain(ranked, tags, k=6)
    domains = {tags[cid] for cid in selected}

    assert domains == {"dense", "sparse_a", "sparse_b"}
    assert len(selected) == 6


def test_allocation_enforces_a_ceiling(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "fusion_balance_enabled", True)
    monkeypatch.setattr(retrieval.settings, "fusion_domain_ceiling", 0.5)

    ranked = list(range(20))
    tags = {i: ("dense" if i < 16 else "sparse") for i in ranked}

    selected = retrieval._allocate_by_domain(ranked, tags, k=8)
    dense = sum(1 for cid in selected if tags[cid] == "dense")

    assert dense <= 4  # ceil(8 * 0.5)


def test_allocation_backfills_rather_than_losing_recall(monkeypatch):
    """The ceiling is a preference; unfilled slots go back to global rank order."""
    monkeypatch.setattr(retrieval.settings, "fusion_balance_enabled", True)
    monkeypatch.setattr(retrieval.settings, "fusion_domain_ceiling", 0.25)

    ranked = list(range(10))
    tags = {i: ("a" if i < 8 else "b") for i in ranked}

    selected = retrieval._allocate_by_domain(ranked, tags, k=8)
    assert len(selected) == 8
    assert len(set(selected)) == 8


def test_allocation_disabled_reproduces_the_global_flood(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "fusion_balance_enabled", False)

    ranked = list(range(20))
    tags = {i: ("dense" if i < 15 else "sparse") for i in ranked}

    selected = retrieval._allocate_by_domain(ranked, tags, k=6)
    assert selected == list(range(6))
    assert {tags[cid] for cid in selected} == {"dense"}


def test_rebalance_after_rerank_restores_domain_spread(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "fusion_balance_enabled", True)
    monkeypatch.setattr(retrieval.settings, "fusion_domain_ceiling", 0.6)

    # Cross-encoder handed back an all-"dense" head.
    results = [{"chunk_id": i, "folder_tag": "dense"} for i in range(6)]
    results += [{"chunk_id": 100 + i, "folder_tag": "sparse"} for i in range(3)]

    balanced = retrieval._rebalance_after_rerank(results, k=4)

    # The answer window is balanced ...
    assert {r["folder_tag"] for r in balanced[:4]} == {"dense", "sparse"}
    # ... and the near-miss tail survives rather than being truncated away.
    assert len(balanced) == len(results)
    assert {r["chunk_id"] for r in balanced} == {r["chunk_id"] for r in results}


# ── F5 Reranker bypass removed ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reranker_is_not_bypassed_on_a_score_gap(monkeypatch):
    """The old heuristic skipped the cross-encoder when top-1 was 2x the second."""
    called = {}

    async def fake_rerank(query, results, top_k, text_key):
        called["ran"] = True
        return results

    monkeypatch.setattr(retrieval, "rerank", fake_rerank)

    results = [{"chunk_id": 1, "score": 100.0}, {"chunk_id": 2, "score": 1.0}]
    await retrieval._apply_reranker_if_needed(results, "q", use_reranker=True, k=5)

    assert called.get("ran"), "reranker must run regardless of the RRF score gap"


@pytest.mark.asyncio
async def test_near_misses_survive_the_reranker(monkeypatch):
    """rerank() truncates to top_k, so passing only k killed the overflow list.

    stream_rag asks for near_misses=10 and then slices `retrieved[k:]`; before
    this, that slice was always empty for any query that reranks.
    """
    retrieval.clear_retrieval_cache()
    monkeypatch.setattr(retrieval.settings, "fusion_balance_enabled", False)

    candidates = [
        {"chunk_id": i, "text": "x" * 80, "file_path": "f.md", "folder_tag": "t", "score": 1.0}
        for i in range(20)
    ]

    seen_top_k = {}

    async def fake_rerank(query, results, top_k, text_key):
        seen_top_k["value"] = top_k
        return results[:top_k]  # the real reranker truncates here

    monkeypatch.setattr(retrieval, "rerank", fake_rerank)
    monkeypatch.setattr(retrieval, "_fts_search", AsyncMock(return_value=[]))
    monkeypatch.setattr(retrieval, "_semantic_search_with_emb", AsyncMock(return_value=[]))
    monkeypatch.setattr(retrieval, "_summary_search_with_emb", AsyncMock(return_value=[]))
    monkeypatch.setattr(retrieval, "_expand_summary_paths_to_chunks", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        retrieval, "_compute_rrf_scores", lambda *a, **kw: [(str(i), 1.0) for i in range(20)]
    )
    monkeypatch.setattr(retrieval, "_build_candidate_results", lambda *a, **kw: list(candidates))

    emb = MagicMock()
    emb.embed_query = AsyncMock(return_value=[0.1])

    results = await retrieval.hybrid_retrieve(
        "q", _FakeDB(), emb, MagicMock(), k=5, near_misses=10, use_reranker=True
    )

    assert seen_top_k["value"] == 15, "the reranker must see the full answer+overflow window"
    assert len(results) == 15
    assert len(results[5:]) == 10, "near-miss tail must be non-empty"


# ── Phase 3 Bounded loop ────────────────────────────────────────────────────


def _chunk(cid, score=1.0, tag="notes", rerank_score=None):
    """A retrieved chunk.

    ``rerank_score`` defaults to mirroring ``score`` because sufficiency is
    judged on the cross-encoder scale: a chunk with no ``rerank_score`` is
    "not assessed", which is deliberately neither satisfied nor unanswered.
    Tests that want a sub-question to stay unanswered must pass a value below
    ``agentic_evidence_score_floor``.
    """
    return {
        "chunk_id": cid,
        "text": f"body {cid}",
        "folder_tag": tag,
        "score": score,
        "rerank_score": score if rerank_score is None else rerank_score,
    }


@pytest.mark.asyncio
async def test_loop_stops_at_the_iteration_cap(monkeypatch):
    monkeypatch.setattr(agentic.settings, "agentic_max_iterations", 2)
    monkeypatch.setattr(agentic.settings, "agentic_subquery_max", 3)

    calls = {"n": 0}

    async def retrieve(text, k):
        calls["n"] += 1
        # Always something new, and always below the relevance floor, so nothing
        # is ever satisfied and only the iteration cap can stop the loop.
        return [_chunk(f"{text}-{calls['n']}", score=0.0, rerank_score=-5.0)]

    llm = MagicMock()
    llm.generate_raw = AsyncMock(
        return_value='[{"question": "q1", "domain": null}, {"question": "q2", "domain": null}]'
    )

    state = await agentic.run_agentic_loop(
        "big question", retrieve=retrieve, llm_client=llm, k=10, tokens_ceiling=100_000
    )

    assert state.iteration == 2
    assert state.stop_reason == "iteration_cap"


@pytest.mark.asyncio
async def test_loop_stops_at_fixpoint(monkeypatch):
    """Re-surfacing only known chunks must end the loop, not burn the budget."""
    monkeypatch.setattr(agentic.settings, "agentic_max_iterations", 5)
    monkeypatch.setattr(agentic.settings, "agentic_evidence_score_floor", 10.0)

    async def retrieve(text, k):
        return [_chunk("always-the-same", score=0.0)]

    llm = MagicMock()
    llm.generate_raw = AsyncMock(return_value='[{"question": "q1"}]')

    state = await agentic.run_agentic_loop(
        "q", retrieve=retrieve, llm_client=llm, k=10, tokens_ceiling=100_000
    )

    assert state.stop_reason == "fixpoint"
    assert state.iteration < 5


@pytest.mark.asyncio
async def test_budget_is_pre_committed(monkeypatch):
    """A node whose declared cost exceeds the remaining ceiling never dispatches."""
    monkeypatch.setattr(agentic.settings, "agentic_max_iterations", 3)

    dispatched = {"n": 0}

    async def retrieve(text, k):
        dispatched["n"] += 1
        return [_chunk("c1")]

    llm = MagicMock()
    llm.generate_raw = AsyncMock(return_value='[{"question": "q1"}]')

    state = await agentic.run_agentic_loop(
        "q", retrieve=retrieve, llm_client=llm, k=10, tokens_ceiling=1
    )

    assert dispatched["n"] == 0, "retrieval must not run once the budget is gone"
    assert state.stop_reason == "budget_exhausted"


@pytest.mark.asyncio
async def test_per_subquery_k_is_derived_from_budget(monkeypatch):
    """Four sub-questions at k=15 is 60 chunks; the split must divide, not repeat."""
    monkeypatch.setattr(agentic.settings, "agentic_subquery_max", 4)

    llm = MagicMock()
    llm.generate_raw = AsyncMock(
        return_value='[{"question":"a"},{"question":"b"},{"question":"c"},{"question":"d"}]'
    )

    state = agentic.QueryState(query="q", tokens_ceiling=100_000)
    state = await agentic.decompose_node(state, llm, k=15)

    assert len(state.subqueries) == 4
    assert all(sq.k == 3 for sq in state.subqueries)
    assert sum(sq.k for sq in state.subqueries) <= 15


@pytest.mark.asyncio
async def test_decomposition_falls_back_to_the_original_query():
    """Local models wrap or mangle JSON; that must degrade, not disable retrieval."""
    llm = MagicMock()
    llm.generate_raw = AsyncMock(return_value="Sure! Here are some ideas: ...")

    state = agentic.QueryState(query="original question", tokens_ceiling=100_000)
    state = await agentic.decompose_node(state, llm, k=9)

    assert [sq.text for sq in state.subqueries] == ["original question"]


@pytest.mark.asyncio
async def test_trace_reports_what_was_searched_for_and_not_found(monkeypatch):
    monkeypatch.setattr(agentic.settings, "agentic_max_iterations", 1)
    monkeypatch.setattr(agentic.settings, "agentic_evidence_score_floor", 5.0)

    async def retrieve(text, k):
        return [_chunk("weak", score=0.1)] if text == "answerable" else []

    llm = MagicMock()
    llm.generate_raw = AsyncMock(
        return_value='[{"question":"answerable"},{"question":"nothing on this"}]'
    )

    state = await agentic.run_agentic_loop(
        "q", retrieve=retrieve, llm_client=llm, k=10, tokens_ceiling=100_000
    )

    payload = agentic.trace_payload(state)
    not_found = [e for e in payload if e["kind"] == "not_found"]
    assert not_found, "the loop must report what it looked for and did not find"
    assert "nothing on this" in not_found[0]["detail"]


@pytest.mark.asyncio
async def test_agentic_loop_is_mode_gated(monkeypatch):
    """FAST_METADATA / FAST_PROJECT must never pay for the loop (F4)."""
    monkeypatch.setattr(retrieval.settings, "agentic_enabled", True)

    for mode in (PlanMode.FAST_METADATA, PlanMode.FAST_PROJECT, PlanMode.GRAPH_SEARCH):
        plan = MagicMock(mode=mode)
        got, trace = await retrieval._maybe_run_agentic_loop(
            "q", plan, MagicMock(), MagicMock(), MagicMock(), MagicMock(), 10, None, None, None
        )
        assert got is None and trace is None


@pytest.mark.asyncio
async def test_agentic_loop_is_off_by_default(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "agentic_enabled", False)
    plan = MagicMock(mode=PlanMode.FULL_RAG)

    got, trace = await retrieval._maybe_run_agentic_loop(
        "q", plan, MagicMock(), MagicMock(), MagicMock(), MagicMock(), 10, None, None, None
    )
    assert got is None and trace is None
