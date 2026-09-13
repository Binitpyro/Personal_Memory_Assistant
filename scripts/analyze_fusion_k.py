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
import shutil
import statistics
import sys
import tempfile
import zlib
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

    # _winners: a flat curve has no optimum, and must not read as the grid floor.
    def per_k(best: dict[int, float]) -> dict[str, dict[str, float]]:
        return {str(k): {"ndcg": best.get(k, 0.5)} for k in K_GRID}

    assert _winners(per_k({}), "ndcg") == list(K_GRID)
    assert _winners(per_k({150: 1.0}), "ndcg") == [150]
    assert _winners(per_k({3: 1.0, 5: 1.0}), "ndcg") == [3, 5]

    # _class_analysis: two classes wanting opposite k. A per-class k must win
    # held-out, and each class must be assigned its own k.
    def rows_for(cls: str, best_k: int, n: int = 40) -> list[dict[str, Any]]:
        return [
            {
                "build": 0,
                "query_id": f"{cls}-{i}",
                "type": cls,
                "tau": None,
                "per_k": per_k({best_k: 1.0}),
            }
            for i in range(n)
        ]

    opposite = rows_for("code_identifier", 0) + rows_for("navigation", 150)
    ca = _class_analysis(opposite)
    macro = ca["per_build"][0]["class_macro"]
    assert macro["n_held_out"] == len(opposite), macro["n_held_out"]  # every row tested once
    assert abs(macro["gain"] - 0.25) < 1e-9, macro["gain"]
    assert macro["selected_k"] == {"code_identifier": 0, "navigation": 150}, macro["selected_k"]
    assert ca["verdict"].startswith("PASS"), ca["verdict"]
    # Negative control: two classes wanting the SAME k - nothing to adapt to.
    same = _class_analysis(rows_for("code_identifier", 5) + rows_for("navigation", 5))
    assert same["per_build"][0]["class_macro"]["gain"] == 0.0
    assert same["verdict"].startswith("FAIL"), same["verdict"]
    print("self-check OK: _kendall_tau, _spearman, _rank_with_ties, _winners, _class_analysis")


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _compose_corpus(corpora: Sequence[Path], dest: Path) -> dict[str, int]:
    """Copy several corpora's domain directories into one root.

    The point is to kill saturation. corpus_squad is 48 documents with one
    relevant answer each, so recall@10 is 1.0000 at every k and no fusion knob
    can move it (measured: whole-k-curve range 0.0079 against a 0.025
    threshold). The same 100 queries against thousands of competing documents
    from other registers stop being trivial, which is the condition CLAUDE.md
    8.3 and Research_Paper_1 both actually care about.

    Labels transfer untouched because every fixture already namespaces its
    `relevant_files` by a domain directory and the five in use are disjoint -
    docs, notes, research, squad, scifact. `EvalIndex.relativize` strips the
    corpus root, so `scifact/x.txt` stays `scifact/x.txt`.

    Composed into a temp dir on purpose: this is ~9 MB of duplicated fixture
    and committing it would put a second copy of SciFact in the repo.
    """
    counts: dict[str, int] = {}
    for corpus in corpora:
        for domain in sorted(p for p in corpus.iterdir() if p.is_dir()):
            target = dest / domain.name
            if target.exists():
                raise SystemExit(
                    f"domain {domain.name!r} appears in two corpora; labels would collide"
                )
            shutil.copytree(domain, target)
            counts[domain.name] = sum(1 for _ in target.rglob("*") if _.is_file())
    return counts


async def _chunk_maps(db: Any) -> tuple[dict[int, str], dict[int, str]]:
    """chunk id -> (file path, folder_tag), fetched once for every k and query.

    Cheaper than production's per-query hydration (retrieval.py:657-666) and
    equivalent for scoring: the path ranks documents, the tag feeds domain
    allocation. Production reads both off the same join.
    """
    rows = await db.execute_query(
        "SELECT c.id, f.path, f.folder_tag FROM chunks c JOIN files f ON c.file_id = f.id", ()
    )
    paths = {int(r[0]): str(r[1]) for r in rows}
    tags = {int(r[0]): str(r[2] or "") for r in rows}
    return paths, tags


