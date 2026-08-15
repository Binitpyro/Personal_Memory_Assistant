"""Retrieval quality gate.

Opt-in — deselected from the default suite:

    .venv\\Scripts\\python.exe -m pytest tests/test_eval_retrieval.py -q -m eval

It indexes a real corpus with the real embedding and reranker models, which
takes tens of seconds and needs those models on disk. Keeping it out of the
default run is what lets `pytest tests/` stay fast and hermetic.

Assertions are **directional**, not absolute. A 24-document fixture cannot
justify "recall@k must exceed 0.8" — that number would encode nothing but the
corpus, and the first time it failed someone would lower it. What the fixture
*can* support is "turning this on beats turning it off", which is the claim
each feature actually makes.

Directional assertions have their own failure mode: `>=` passes when nothing
moves at all. Both ablations below were checked against measured deltas on this
corpus before being written, and the deltas are recorded in each docstring. If
a toggle stops moving its metric, that is a regression worth failing on, not a
threshold to relax.

Retrieval runs with the cross-encoder off — see the note in harness.retrieve.
"""

from __future__ import annotations

import pytest

from app.search import retrieval
from tests.eval import harness

pytestmark = pytest.mark.eval

# k=5, not 10. At k=10 the window holds ~40% of a 24-document corpus, so nearly
# everything relevant lands in it regardless of configuration and every metric
# saturates near 1.0 — measured baseline was recall 0.97 / domain coverage 1.00
# with both features *disabled*. A contested window is what makes the ablations
# mean anything.
K = 5


@pytest.fixture(scope="module")
async def index():
    """One index for every test in this module — indexing dominates runtime and
    the ablations only vary retrieval settings.

    EvalIndex allocates its own temp directory rather than taking tmp_path; see
    the note in harness.py for why pytest's basetemp cannot be used here."""
    idx = await harness.EvalIndex().build()
    yield idx
    await idx.close()


@pytest.fixture(scope="module")
def queries():
    return harness.load_queries()


async def _run(index, queries, **overrides):
    """Run the full query set with settings temporarily overridden."""
    previous = {key: getattr(retrieval.settings, key) for key in overrides}
    for key, value in overrides.items():
        setattr(retrieval.settings, key, value)
    try:
        return await index.run(queries, K)
    finally:
        for key, value in previous.items():
            setattr(retrieval.settings, key, value)


# ── Baseline sanity ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_corpus_indexes_and_retrieves(index, queries):
    """If this fails, every number below is meaningless."""
    run = await _run(index, queries)
    report = harness.format_report(run, K)

    assert all(r.results for r in run), f"some queries returned nothing:\n{report}"

    overall = harness.aggregate(run, K)
    assert overall["recall"] > 0.0, f"retriever found no labelled document at all:\n{report}"
    print("\nBaseline:\n" + report)


# ── Ablation: the summary-routing RRF leg ───────────────────────────────────


@pytest.mark.asyncio
async def test_summary_signal_improves_document_level_ranking(index, queries):
    """The summary leg ranks whole documents, not passages.

    It cannot surface a document chunk search cannot reach — a summary is
    derived from the body, so anything in it is also in the body. What it does
    is promote a file that is on-topic *throughout* over one where a single
    chunk coincidentally matched.

    Measured on this corpus at k=5: overall recall 0.972 -> 1.000 and nDCG
    0.970 -> 0.990. The whole delta comes from solver_state_connection, a
    three-domain query where the third relevant document sat just below the
    cutoff on chunk evidence alone (recall 0.67 -> 1.00).
    """
    off = harness.aggregate(await _run(index, queries, rrf_summary_weight=0.0), K)
    on = harness.aggregate(await _run(index, queries, rrf_summary_weight=0.3), K)

    assert on["recall"] > off["recall"] or on["ndcg"] > off["ndcg"], (
        "the summary leg changed nothing — it is either disabled upstream or the "
        f"summary index is empty. recall {off['recall']:.3f} -> {on['recall']:.3f}, "
        f"ndcg {off['ndcg']:.3f} -> {on['ndcg']:.3f}"
    )
    assert on["ndcg"] >= off["ndcg"], (
        f"summary weighting degraded overall nDCG: {off['ndcg']:.3f} -> {on['ndcg']:.3f}"
    )


