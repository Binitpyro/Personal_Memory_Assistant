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

**Every ablation aggregates over several independent index builds.** Chunk ids
are assigned in completion order by a concurrent pipeline, so rebuilding the
same corpus reshuffles them — measured, 43 of 44 rows change id, and forcing
``index_concurrency=1`` still moves 27. Fusion ties then resolve differently,
and the summary leg manufactures ties in bulk (one equal-scored block per
ranked file). A single build therefore gives a point estimate of a random
variable: the summary ablation measured deltas of -0.028, -0.042 and -0.278 on
three builds of the identical corpus before this fixture existed.

Both arms always run against the *same* index, so each build is a valid paired
comparison; what varies between builds is the draw. Aggregating across builds is
what makes the number a measurement instead of a lottery ticket.
"""

from __future__ import annotations

import os
import statistics

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


# Independent builds per ablation. Five is enough to separate a real effect from
# tie-resolution noise on this corpus without making an opt-in suite unbearable;
# lower it when iterating locally.
BUILDS = int(os.environ.get("PMA_EVAL_BUILDS", "5"))


@pytest.fixture(scope="module")
async def indexes():
    """Several independent builds of the same corpus — see the module docstring.

    EvalIndex allocates its own temp directory rather than taking tmp_path; see
    the note in harness.py for why pytest's basetemp cannot be used here."""
    built = []
    for _ in range(BUILDS):
        built.append(await harness.EvalIndex().build())
    yield built
    for idx in built:
        await idx.close()


@pytest.fixture(scope="module")
def index(indexes):
    """First build, for tests that do not compare two configurations."""
    return indexes[0]


async def _ablate(indexes, queries, metric, *, query_type=None, **overrides):
    """One metric under one configuration, once per build.

    Returns (mean, per-build values). The caller compares means and reports the
    spread, so a difference smaller than the build-to-build noise is visible as
    such rather than being read as an effect.
    """
    values = []
    for idx in indexes:
        run = await _run(idx, queries, **overrides)
        values.append(harness.aggregate(run, K, query_type=query_type)[metric])
    return statistics.mean(values), values


def _spread(values):
    lo, hi = min(values), max(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return f"[{lo:.3f}, {hi:.3f}] sd={sd:.3f}"


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
async def test_summary_leg_does_not_regress_at_the_shipping_weight(indexes, queries):
    """The summary leg must not cost recall at whatever weight ships.

    This asserted that the leg *improves* recall, citing 0.972 -> 1.000. It had
    been failing for as long as the multi-build fixture has existed, unnoticed
    because the whole suite is `-m eval` and deselected from both gates.

    What the measurement actually showed, consistently and on every build, was
    the opposite: at `rrf_summary_weight = 0.3` recall fell to 0.889 against
    0.972 with the leg off. The signal was fine - the summary index ranks the
    right document first for a query whose answer is one file - and the fault
    was scale. See the note on `rrf_summary_weight` in app/config.py: at 0.3 a
    file's boost outweighed roughly 61 ranks of chunk-level evidence, more than
    this corpus even contains, so the leg overrode chunk search instead of
    breaking ties between its results. The default is now 0.1, where recall is
    0.981.

    **This asserts non-regression, not improvement, and that is deliberate.**
    12 queries over 24 documents cannot demonstrate that a *document-routing*
    signal helps - routing has least to offer when there are 24 candidates, and
    0.981 vs 0.972 overlaps per build. Asserting an improvement here is how the
    previous version came to encode a claim the corpus could not support. Turn
    this into a real improvement assertion when there is a corpus that can carry
    one; until then it guards against the regression that actually happened.
    """
    shipping = retrieval.settings.rrf_summary_weight

    r_off, r_off_all = await _ablate(indexes, queries, "recall", rrf_summary_weight=0.0)
    r_on, r_on_all = await _ablate(indexes, queries, "recall", rrf_summary_weight=shipping)
    n_off, n_off_all = await _ablate(indexes, queries, "ndcg", rrf_summary_weight=0.0)
    n_on, n_on_all = await _ablate(indexes, queries, "ndcg", rrf_summary_weight=shipping)

    detail = chr(10).join(
        [
            f"shipping rrf_summary_weight={shipping} over {BUILDS} builds",
            f"  recall off {r_off:.3f} {_spread(r_off_all)}",
            f"  recall on  {r_on:.3f} {_spread(r_on_all)}",
            f"  ndcg   off {n_off:.3f} {_spread(n_off_all)}",
            f"  ndcg   on  {n_on:.3f} {_spread(n_on_all)}",
        ]
    )

    # One build's worth of tie-resolution noise, not a licence to drift: at 0.3
    # the regression was 0.083, eight times this.
    tolerance = 0.01
    assert r_on >= r_off - tolerance, (
        "the summary leg costs recall at the shipping weight. It is almost "
        "certainly scale, not the signal - check rrf_summary_weight against the "
        "semantic weight and rrf_k before suspecting the summary index." + chr(10) + detail
    )
    assert n_on >= n_off - tolerance, "summary weighting degraded mean nDCG." + chr(10) + detail


async def test_summary_leg_is_reachable_and_ranks_the_right_document(indexes, queries):
    """Non-regression alone would also pass with the leg silently disabled.

    Guards the other two causes the old assertion conflated: a leg switched off
    upstream, and an empty summary index.
    """
    from app.search import retrieval as r

    index = indexes[0]
    query = next(q for q in queries if q.relevant_files)
    emb = await index.embeddings.embed_texts([query.query])
    ranked = await r._summary_search_with_emb(index.lancedb, emb[0].tolist(), k=5)

    assert ranked, "the summary index returned nothing - it is empty or not built"
    expanded = await r._expand_summary_paths_to_chunks(index.db, ranked)
    assert expanded, "summary paths expanded to no chunks"


# ── Ablation: source-balanced fusion ────────────────────────────────────────


@pytest.mark.asyncio
async def test_balancing_does_not_reduce_domain_coverage(indexes, queries):
    """docs/ is deliberately the lexically dense domain - it repeats the shared
    vocabulary far more than research/ or code/, so unbalanced fusion lets it
    take the window on multi-domain queries and hide the other two.

    This asserted `on > off`, citing coverage 0.944 -> 1.000, and it passed for
    a bad reason: the baseline it compared against was being *degraded* by
    `rrf_summary_weight = 0.3`. With that corrected to 0.05 the unbalanced
    baseline reaches 1.000 on its own, so balancing has nothing left to improve
    and `on > off` is unsatisfiable at 1.000 == 1.000.

    So this corpus can no longer demonstrate the allocator's value - it is
    saturated at k=5 with 12 queries over 24 documents in 3 domains, which is
    too few candidates for a domain allocator to matter. Asserting an
    improvement against a saturated baseline is how a test comes to depend on a
    defect elsewhere staying unfixed.

    Non-regression is what remains assertable. Restore the stronger assertion
    when there is a corpus with enough domains and documents to leave headroom.
    """
    off, off_all = await _ablate(
        indexes,
        queries,
        "domain_coverage",
        query_type="multi_domain",
        fusion_balance_enabled=False,
    )
    on, on_all = await _ablate(
        indexes,
        queries,
        "domain_coverage",
        query_type="multi_domain",
        fusion_balance_enabled=True,
    )

    detail = chr(10).join(
        [
            f"over {BUILDS} builds",
            f"  off {off:.3f} {_spread(off_all)}",
            f"  on  {on:.3f} {_spread(on_all)}",
        ]
    )
    assert on >= off, "balancing reduced domain coverage on multi-domain queries" + chr(10) + detail


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