async def _one_build(
    build: int, keyword_mode: str, corpus: Path, queries_files: Sequence[Path]
) -> list[dict[str, Any]]:
    from app.config import settings
    from app.search import retrieval
    from tests.eval import metrics
    from tests.eval.harness import EvalIndex, load_queries

    queries = []
    seen_ids: set[str] = set()
    for qf in queries_files:
        for q in load_queries(qf):
            if q.id in seen_ids:
                raise SystemExit(f"duplicate query id {q.id!r} across query files")
            seen_ids.add(q.id)
            queries.append(q)

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
        path_map, tag_map = await _chunk_maps(db)
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
                ids = [int(cid) for cid, _ in fused if int(cid) in path_map]
                # Production also balances across domains before the reranker
                # (retrieval.py:670-674). It is a no-op on a single-domain
                # corpus, which is why SciFact and SQuAD did not need it - but
                # on a composed corpus one domain has 5183 documents and
                # another has 8, which is the exact case _allocate_by_domain
                # exists for. Measuring only the unbalanced ranking there would
                # report a number production never computes.
                balanced = retrieval._allocate_by_domain(ids, tag_map, RECALL_K)

                scored: dict[str, float] = {}
                for suffix, chunk_ids in (("", ids), ("_bal", balanced)):
                    ranked = metrics.ranked_files(
                        [{"file_path": idx.relativize(path_map[c])} for c in chunk_ids]
                    )
                    scored[f"ndcg{suffix}"] = metrics.ndcg_at_k(ranked, q.relevant_files, SCORE_AT)
                    scored[f"recall{suffix}"] = metrics.recall_at_k(
                        ranked, q.relevant_files, SCORE_AT
                    )
                per_k[str(k_rrf)] = scored

            # No optimal k is stored: it is derived from per_k by _winners, which
            # keeps ties as ties. An argmax stored here once reported k=0 for
            # every flat curve (CLAUDE.md 8.3a retraction).
            rows.append(
                {
                    "build": build,
                    "query_id": q.id,
                    "type": q.type,
                    "note": q.note,
                    "tau": tau,
                    "overlap_at_10": overlap,
                    "fts_len": len(fts_ids),
                    "sem_len": len(sem_ids),
                    "summary_len": len(summary),
                    "per_k": per_k,
                }
            )
    finally:
        settings.rrf_k = original_k
        await idx.close()
    return rows


def _metric_key(rows: Sequence[dict[str, Any]]) -> str:
    """Domain-balanced nDCG when the rows carry it - it is what production ranks."""
    return "ndcg_bal" if "ndcg_bal" in rows[0]["per_k"][str(K_GRID[0])] else "ndcg"


def _winners(per_k: dict[str, dict[str, float]], key: str) -> list[int]:
    """Every k that reaches the row's best score, ascending.

    A flat curve returns the whole grid. Picking one element instead is what
    produced "optimal k pinned at the floor for 1066/1236 rows": max() returns
    the first maximum of an ascending grid, so indifference read as k=0.
    """
    top = max(per_k[str(k)][key] for k in K_GRID)
    return [k for k in K_GRID if per_k[str(k)][key] == top]


# Query class for the pre-registered test (CLAUDE.md 8.3a). squad and scifact
# are both semantic-prose; the tests/eval/corpus types are not taxonomy classes.
_CLASS_OF_TYPE = {
    "squad": "semantic_prose",
    "scifact": "semantic_prose",
    "code_identifier": "code_identifier",
    "navigation": "navigation",
    "structured_lookup": "structured_lookup",
}
DECISION_CLASSES = ("code_identifier", "navigation", "structured_lookup", "semantic_prose")
CV_FOLDS = 5
PASS_MIN_GAIN = 0.025
PASS_MIN_K = 20


def _source(row: dict[str, Any]) -> str:
    """Fixture a query came from, by id prefix (squad-, scifact-, qtcode-, ...)."""
    qid = str(row["query_id"])
    return qid.split("-", 1)[0] if "-" in qid else "other"