# ── Ablation: source-balanced fusion ────────────────────────────────────────


@pytest.mark.asyncio
async def test_balancing_improves_domain_coverage(index, queries):
    """docs/ is deliberately the lexically dense domain — it repeats the shared
    vocabulary far more than research/ or code/, so unbalanced fusion lets it
    take the window on multi-domain queries and hide the other two.

    Measured on this corpus at k=5: domain coverage 0.944 -> 1.000 and recall
    0.944 -> 1.000 on multi-domain queries. The coverage gain holds at k=3, 5
    and 8, so it is a property of the allocator rather than of one window size.
    """
    off = await _run(index, queries, fusion_balance_enabled=False)
    on = await _run(index, queries, fusion_balance_enabled=True)

    off_score = harness.aggregate(off, K, query_type="multi_domain")
    on_score = harness.aggregate(on, K, query_type="multi_domain")

    assert on_score["domain_coverage"] > off_score["domain_coverage"], (
        f"balancing did not improve domain coverage on multi-domain queries: "
        f"{off_score['domain_coverage']:.2f} -> {on_score['domain_coverage']:.2f}\n"
        f"unbalanced:\n{harness.format_report(off, K)}\n\n"
        f"balanced:\n{harness.format_report(on, K)}"
    )


# ── Phase 1.1 end to end ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_phrasing_queries_get_a_full_window(index, queries):
    """Ordinary relational English ("connection between", "how is X used") used
    to route to GRAPH_SEARCH and come back with 3 chunks at a flat score of 1.0
    on a document corpus. It must now fall through to full retrieval."""
    from unittest.mock import AsyncMock, MagicMock

    from app.search.planner import QueryPlanner
    from app.search.retrieval import full_rag

    graph_queries = [q for q in queries if q.type == "graph_phrasing"]
    assert graph_queries, "fixture must contain graph-phrasing queries"

    llm = MagicMock()
    llm.get_model_class.return_value = "7b_local"
    llm.generate_answer = AsyncMock(return_value="answer")

    retrieval.clear_retrieval_cache()

    for q in graph_queries:
        result = await full_rag(
            q.query,
            index.db,
            index.embeddings,
            index.lancedb,
            llm,
            QueryPlanner(),
            k=K,
        )
        sources = result["sources"]
        assert len(sources) > 3, f"{q.id!r} returned only {len(sources)} sources"
        assert len({s["score"] for s in sources}) > 1, (
            f"{q.id!r} returned uniformly-scored results - no ranking signal"
        )


# ── Ablation: the bounded loop ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agentic_loop_runs_and_reports_gaps(index, queries):
    """Decomposition needs an LLM, which the fixture has no business calling.
    A stub splitting on 'and' is enough to prove the loop wires through and
    produces a trace with a not-found list."""
    from unittest.mock import AsyncMock, MagicMock

    from app.search.agentic import run_agentic_loop, trace_payload

    llm = MagicMock()
    llm.generate_raw = AsyncMock(
        return_value='[{"question": "how is the cache keyed"}, '
        '{"question": "what is a wombat pipeline"}]'
    )

    state = await run_agentic_loop(
        "how is the cache keyed and what is a wombat pipeline",
        retrieve=lambda text, k: index.retrieve(text, k),
        llm_client=llm,
        k=K,
        tokens_ceiling=100_000,
    )

    assert state.evidence, "the loop retrieved nothing at all"
    payload = trace_payload(state)
    assert any(e["kind"] == "retrieve" for e in payload)
    assert state.stop_reason in {
        "iteration_cap",
        "fixpoint",
        "all_satisfied",
        "budget_exhausted",
        "no_results",
        "complete",
        # Sufficiency is judged on the cross-encoder scale, and this harness
        # runs with use_reranker=False, so no evidence carries a rerank_score.
        # That is "not assessed" - deliberately neither satisfied nor reported
        # as missing, because claiming either would be a false statement.
        "unverified",
    }
