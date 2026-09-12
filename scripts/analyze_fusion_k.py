"""Mechanism check for Research_Paper_1: does optimal RRF k track retriever agreement?

The paper (section 4.4) claims two structural properties determine a query's
optimal fusion constant: top-1 trustworthiness, and rank-list correlation
between the two retrievers. It predicts a monotone relationship between those
and optimal k, and calls that "the first predictive account of the RRF
constant". Section 6.5 is the check; Appendix A (B10) is the no-classifier
variant that maps Kendall tau straight to k.

**Every quantitative claim in the paper is a [PH] placeholder.** This script
does not reproduce a result, it produces the first one.

Why this is cheap, and why it is three builds and not twenty-one
---------------------------------------------------------------
k enters only the fusion denominator. Given the per-leg ranked lists for a
query, every value of k is re-scorable offline with no re-indexing and no LLM.
So the whole K_GRID costs one index build, and CLAUDE.md 8.3's three-builds
discipline costs three - not three times the grid. Indexing dominates runtime;
`autoresearch.py` rebuilds per config because the knobs it sweeps change the
index, and this one does not.

`_compute_rrf_scores` is called directly rather than reimplemented. A private
import is the price of measuring the fusion that actually ships: commit
9a24443 ("the two embed loops had drifted") is what reimplementing a formula
for measurement costs.

Two deliberate departures from production
-----------------------------------------
* `recall_k` is held FIXED. Production sizes it by query word count
  (retrieval.py:598-605), and since the reward ratio depends on list length,
  letting it vary would make optimal k partly track *query length* instead of
  agreement. Fixing it isolates agreement as the independent variable.
* The reranker is off, as `tests/eval/harness.py` also leaves it off: the
  cross-encoder re-sorts whatever pool it is handed, which would put a
  downstream confound on an experiment about fusion.

Usage:
    .venv\\Scripts\\python.exe scripts/analyze_fusion_k.py --self-check
    .venv\\Scripts\\python.exe scripts/analyze_fusion_k.py --builds 3 \\
        --json-out research/fusion_k.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Spans the range the paper's Table 4 spans ([PH: 10] to [PH: 150]) plus the
# shipped 60, so the shipped value is always an arm and always a control.
#
# Brackets DOWN to 0 deliberately. The first corpus_squad run put optimal k at
# the grid's minimum for 293 of 300 rows, which is a boundary artifact: a grid
# that does not bracket the optimum cannot locate it, and an optimal-k that is
# 98% one value has no variance for a correlation to use. k=0 is the sharpest
# fusion RRF admits, 1/(rank+1).
K_GRID: tuple[int, ...] = (0, 1, 2, 3, 5, 10, 20, 30, 60, 100, 150)

# See module docstring. 50 is production's widest window (retrieval.py:598-605
# tops out at max(50, k*2)), so no query is starved relative to production.
RECALL_K = 50

# Metrics cut-off. 10 matches the paper's NDCG@10 and the project's -k default.
SCORE_AT = 10

CORPUS = REPO / "tests" / "eval" / "corpus_squad"
QUERIES = REPO / "tests" / "eval" / "queries_squad.json"

# Detection thresholds from CLAUDE.md 8.3: corpus_squad 0.025 (gemma2-2b),
# corpus_large ~0.06. A curve whose whole range sits under its corpus's
# threshold is a null no matter which arm happens to top it.
DEFAULT_THRESHOLD = 0.025


# ---------------------------------------------------------------------------
# Statistics. stdlib only - section 6's dependency policy wants a measured
# bottleneck before a dependency, and 100 queries is not one.
# ---------------------------------------------------------------------------


def _rank_with_ties(values: Sequence[float]) -> list[float]:
    """Average ranks, so Spearman stays correct when values tie.

    optimal-k takes at most len(K_GRID) distinct values, so ties are the norm
    here rather than the exception. The 1 - 6*sum(d^2)/(n(n^2-1)) shortcut
    assumes no ties and is simply wrong on this data; Pearson over
    average-ranks is the tie-corrected form.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0
        for t in range(i, j + 1):
            ranks[order[t]] = shared
        i = j + 1
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    if dx == 0.0 or dy == 0.0:
        return None
    return num / (dx * dy)


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Tie-corrected Spearman. None when undefined (n < 3, or no variance)."""
    if len(xs) != len(ys):
        raise ValueError("spearman needs equal-length inputs")
    return _pearson(_rank_with_ties(xs), _rank_with_ties(ys))


def _kendall_tau(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Kendall tau over the ids the two ranked lists share.

    The legs return different *sets*, not two orderings of one set, so tau is
    only defined on the intersection. Returns None below two shared ids, so
    the caller can report how often agreement is undefined instead of
    silently scoring it 0.0 - an undefined tau and a tau of zero are different
    findings, and if the intersection is usually empty then Appendix A's
    mechanism does not apply to this corpus at all.

    Ranks within each list are unique, so tau-a and tau-b coincide.
    """
    in_b = set(b)
    common = [x for x in a if x in in_b]
    if len(common) < 2:
        return None
    ra = {x: i for i, x in enumerate(a)}
    rb = {x: i for i, x in enumerate(b)}
    concordant = 0
    discordant = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            x, y = common[i], common[j]
            sign = (ra[x] - ra[y]) * (rb[x] - rb[y])
            if sign > 0:
                concordant += 1
            elif sign < 0:
                discordant += 1
    total = concordant + discordant
    if total == 0:
        return None
    return (concordant - discordant) / total