def _query_class(row: dict[str, Any]) -> str:
    # Rows written before `type` was recorded fall back to their id prefix.
    return _CLASS_OF_TYPE.get(str(row.get("type") or _source(row)), "other")


def _fold(row: dict[str, Any]) -> int:
    # By query id, so a query sits in the same fold in every build.
    return zlib.crc32(str(row["query_id"]).encode()) % CV_FOLDS


def _mean_at(rows: Sequence[dict[str, Any]], k: int, key: str) -> float:
    return statistics.fmean(r["per_k"][str(k)][key] for r in rows)


def _best_k(rows: Sequence[dict[str, Any]], key: str, group_of: Any, macro: bool) -> int:
    """Fixed k maximising the micro mean, or the mean of per-group means.

    Ties go to the smallest k, which is conservative for the PASS rule's
    "some class wants k >= 20" clause.
    """

    def objective(k: int) -> float:
        if not macro:
            return _mean_at(rows, k, key)
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            groups.setdefault(group_of(r), []).append(r)
        return statistics.fmean(_mean_at(g, k, key) for g in groups.values())

    return max(K_GRID, key=objective)


def _cv_conditional(
    rows: Sequence[dict[str, Any]], key: str, group_of: Any, macro: bool
) -> dict[str, Any]:
    """Held-out score of a per-group k against one fixed k, for ONE build.

    Each fold picks a k per group, and one global k, on the other folds, and
    both are scored on the held-out fold. The global k is chosen by the same
    objective (micro or macro) it is scored on, so the per-group arm gets no
    free advantage from unequal group sizes. Groups unseen in training fall
    back to the global k.
    """
    # (group, (per-group k, global k, k=60)) per held-out row.
    held: list[tuple[str, tuple[float, float, float]]] = []
    for f in range(CV_FOLDS):
        train = [r for r in rows if _fold(r) != f]
        test = [r for r in rows if _fold(r) == f]
        if not train or not test:
            continue
        global_k = _best_k(train, key, group_of, macro)
        by_group: dict[str, list[dict[str, Any]]] = {}
        for r in train:
            by_group.setdefault(group_of(r), []).append(r)
        pick = {g: _best_k(rs, key, group_of, False) for g, rs in by_group.items()}
        for r in test:
            g = group_of(r)
            held.append(
                (
                    g,
                    (
                        r["per_k"][str(pick.get(g, global_k))][key],
                        r["per_k"][str(global_k)][key],
                        r["per_k"]["60"][key],
                    ),
                )
            )

    def score(idx: int) -> float:
        if not macro:
            return statistics.fmean(h[1][idx] for h in held)
        groups = sorted({h[0] for h in held})
        return statistics.fmean(
            statistics.fmean(h[1][idx] for h in held if h[0] == g) for g in groups
        )

    by_group_all: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_group_all.setdefault(group_of(r), []).append(r)
    return {
        "n_held_out": len(held),
        "adaptive": score(0),
        "global_fixed": score(1),
        "k60": score(2),
        "gain": score(0) - score(1),
        # The k each group would ship with: chosen on all of this build's rows.
        # The folds above measure how well that choice generalises.
        "selected_k": {g: _best_k(rs, key, group_of, False) for g, rs in by_group_all.items()},
    }


