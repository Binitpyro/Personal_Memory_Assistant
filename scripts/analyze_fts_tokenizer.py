"""Does a word-level FTS5 leg fix what a small rrf_k only compensates for?

CLAUDE.md 8.3a (2026-09-15): on SciFact the whole k=1-over-k=60 gain is PMA's
trigram keyword leg. Alone it scores nDCG@10 0.596 against 0.677 for word-level
BM25 on the same chunks, and a small k merely stops fusion trusting it. This
measures the root-cause alternative on the query-type composite, reranker ON,
with production keywords (QueryPlan.keywords), before any rrf_k change ships.

Arms, keyword leg x k, everything else production:
  tri   shipped: chunk_fts, tokenize="trigram" (schema.sql:64-69), weight 0.4
  word  a side table, tokenize="porter unicode61", weight 0.4, the same MATCH
        expression (_sanitize_fts_query) and the same SQL as _fts_search
  both  tri and word as two keyword lists at 0.2 each, so keyword weight is unchanged
  k in (0, 1, 5, 10, 60)

Reuses analyze_fusion_k's reranker replay (one production rerank() per distinct
pool) and, in build 1, checks it: tri@60 must equal hybrid_retrieve on every
query whose production recall_k is 50, and the copied FTS SQL must return
exactly what _fts_search returns on every query.

PRE-REGISTERED RULE (CLAUDE.md 8.3a, written before any run)
  Metric: post-rerank nDCG@10, mean over all queries per build; floor = min over builds.
  SHIPPED = tri@60. KFIX = tri@k*, k* in {0,1,5,10} with the best 3-build mean.
  Candidates: word@60, both@60.
  Guard: a candidate fails if any of code_identifier / navigation /
    structured_lookup / semantic_prose has a 3-build-mean delta vs SHIPPED <= -0.025.
  LEG FIX WINS  iff a candidate passes the guard, floor vs SHIPPED >= +0.025 and
                floor vs KFIX >= 0.
  K FIX WINS    else iff KFIX floor vs SHIPPED >= +0.025.
  NEITHER       otherwise.

Usage:
    .venv\\Scripts\\python.exe scripts/analyze_fts_tokenizer.py --self-check
    .venv\\Scripts\\python.exe scripts/analyze_fts_tokenizer.py --builds 3 \\
        --json-out research/fts_tokenizer_qtype.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts import analyze_fusion_k as afk  # noqa: E402

EVAL = REPO / "tests" / "eval"
PAIRS = (
    ("corpus_squad", "queries_squad.json"),
    ("corpus_scifact", "queries_scifact.json"),
    ("corpus", "queries.json"),
    ("corpus_qtype", "queries_qtype.json"),
)
VARIANTS = ("tri", "word", "both")
KS = (0, 1, 5, 10, 60)
KFIX_KS = (0, 1, 5, 10)
CANDIDATES = ("word@60", "both@60")
SHIPPED = "tri@60"
THRESHOLD = 0.025
_ALL = 10**9  # no truncation, for the two-list sum in "both"

WORD_DDL = (
    "CREATE VIRTUAL TABLE chunk_fts_word USING fts5("
    'chunks_text, content="", tokenize="porter unicode61", detail=full)'
)
# retrieval.py:222-228 with no folder/type filter. Two literals, not a format
# string, so the table name is never interpolated.
TRI_SQL = (
    "SELECT cf.rowid FROM chunk_fts cf JOIN chunks c ON c.id = cf.rowid "
    "JOIN files f ON f.id = c.file_id WHERE cf.chunks_text MATCH ? ORDER BY rank LIMIT ?"
)
WORD_SQL = (
    "SELECT cf.rowid FROM chunk_fts_word cf JOIN chunks c ON c.id = cf.rowid "
    "JOIN files f ON f.id = c.file_id WHERE cf.chunks_text MATCH ? ORDER BY rank LIMIT ?"
)


async def _fts(db: Any, sql: str, query: str, keywords: list[str] | None) -> list[dict[str, Any]]:
    from app.search.retrieval import _sanitize_fts_query

    match = _sanitize_fts_query(query, keywords)
    if not match:
        return []
    try:
        rows = await db.execute_query(sql, (match, 2 * afk.RECALL_K))
    except Exception:  # retrieval.py:231 degrades to [] the same way
        return []
    return [{"id": str(r[0])} for r in rows]


def _fuse(variant: str, legs: dict[str, Any], k: int) -> list[tuple[str, float]]:
    """Production _compute_rrf_scores at k; "both" sums two calls at half keyword weight."""
    from app.config import settings
    from app.search import retrieval

    settings.rrf_k = k
    if variant != "both":
        return retrieval._compute_rrf_scores(
            legs[variant], legs["sem"], legs["summary"], afk.RECALL_K
        )
    weight = settings.rrf_fts_weight
    settings.rrf_fts_weight = weight / 2
    try:
        scores = dict(
            retrieval._compute_rrf_scores(legs["tri"], legs["sem"], legs["summary"], _ALL)
        )
        for cid, sc in retrieval._compute_rrf_scores(legs["word"], [], None, _ALL):
            scores[cid] = scores.get(cid, 0.0) + sc
    finally:
        settings.rrf_fts_weight = weight
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], retrieval._chunk_sort_key(kv[0])))
    return ordered[: afk.RECALL_K]


async def _one_build(build: int, root: Path, queries: Sequence[Any]) -> list[dict[str, Any]]:
    from app.config import settings
    from app.search import retrieval
    from app.search.planner import QueryPlanner
    from tests.eval import metrics
    from tests.eval.harness import EvalIndex

    idx = EvalIndex(corpus_dir=root)
    await idx.build()
    db, lance, embeddings = idx.db, idx.lancedb, idx.embeddings
    if db is None or lance is None or embeddings is None:
        raise RuntimeError("EvalIndex.build() must run before analysis")
    planner = QueryPlanner()
    original = (settings.rrf_k, settings.rrf_fts_weight)
    check = {"fts_equal": 0, "matched": 0, "skipped_degraded": 0, "passes": 0}
    rows: list[dict[str, Any]] = []
    try:
        await db.execute_write(WORD_DDL)
        await db.execute_write(
            "INSERT INTO chunk_fts_word(rowid, chunks_text) "
            "SELECT id, zlib_decompress(text_preview) FROM chunks"
        )
        n_chunks = (await db.execute_query("SELECT count(*) FROM chunks"))[0][0]
        n_word = (await db.execute_query("SELECT count(*) FROM chunk_fts_word_docsize"))[0][0]
        if n_word != n_chunks:
            raise SystemExit(f"word FTS holds {n_word} rows, chunks {n_chunks}")
        try:
            sizes = await db.execute_query(
                "SELECT name LIKE 'chunk_fts_word%', sum(pgsize) FROM dbstat "
                "WHERE name LIKE 'chunk_fts%' GROUP BY 1"
            )
            print(f"[build {build + 1}] FTS bytes (0=trigram, 1=word): {sizes}")
        except Exception as e:
            print(f"[build {build + 1}] dbstat unavailable: {e}")

        path_map, tag_map = await afk._chunk_maps(db)
        t0 = time.time()
        for i, q in enumerate(queries):
            kw = planner.plan(q.query).keywords
            tri = await retrieval._fts_search(db, q.query, afk.RECALL_K, keywords=kw)
            if build == 0:
                if await _fts(db, TRI_SQL, q.query, kw) != tri:
                    raise SystemExit(f"copied FTS SQL != _fts_search for {q.id!r}")
                check["fts_equal"] += 1
            word = await _fts(db, WORD_SQL, q.query, kw)
            emb = await embeddings.embed_query(q.query)
            sem = await retrieval._semantic_search_with_emb(lance, emb, afk.RECALL_K)
            summary_paths = await retrieval._summary_search_with_emb(
                lance, emb, settings.retrieval_top_k, where_filter={"is_folder_profile": "false"}
            )
            summary = await retrieval._expand_summary_paths_to_chunks(db, summary_paths, None)
            legs = {"tri": tri, "word": word, "sem": sem, "summary": summary}

            def ranked(chunk_ids: Sequence[int]) -> list[str]:
                return metrics.ranked_files(
                    [{"file_path": idx.relativize(path_map[c])} for c in chunk_ids if c in path_map]
                )

            pools: dict[str, tuple[list[int], dict[int, float]]] = {}
            off: dict[str, float] = {}
            for variant in VARIANTS:
                for k in KS:
                    fused = _fuse(variant, legs, k)
                    ids = [int(c) for c, _ in fused if int(c) in path_map]
                    balanced = retrieval._allocate_by_domain(ids, tag_map, afk.RECALL_K)
                    arm = f"{variant}@{k}"
                    pools[arm] = (balanced, {int(c): s for c, s in fused})
                    off[arm] = metrics.ndcg_at_k(ranked(balanced), q.relevant_files, afk.SCORE_AT)
            settings.rrf_k = original[0]

            per_arm, final_ids, passes = await afk._rerank_replay(q, pools, db, idx.relativize)  # type: ignore[arg-type]
            check["passes"] += passes

            if build == 0 and len(q.query.split()) > 8:
                settings.rrf_k = 60
                retrieval.clear_retrieval_cache()
                live = await retrieval.hybrid_retrieve(
                    query=q.query,
                    db=db,
                    embedding_service=embeddings,
                    lancedb_client=lance,
                    k=settings.retrieval_top_k,
                    use_reranker=True,
                    keywords=kw,
                )
                settings.rrf_k = original[0]
                if any(r.get("_degraded") for r in live):
                    check["skipped_degraded"] += 1
                elif [r["chunk_id"] for r in live] != final_ids[SHIPPED]:  # type: ignore[index]
                    raise SystemExit(
                        f"replay != hybrid_retrieve for {q.id!r} at tri@60:\n"
                        f"  replay {final_ids[SHIPPED]}\n  live   {[r['chunk_id'] for r in live]}"  # type: ignore[index]
                    )
                else:
                    check["matched"] += 1

            rows.append(
                {
                    "build": build,
                    "query_id": q.id,
                    "type": q.type,
                    "fts_len": len(tri),
                    "word_len": len(word),
                    "leg_alone": {
                        leg: metrics.ndcg_at_k(
                            ranked([int(r["id"]) for r in legs[leg]]),
                            q.relevant_files,
                            afk.SCORE_AT,
                        )
                        for leg in ("tri", "word")
                    },
                    "arms": {arm: {**per_arm[arm], "ndcg_off": off[arm]} for arm in pools},
                }
            )
            if (i + 1) % 50 == 0:
                el = time.time() - t0
                print(
                    f"[build {build + 1}] {i + 1}/{len(queries)} queries, {el:.0f}s,"
                    f" ~{el / (i + 1) * (len(queries) - i - 1):.0f}s left in build,"
                    f" {check['passes'] / (i + 1):.1f} passes/query",
                    flush=True,
                )
    finally:
        settings.rrf_k, settings.rrf_fts_weight = original
        await idx.close()
    if build == 0:
        print(
            f"[build 1] copied FTS SQL == _fts_search on {check['fts_equal']} queries;"
            f" replay == hybrid_retrieve on {check['matched']}"
            f" ({check['skipped_degraded']} skipped: reranker timed out)"
        )
    return rows


def _summarise(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    builds = sorted({r["build"] for r in rows})
    arms = list(rows[0]["arms"])

    def mean_of(b: int, arm: str, key: str = "ndcg", cls: str | None = None) -> float:
        sel = [r for r in rows if r["build"] == b and (cls is None or afk._query_class(r) == cls)]
        return statistics.fmean(r["arms"][arm][key] for r in sel)

    per_build = {b: {a: mean_of(b, a) for a in arms} for b in builds}
    avg = {a: statistics.fmean(per_build[b][a] for b in builds) for a in arms}

    def floor(arm: str, ref: str) -> float:
        return min(per_build[b][arm] - per_build[b][ref] for b in builds)

    classes = [c for c in afk.DECISION_CLASSES if any(afk._query_class(r) == c for r in rows)]

    def class_delta(arm: str, ref: str) -> dict[str, float]:
        return {
            c: statistics.fmean(mean_of(b, arm, cls=c) - mean_of(b, ref, cls=c) for b in builds)
            for c in classes
        }

    kfix = max((f"tri@{k}" for k in KFIX_KS), key=lambda a: avg[a])
    candidates = {}
    for cand in CANDIDATES:
        cd = class_delta(cand, SHIPPED)
        candidates[cand] = {
            "floor_vs_shipped": floor(cand, SHIPPED),
            "floor_vs_kfix": floor(cand, kfix),
            "class_delta_vs_shipped": cd,
            "guard_ok": all(v > -THRESHOLD for v in cd.values()),
        }
    kfix_floor = floor(kfix, SHIPPED)
    return {
        "builds": len(builds),
        "arm_mean": avg,
        "arm_recall": {a: statistics.fmean(mean_of(b, a, "recall") for b in builds) for a in arms},
        "arm_mean_rerank_off": {
            a: statistics.fmean(mean_of(b, a, "ndcg_off") for b in builds) for a in arms
        },
        "per_build": per_build,
        "kfix": kfix,
        "kfix_floor_vs_shipped": kfix_floor,
        "kfix_class_delta_vs_shipped": class_delta(kfix, SHIPPED),
        "candidates": candidates,
        "leg_alone": {
            leg: statistics.fmean(r["leg_alone"][leg] for r in rows) for leg in ("tri", "word")
        },
        "verdict": _verdict(candidates, kfix_floor),
    }


def _verdict(candidates: dict[str, dict[str, Any]], kfix_floor: float) -> str:
    passing = [
        c
        for c, v in candidates.items()
        if v["guard_ok"] and v["floor_vs_shipped"] >= THRESHOLD and v["floor_vs_kfix"] >= 0
    ]
    if passing:
        best = max(passing, key=lambda c: candidates[c]["floor_vs_shipped"])
        return f"LEG FIX WINS ({', '.join(passing)}; best {best})"
    if kfix_floor >= THRESHOLD:
        return "K FIX WINS"
    return "NEITHER"


def _report(s: dict[str, Any]) -> str:
    lines = ["", f"post-rerank nDCG@10, mean of {s['builds']} builds (rerank-off / recall@10)"]
    for a, v in s["arm_mean"].items():
        lines.append(
            f"  {a:<9} {v:.4f}   off {s['arm_mean_rerank_off'][a]:.4f}   recall {s['arm_recall'][a]:.4f}"
        )
    lines.append(
        "  keyword leg alone: " + "  ".join(f"{k} {v:.4f}" for k, v in s["leg_alone"].items())
    )
    lines.append(
        f"  KFIX = {s['kfix']}: floor vs {SHIPPED} {s['kfix_floor_vs_shipped']:+.4f}  classes "
        + " ".join(f"{c} {d:+.4f}" for c, d in s["kfix_class_delta_vs_shipped"].items())
    )
    for c, v in s["candidates"].items():
        lines.append(
            f"  {c}: floor vs {SHIPPED} {v['floor_vs_shipped']:+.4f}, vs KFIX {v['floor_vs_kfix']:+.4f},"
            f" guard {'ok' if v['guard_ok'] else 'FAIL'}  classes "
            + " ".join(f"{k} {d:+.4f}" for k, d in v["class_delta_vs_shipped"].items())
        )
    lines += ["", f"VERDICT: {s['verdict']}"]
    return "\n".join(lines)


def _self_check() -> None:
    legs: dict[str, Any] = {
        "tri": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
        "sem": [{"id": "3"}, {"id": "1"}],
        "summary": [{"id": "2", "rank": 0}],
    }
    for k in KS:
        # word == tri at half weight twice must be the shipped fusion exactly.
        same = _fuse("both", {**legs, "word": legs["tri"]}, k)
        ref = _fuse("tri", {**legs, "word": []}, k)
        assert [c for c, _ in same] == [c for c, _ in ref], (k, same, ref)
        assert all(abs(a - b) < 1e-12 for (_, a), (_, b) in zip(same, ref, strict=True))
    # Negative control: a disagreeing word list must change the order.
    bare = {"tri": legs["tri"], "sem": [], "summary": None}
    flipped = _fuse("both", {**bare, "word": list(reversed(legs["tri"]))}, 60)
    assert [c for c, _ in flipped] != [c for c, _ in _fuse("tri", {**bare, "word": []}, 60)]

    ok = {"guard_ok": True, "floor_vs_shipped": 0.03, "floor_vs_kfix": 0.001}
    assert _verdict({"word@60": ok}, 0.02).startswith("LEG FIX WINS")
    assert _verdict({"word@60": {**ok, "guard_ok": False}}, 0.03) == "K FIX WINS"
    assert _verdict({"word@60": {**ok, "floor_vs_kfix": -0.001}}, 0.02) == "NEITHER"
    assert _verdict({"word@60": {**ok, "floor_vs_shipped": 0.02}}, 0.024) == "NEITHER"
    print("self-check OK: _fuse (both == tri when lists agree; differs when not), _verdict")


async def _main_async(args: argparse.Namespace) -> int:
    from tests.eval.harness import load_queries

    queries = [q for _, qf in PAIRS for q in load_queries(EVAL / qf)]
    if len({q.id for q in queries}) != len(queries):
        raise SystemExit("duplicate query ids across query files")
    workdir = Path(tempfile.mkdtemp(prefix="pma_fts_corpus_"))
    rows: list[dict[str, Any]] = []
    try:
        counts = afk._compose_corpus([EVAL / c for c, _ in PAIRS], workdir)
        print(
            f"composed {len(counts)} domains, {sum(counts.values())} files, {len(queries)} queries"
        )
        for build in range(args.builds):
            print(f"[build {build + 1}/{args.builds}] indexing ...", flush=True)
            rows.extend(await _one_build(build, workdir, queries))
            if args.json_out:  # per-build checkpoint: a power cut costs one build
                Path(args.json_out).write_text(json.dumps({"rows": rows}), encoding="utf-8")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    summary = _summarise(rows)
    print(_report(summary))
    if args.json_out:
        payload = {"ks": list(KS), "variants": list(VARIANTS), "summary": summary, "rows": rows}
        Path(args.json_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.json_out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Word-level FTS5 leg vs a smaller rrf_k.")
    p.add_argument("--builds", type=int, default=3)
    p.add_argument("--json-out", default="")
    p.add_argument("--reanalyse", default="", help="re-score a saved --json-out (no index build)")
    p.add_argument("--self-check", action="store_true")
    args = p.parse_args()
    _self_check()
    if args.self_check:
        return 0
    if args.reanalyse:
        saved = json.loads(Path(args.reanalyse).read_text(encoding="utf-8"))
        print(_report(_summarise(saved["rows"])))
        return 0
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