def _self_check() -> None:
    """Negative control for the two statistics.

    A metric nobody checked is a metric that can be silently wrong, and three
    tests in this repo have been vacuous.
    """
    # Kendall tau: identical, reversed, and one hand-computed middle case.
    assert _kendall_tau(["1", "2", "3", "4"], ["1", "2", "3", "4"]) == 1.0
    assert _kendall_tau(["1", "2", "3", "4"], ["4", "3", "2", "1"]) == -1.0
    # a=[1,2,3] vs b=[1,3,2]: (1,2) concordant, (1,3) concordant, (2,3)
    # discordant -> (2-1)/3.
    tau = _kendall_tau(["1", "2", "3"], ["1", "3", "2"])
    assert tau is not None and abs(tau - 1.0 / 3.0) < 1e-12, tau
    # Disjoint and single-overlap lists are undefined, not zero.
    assert _kendall_tau(["1", "2"], ["3", "4"]) is None
    assert _kendall_tau(["1", "2"], ["2", "9"]) is None
    # Partial overlap: common ids 2,3 appear in the same relative order.
    assert _kendall_tau(["1", "2", "3"], ["2", "3", "7"]) == 1.0

    # Spearman: perfect, reversed, and tie handling.
    s = _spearman([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
    assert s is not None and abs(s - 1.0) < 1e-12, s
    s = _spearman([1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0])
    assert s is not None and abs(s + 1.0) < 1e-12, s
    # All-tied y has no variance, so the coefficient is undefined.
    assert _spearman([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]) is None
    # Average-rank correctness: [10,10,20] -> ranks [0.5,0.5,2].
    assert _rank_with_ties([10.0, 10.0, 20.0]) == [0.5, 0.5, 2.0]
    # A tie in x against a monotone y must not score a perfect 1.0.
    s = _spearman([1.0, 1.0, 2.0, 3.0], [1.0, 2.0, 3.0, 4.0])
    assert s is not None and 0.8 < s < 1.0, s
    print("self-check OK: _kendall_tau, _spearman, _rank_with_ties")


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


async def _chunk_to_path(db: Any) -> dict[int, str]:
    """chunk id -> file path, fetched once and reused for every k and query.

    Cheaper than production's per-query hydration (retrieval.py:657-666) and
    equivalent for scoring: only the path is needed to rank documents.
    """
    rows = await db.execute_query(
        "SELECT c.id, f.path FROM chunks c JOIN files f ON c.file_id = f.id", ()
    )
    return {int(r[0]): str(r[1]) for r in rows}


async def _one_build(
    build: int, keyword_mode: str, corpus: Path, queries_file: Path
) -> list[dict[str, Any]]:
    from app.config import settings
    from app.search import retrieval
    from tests.eval import metrics
    from tests.eval.harness import EvalIndex, load_queries

    queries = load_queries(queries_file)
    idx = EvalIndex(corpus_dir=corpus)
    await idx.build()

    # Bound to locals behind one guard, the same shape EvalIndex.retrieve uses:
    # the attributes are annotated `X | None` so that importing this module
    # from scripts/ does not make mypy infer them as literally None.
    db = idx.db
    lance = idx.lancedb
    embeddings = idx.embeddings
    if db is None or lance is None or embeddings is None:
        raise RuntimeError("EvalIndex.build() must run before analysis")

    planner = None
    if keyword_mode == "planner":
        from app.search.planner import QueryPlanner

        planner = QueryPlanner()

    original_k = settings.rrf_k
    rows: list[dict[str, Any]] = []
    try:
        path_map = await _chunk_to_path(db)
        for q in queries:
            keywords = planner.plan(q.query).keywords if planner is not None else None

            fts = await retrieval._fts_search(db, q.query, RECALL_K, keywords=keywords)
            emb = await embeddings.embed_query(q.query)
            sem = await retrieval._semantic_search_with_emb(lance, emb, RECALL_K)
            summary_paths = await retrieval._summary_search_with_emb(
                lance,
                emb,
                settings.retrieval_top_k,
                where_filter={"is_folder_profile": "false"},
            )
            summary = await retrieval._expand_summary_paths_to_chunks(db, summary_paths, None)

            fts_ids = [str(r["id"]) for r in fts]
            sem_ids = [str(r["id"]) for r in sem]
            tau = _kendall_tau(fts_ids[:SCORE_AT], sem_ids[:SCORE_AT])
            overlap = len(set(fts_ids[:SCORE_AT]) & set(sem_ids[:SCORE_AT]))

            per_k: dict[str, dict[str, float]] = {}
            for k_rrf in K_GRID:
                # The real fusion, at this k. settings is a plain pydantic
                # model with no validate_assignment, and harness.build()
                # already mutates it the same way.
                settings.rrf_k = k_rrf
                fused = retrieval._compute_rrf_scores(fts, sem, summary, RECALL_K)
                ordered = [
                    {"file_path": idx.relativize(path_map.get(int(cid), ""))}
                    for cid, _ in fused
                    if int(cid) in path_map
                ]
                ranked = metrics.ranked_files(ordered)
                per_k[str(k_rrf)] = {
                    "ndcg": metrics.ndcg_at_k(ranked, q.relevant_files, SCORE_AT),
                    "recall": metrics.recall_at_k(ranked, q.relevant_files, SCORE_AT),
                }

            best = max(K_GRID, key=lambda kk: per_k[str(kk)]["ndcg"])
            rows.append(
                {
                    "build": build,
                    "query_id": q.id,
                    "tau": tau,
                    "overlap_at_10": overlap,
                    "fts_len": len(fts_ids),
                    "sem_len": len(sem_ids),
                    "summary_len": len(summary),
                    "optimal_k": best,
                    "per_k": per_k,
                }
            )
    finally:
        settings.rrf_k = original_k
        await idx.close()
    return rows


def _summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Global k curve, the mechanism correlation, and how often tau is undefined."""
    curve: dict[str, dict[str, float]] = {}
    for k_rrf in K_GRID:
        ndcgs = [r["per_k"][str(k_rrf)]["ndcg"] for r in rows]
        recalls = [r["per_k"][str(k_rrf)]["recall"] for r in rows]
        curve[str(k_rrf)] = {
            "ndcg_mean": statistics.fmean(ndcgs),
            "recall_mean": statistics.fmean(recalls),
        }

    defined = [r for r in rows if r["tau"] is not None]
    taus = [float(r["tau"]) for r in defined]
    opt = [float(r["optimal_k"]) for r in defined]
    rho = _spearman(opt, taus) if len(defined) >= 3 else None

    return {
        "n_rows": len(rows),
        "n_tau_defined": len(defined),
        "n_tau_undefined": len(rows) - len(defined),
        "mean_overlap_at_10": statistics.fmean([r["overlap_at_10"] for r in rows]),
        "tau_mean": statistics.fmean(taus) if taus else None,
        "tau_stdev": statistics.stdev(taus) if len(taus) > 1 else None,
        "spearman_optimal_k_vs_tau": rho,
        "optimal_k_distribution": {
            str(k_rrf): sum(1 for r in rows if r["optimal_k"] == k_rrf) for k_rrf in K_GRID
        },
        "global_k_curve": curve,
    }


def _fmt(value: float | None) -> str:
    return "None" if value is None else f"{value:.4f}"


def _report(summary: dict[str, Any], threshold: float, corpus_name: str) -> str:
    lines = [
        "",
        f"Global k curve on {corpus_name} (mean over all queries and builds)",
        f"{'k':>5} {'nDCG@10':>9} {'recall@10':>10}",
        "-" * 26,
    ]
    for k_rrf in K_GRID:
        c = summary["global_k_curve"][str(k_rrf)]
        mark = "  <- shipped" if k_rrf == 60 else ""
        lines.append(f"{k_rrf:>5} {c['ndcg_mean']:>9.4f} {c['recall_mean']:>10.4f}{mark}")

    # State the verdict rather than leaving the subtraction to the reader. A
    # curve whose entire range sits under the corpus's detection threshold is a
    # null, and reading its best arm as a winner is how a knob gets "tuned" on
    # noise.
    ndcgs = [summary["global_k_curve"][str(k)]["ndcg_mean"] for k in K_GRID]
    span = max(ndcgs) - min(ndcgs)
    verdict = "NULL - under the threshold" if span < threshold else "above threshold"
    lines += [
        "-" * 26,
        f"  range {span:.4f} vs threshold {threshold:.4f}  ->  {verdict}",
    ]
    best_ndcg = max(ndcgs)
    if best_ndcg > 0.95:
        lines.append(
            f"  ceiling warning: best nDCG@10 is {best_ndcg:.4f}; a saturated"
            " corpus cannot show a fusion effect"
        )

    lines += [
        "",
        "Mechanism check (Paper 1 section 4.4 / 6.5)",
        "-" * 44,
        f"  rows                          {summary['n_rows']}",
        f"  tau defined                   {summary['n_tau_defined']}",
        f"  tau UNDEFINED                 {summary['n_tau_undefined']}"
        "   (<2 shared ids in the two top-10s)",
        f"  mean FTS/semantic overlap@10  {summary['mean_overlap_at_10']:.2f} of 10",
        f"  tau mean                      {_fmt(summary['tau_mean'])}",
        f"  tau stdev                     {_fmt(summary['tau_stdev'])}",
        f"  Spearman(optimal k, tau)      {_fmt(summary['spearman_optimal_k_vs_tau'])}"
        "   <- paper predicts monotone",
        "",
        "  optimal-k distribution:",
    ]
    dist = summary["optimal_k_distribution"]
    for k_rrf in K_GRID:
        lines.append(f"    k={k_rrf:<4} {dist[str(k_rrf)]:>4}")

    # Two ways this correlation lies, both hit on the first corpus_squad run.
    total = max(1, summary["n_rows"])
    edges = (str(K_GRID[0]), str(K_GRID[-1]))
    at_edge = max(dist[e] for e in edges)
    if at_edge / total > 0.5:
        lines.append(
            f"  !! {at_edge}/{total} optimal-k values sit on a grid EDGE."
            " The grid does not bracket the optimum,"
        )
        lines.append("     so optimal-k is censored and the correlation above is not usable.")
    modal = max(dist.values())
    if modal / total > 0.9:
        lines.append(
            f"  !! optimal-k is one value for {modal}/{total} rows. A correlation"
            " against a near-constant"
        )
        lines.append("     is void, not weak - do not read it as evidence either way.")

    lines += [
        "",
        f"Read with care: {corpus_name} is one query register. This tests the",
        "MECHANISM (section 4.4), not the five-class taxonomy, and cannot",
        "validate the latter - no fixture here spans the five classes.",
        "",
    ]
    return "\n".join(lines)


async def _main_async(args: argparse.Namespace) -> int:
    corpus = Path(args.corpus) if args.corpus else CORPUS
    queries_file = Path(args.queries) if args.queries else QUERIES
    all_rows: list[dict[str, Any]] = []
    for build in range(args.builds):
        print(f"[build {build + 1}/{args.builds}] indexing {corpus.name} ...")
        rows = await _one_build(build, args.keywords, corpus, queries_file)
        all_rows.extend(rows)
        print(f"[build {build + 1}/{args.builds}] {len(rows)} queries scored")

    summary = _summarise(all_rows)
    print(_report(summary, args.threshold, corpus.name))

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "k_grid": list(K_GRID),
            "recall_k": RECALL_K,
            "score_at": SCORE_AT,
            "builds": args.builds,
            "keyword_mode": args.keywords,
            "corpus": corpus.name,
            "queries": queries_file.name,
            "threshold": args.threshold,
            "summary": summary,
            "rows": all_rows,
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Mechanism check for adaptive-k RRF.")
    p.add_argument(
        "--builds",
        type=int,
        default=3,
        help="independent index builds (default 3; see CLAUDE.md 8.3)",
    )
    p.add_argument(
        "--keywords",
        choices=("none", "planner"),
        default="none",
        help=(
            "FTS keyword source. 'none' matches tests/eval/harness.py, which is "
            "how every existing baseline here was measured; 'planner' matches "
            "production, which passes QueryPlan.keywords"
        ),
    )
    p.add_argument("--corpus", default="", help=f"corpus dir (default {CORPUS.name})")
    p.add_argument("--queries", default="", help=f"labelled queries JSON (default {QUERIES.name})")
    p.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="detection threshold for this corpus (CLAUDE.md 8.3)",
    )
    p.add_argument("--json-out", default="", help="write the full per-query rows here")
    p.add_argument(
        "--self-check",
        action="store_true",
        help="run the statistics negative control and exit (no index build)",
    )
    args = p.parse_args()

    if args.self_check:
        _self_check()
        return 0
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