def _class_analysis(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The pre-registered PASS/FAIL test, plus the oracle ceilings it sits under."""
    key = _metric_key(rows)
    builds = sorted({r["build"] for r in rows})
    decision = [r for r in rows if _query_class(r) in DECISION_CLASSES]
    present = sorted({_query_class(r) for r in decision})

    per_build: list[dict[str, Any]] = []
    for b in builds:
        br = [r for r in rows if r["build"] == b]
        dr = [r for r in decision if r["build"] == b]
        entry: dict[str, Any] = {"build": b}
        if len(present) >= 2:
            entry["class_macro"] = _cv_conditional(dr, key, _query_class, macro=True)
            entry["class_micro"] = _cv_conditional(dr, key, _query_class, macro=False)
        # By fixture: separates squad from scifact inside semantic_prose, and is
        # the class-vs-domain check. On the original composed corpus this is the
        # squad / scifact / corpus split measured inline on 2026-09-13.
        entry["source_micro"] = _cv_conditional(br, key, _source, macro=False)
        best = _best_k(br, key, _source, False)
        fixed = _mean_at(br, best, key)
        entry["best_fixed_k"] = best
        entry["oracle_gain"] = (
            statistics.fmean(max(r["per_k"][str(k)][key] for k in K_GRID) for r in br) - fixed
        )
        base3 = _mean_at(br, 3, key)
        entry["two_arm_gain"] = {
            big: statistics.fmean(max(r["per_k"]["3"][key], r["per_k"][str(big)][key]) for r in br)
            - base3
            for big in (60, 150)
        }
        per_build.append(entry)

    curves = {
        c: {str(k): _mean_at([r for r in rows if _query_class(r) == c], k, key) for k in K_GRID}
        for c in sorted({_query_class(r) for r in rows})
    }
    # Within-class subgroups (fixture template, else source): a class whose
    # optimal k is really its template's is risk 1 in the plan.
    subgroups: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = f"{_query_class(r)}/{r.get('note') or _source(r)}"
        subgroups.setdefault(name, {"rows": []})["rows"].append(r)
    for s in subgroups.values():
        rs = s.pop("rows")
        s["n"] = len(rs)
        s["best_k"] = _best_k(rs, key, _source, False)
        s["at_best"] = _mean_at(rs, s["best_k"], key)
        s["at_3"] = _mean_at(rs, 3, key)
        s["at_60"] = _mean_at(rs, 60, key)

    verdict = "N/A - fewer than two decision classes present"
    if len(present) >= 2:
        floor = min(e["class_macro"]["gain"] for e in per_build)
        wants_large = [
            c
            for c in present
            if all(e["class_macro"]["selected_k"].get(c, 0) >= PASS_MIN_K for e in per_build)
        ]
        passed = floor >= PASS_MIN_GAIN and bool(wants_large)
        verdict = (
            f"{'PASS' if passed else 'FAIL'} - macro gain floor {floor:+.4f} "
            f"(needs >= {PASS_MIN_GAIN}); classes with k >= {PASS_MIN_K} in every "
            f"build: {wants_large or 'none'}"
        )
    return {
        "metric": key,
        "classes_present": present,
        "missing_classes": [c for c in DECISION_CLASSES if c not in present],
        "per_build": per_build,
        "class_curves": curves,
        "subgroups": subgroups,
        "verdict": verdict,
    }


def _summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Global k curve, the mechanism correlation, and how often tau is undefined."""
    curve: dict[str, dict[str, float]] = {}
    for k_rrf in K_GRID:
        entry: dict[str, float] = {}
        for suffix in ("", "_bal"):
            key_n, key_r = f"ndcg{suffix}", f"recall{suffix}"
            if key_n not in rows[0]["per_k"][str(k_rrf)]:
                continue
            entry[f"ndcg_mean{suffix}"] = statistics.fmean(
                [r["per_k"][str(k_rrf)][key_n] for r in rows]
            )
            entry[f"recall_mean{suffix}"] = statistics.fmean(
                [r["per_k"][str(k_rrf)][key_r] for r in rows]
            )
        curve[str(k_rrf)] = entry

    key = _metric_key(rows)
    winners = [_winners(r["per_k"], key) for r in rows]
    n_flat = sum(1 for w in winners if len(w) == len(K_GRID))
    # A row with a flat curve has no optimal k, so it cannot enter a correlation
    # with one. A row tied over a subset keeps the median of that subset.
    defined = [
        (float(statistics.median(w)), float(r["tau"]))
        for r, w in zip(rows, winners, strict=True)
        if r["tau"] is not None and len(w) < len(K_GRID)
    ]
    taus = [float(r["tau"]) for r in rows if r["tau"] is not None]
    rho = _spearman([d[0] for d in defined], [d[1] for d in defined]) if len(defined) >= 3 else None

    return {
        "n_rows": len(rows),
        "metric": key,
        "n_flat": n_flat,
        "n_unique_optimum": sum(1 for w in winners if len(w) == 1),
        "n_tau_defined": len(taus),
        "n_tau_undefined": len(rows) - len(taus),
        "mean_overlap_at_10": statistics.fmean([r["overlap_at_10"] for r in rows]),
        "tau_mean": statistics.fmean(taus) if taus else None,
        "tau_stdev": statistics.stdev(taus) if len(taus) > 1 else None,
        "n_spearman_rows": len(defined),
        "spearman_optimal_k_vs_tau": rho,
        # Unique optima only - a tie belongs to no single k.
        "optimal_k_distribution": {
            str(k_rrf): sum(1 for w in winners if w == [k_rrf]) for k_rrf in K_GRID
        },
        "global_k_curve": curve,
        "class_analysis": _class_analysis(rows),
    }


def _fmt(value: float | None) -> str:
    return "None" if value is None else f"{value:.4f}"


def _report(summary: dict[str, Any], threshold: float, corpus_name: str) -> str:
    lines = [
        "",
        f"Global k curve on {corpus_name} (mean over all queries and builds)",
        f"{'k':>5} {'nDCG@10':>9} {'recall@10':>10} {'nDCG bal':>9} {'rec bal':>9}",
        "-" * 48,
    ]
    has_bal = "ndcg_mean_bal" in summary["global_k_curve"][str(K_GRID[0])]
    for k_rrf in K_GRID:
        c = summary["global_k_curve"][str(k_rrf)]
        mark = "  <- shipped" if k_rrf == 60 else ""
        bal = f" {c['ndcg_mean_bal']:>9.4f} {c['recall_mean_bal']:>9.4f}" if has_bal else ""
        lines.append(f"{k_rrf:>5} {c['ndcg_mean']:>9.4f} {c['recall_mean']:>10.4f}{bal}{mark}")

    # State the verdict rather than leaving the subtraction to the reader. A
    # curve whose entire range sits under the corpus's detection threshold is a
    # null, and reading its best arm as a winner is how a knob gets "tuned" on
    # noise.
    key = "ndcg_mean_bal" if has_bal else "ndcg_mean"
    ndcgs = [summary["global_k_curve"][str(k)][key] for k in K_GRID]
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
        f"  flat k-curve (no optimum)     {summary['n_flat']}   (metric {summary['metric']})",
        f"  unique optimum                {summary['n_unique_optimum']}",
        f"  Spearman(optimal k, tau)      {_fmt(summary['spearman_optimal_k_vs_tau'])}"
        f"   over {summary['n_spearman_rows']} non-flat rows <- paper predicts monotone",
        "",
        "  unique-optimum distribution (ties belong to no single k):",
    ]
    dist = summary["optimal_k_distribution"]
    for k_rrf in K_GRID:
        lines.append(f"    k={k_rrf:<4} {dist[str(k_rrf)]:>4}")
    if summary["n_spearman_rows"] < 30:
        lines.append(
            f"  !! only {summary['n_spearman_rows']} rows have any k preference;"
            " the correlation above is not usable."
        )

    ca = summary["class_analysis"]
    lines += [
        "",
        f"Adaptive k by query class (pre-registered, CLAUDE.md 8.3a; metric {ca['metric']})",
        "-" * 70,
        f"  classes present: {ca['classes_present'] or 'none'}"
        f"   missing: {ca['missing_classes'] or 'none'}",
        "",
        f"  {'class':<18}" + "".join(f"{k:>7}" for k in K_GRID),
    ]
    for c, curve in ca["class_curves"].items():
        lines.append(f"  {c:<18}" + "".join(f"{curve[str(k)]:>7.4f}" for k in K_GRID))
    lines += [
        "",
        f"  {'build':<6} {'test':<14} {'adaptive':>9} {'fixed':>9} {'k60':>9} {'gain':>8}",
    ]
    for e in ca["per_build"]:
        for name in ("class_macro", "class_micro", "source_micro"):
            if name not in e:
                continue
            r = e[name]
            lines.append(
                f"  {e['build']:<6} {name:<14} {r['adaptive']:>9.4f} {r['global_fixed']:>9.4f}"
                f" {r['k60']:>9.4f} {r['gain']:>+8.4f}   k: {r['selected_k']}"
            )
        lines.append(
            f"  {e['build']:<6} ceilings       best fixed k={e['best_fixed_k']}"
            f"  per-query oracle {e['oracle_gain']:+.4f}"
            f"  two-arm 3/60 {e['two_arm_gain'][60]:+.4f}  3/150 {e['two_arm_gain'][150]:+.4f}"
        )
    lines += ["", f"  {'subgroup':<44} {'n':>5} {'best k':>7} {'@best':>7} {'@3':>7} {'@60':>7}"]
    for name, s in sorted(ca["subgroups"].items()):
        lines.append(
            f"  {name:<44} {s['n']:>5} {s['best_k']:>7} {s['at_best']:>7.4f}"
            f" {s['at_3']:>7.4f} {s['at_60']:>7.4f}"
        )
    lines += ["", f"  VERDICT: {ca['verdict']}", ""]
    return "\n".join(lines)


async def _main_async(args: argparse.Namespace) -> int:
    corpora = [Path(c) for c in args.corpus] or [CORPUS]
    queries_files = [Path(q) for q in args.queries] or [QUERIES]
    if len(corpora) != len(queries_files):
        raise SystemExit("--corpus and --queries must be given the same number of times")

    # Compose once, not per build: the corpus content is identical across
    # builds and only the index is rebuilt.
    workdir = Path(tempfile.mkdtemp(prefix="pma_fusion_corpus_"))
    label = "+".join(c.name.replace("corpus_", "") for c in corpora)
    try:
        if len(corpora) == 1:
            root = corpora[0]
        else:
            counts = _compose_corpus(corpora, workdir)
            root = workdir
            total = sum(counts.values())
            print(f"composed {len(counts)} domains, {total} files: {counts}")

        all_rows: list[dict[str, Any]] = []
        for build in range(args.builds):
            print(f"[build {build + 1}/{args.builds}] indexing {label} ...")
            rows = await _one_build(build, args.keywords, root, queries_files)
            all_rows.extend(rows)
            print(f"[build {build + 1}/{args.builds}] {len(rows)} queries scored")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    summary = _summarise(all_rows)
    print(_report(summary, args.threshold, label))

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "k_grid": list(K_GRID),
            "recall_k": RECALL_K,
            "score_at": SCORE_AT,
            "builds": args.builds,
            "keyword_mode": args.keywords,
            "corpus": [c.name for c in corpora],
            "queries": [q.name for q in queries_files],
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
    p.add_argument(
        "--corpus",
        action="append",
        default=[],
        help=(
            f"corpus dir (default {CORPUS.name}). Repeat with a matching --queries "
            "to compose several corpora into one heterogeneous index"
        ),
    )
    p.add_argument(
        "--queries",
        action="append",
        default=[],
        help=f"labelled queries JSON (default {QUERIES.name}); pairs with --corpus",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="detection threshold for this corpus (CLAUDE.md 8.3)",
    )
    p.add_argument("--json-out", default="", help="write the full per-query rows here")
    p.add_argument(
        "--reanalyse",
        default="",
        help="re-score a saved --json-out file with the current analysis (no index build)",
    )
    p.add_argument(
        "--self-check",
        action="store_true",
        help="run the statistics negative control and exit (no index build)",
    )
    args = p.parse_args()

    if args.self_check:
        _self_check()
        return 0
    if args.reanalyse:
        saved = json.loads(Path(args.reanalyse).read_text(encoding="utf-8"))
        threshold = float(saved.get("threshold", args.threshold))
        label = "+".join(str(c).replace("corpus_", "") for c in saved.get("corpus", ["?"]))
        print(_report(_summarise(saved["rows"]), threshold, label))
        return 0
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
