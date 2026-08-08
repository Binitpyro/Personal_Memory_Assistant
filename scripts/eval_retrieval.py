"""
scripts/eval_retrieval.py
─────────────────────────
Run retrieval against the **real** configured index and report what came back.

Two modes:

  Unlabelled (default) - no ground truth needed. Prints the top-k for each
    query with its folder tag and score, plus the domain spread and score
    spread. This is the quickest way to confirm the document-routing signal is
    alive after a re-embed: an empty `pma_summaries` shows up as every query
    drawing from one domain with a flat score distribution.

  Labelled - takes a queries JSON in the tests/eval/queries.json format and
    prints recall / nDCG / MRR / domain coverage per query and in aggregate.

Usage (from the project root with the venv active):
    python scripts/eval_retrieval.py "how is the cache keyed" "what did I write about colour"
    python scripts/eval_retrieval.py --queries-file my_queries.txt
    python scripts/eval_retrieval.py --labelled tests/eval/queries.json
    python scripts/eval_retrieval.py --labelled q.json --corpus-root D:/notes

Notes:
- Reads the same DB and LanceDB the app uses (app.config.settings). Read-only.
- --no-reranker skips the cross-encoder, which a dev checkout usually lacks.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


def _relativize(path: str, root: str) -> str:
    if not root:
        return path
    normalized = path.replace("\\", "/")
    root_norm = root.replace("\\", "/").rstrip("/")
    if normalized.startswith(root_norm):
        return normalized[len(root_norm) :].lstrip("/")
    return normalized


_clients: dict = {}


def apply_path_overrides(db_path: str, lancedb_dir: str) -> None:
    """Point the script at a specific index.

    Must be assignment onto the settings object, not environment variables:
    `Settings.compute_paths` is a post-init validator that *overwrites*
    db_path and lancedb_persist_dir whenever lancedb_mode is "split_brain",
    so PMA_DB_PATH / PMA_LANCEDB_PERSIST_DIR are silently ignored on a normal
    desktop install. Without these flags the script always opens the real
    index, which is the wrong default for a throwaway experiment.
    """
    from app.config import settings

    if db_path:
        settings.db_path = db_path
    if lancedb_dir:
        settings.lancedb_persist_dir = lancedb_dir


async def _get_clients():
    """Initialise db / embeddings / LanceDB once and reuse them."""
    if not _clients:
        from app.api.deps import get_db, get_emb, get_lancedb

        db = await get_db()
        emb = get_emb()
        emb.load_model()
        lance = get_lancedb()
        lance.connect()
        _clients.update(db=db, emb=emb, lance=lance)
    return _clients["db"], _clients["emb"], _clients["lance"]


async def _retrieve(query: str, k: int, use_reranker: bool):
    from app.search import retrieval

    db, emb, lance = await _get_clients()

    retrieval.clear_retrieval_cache()
    return await retrieval.hybrid_retrieve(
        query=query,
        db=db,
        embedding_service=emb,
        lancedb_client=lance,
        k=k,
        use_reranker=use_reranker,
    )


async def run_unlabelled(queries: list[str], k: int, use_reranker: bool) -> int:
    empty = 0
    for q in queries:
        results = await _retrieve(q, k, use_reranker)
        print(f"\n=== {q!r}  (k={k}) ===")
        if not results:
            print("  no results")
            empty += 1
            continue

        for i, r in enumerate(results, 1):
            tag = r.get("folder_tag") or "-"
            name = Path(r.get("file_path", "")).name
            print(f"  {i:>2}. [{tag:<14}] {r.get('score', 0.0):>9.4f}  {name}")

        domains = sorted({(r.get("folder_tag") or "-") for r in results})
        scores = [r.get("score", 0.0) for r in results]
        spread = max(scores) - min(scores) if scores else 0.0
        print(f"  domains: {len(domains)} ({', '.join(domains)})   score spread: {spread:.4f}")
        if len(domains) == 1:
            print("  note: single domain - expected only if the query really is single-domain")
        if spread == 0.0:
            print("  note: flat scores - no ranking signal reached the results")
        if any(a < b for a, b in itertools.pairwise(scores)):
            # Not a bug: source-balanced fusion merges domains round-robin, so a
            # lower-scoring result from an under-represented domain is promoted
            # above a higher-scoring one from a domain that already has slots.
            print("  note: order is not score-descending - domain allocation is interleaving")
    return empty


async def run_labelled(path: Path, k: int, use_reranker: bool, corpus_root: str) -> int:
    from tests.eval import metrics

    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data["queries"] if isinstance(data, dict) else data

    rows = []
    print(f"{'query':<28}{'recall':>8}{'ndcg':>8}{'mrr':>8}{'domains':>9}")
    print("-" * 61)
    for entry in entries:
        results = await _retrieve(entry["query"], k, use_reranker)
        for r in results:
            r["file_path"] = _relativize(r.get("file_path", ""), corpus_root)

        ranked = metrics.ranked_files(results)
        relevant = entry.get("relevant_files", [])
        expected = entry.get("expected_domains", [])
        row = {
            "recall": metrics.recall_at_k(ranked, relevant, k),
            "precision": metrics.precision_at_k(ranked, relevant, k),
            "ndcg": metrics.ndcg_at_k(ranked, relevant, k),
            "mrr": metrics.mrr(ranked, relevant),
            "domain_coverage": metrics.domain_coverage(results, expected, k),
        }
        rows.append(row)
        print(
            f"{entry.get('id', entry['query'])[:27]:<28}"
            f"{row['recall']:>8.2f}{row['ndcg']:>8.2f}{row['mrr']:>8.2f}"
            f"{row['domain_coverage']:>9.2f}"
        )

    print("-" * 61)
    overall = metrics.summarize(rows, ("recall", "precision", "ndcg", "mrr", "domain_coverage"))
    print(
        f"{'MEAN':<28}{overall['recall']:>8.2f}{overall['ndcg']:>8.2f}"
        f"{overall['mrr']:>8.2f}{overall['domain_coverage']:>9.2f}"
    )
    return 0


_HELP = """Run retrieval against the real configured index.

Unlabelled (default): prints the top-k per query with folder tag, score, domain
spread and score spread - enough to tell whether the routing signal is alive.
Labelled: pass --labelled with a queries JSON to get recall / nDCG / MRR /
domain coverage.

  python scripts/eval_retrieval.py "how is the cache keyed"
  python scripts/eval_retrieval.py --labelled tests/eval/queries.json
"""


def build_parser() -> argparse.ArgumentParser:
    # Not __doc__: the module header uses box-drawing characters that a cp1252
    # console cannot encode, and argparse writes help straight to stdout.
    p = argparse.ArgumentParser(
        description=_HELP, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("queries", nargs="*", help="Query strings for unlabelled mode.")
    p.add_argument("--queries-file", type=Path, help="File of queries, one per line.")
    p.add_argument("--labelled", type=Path, help="Queries JSON with ground truth.")
    p.add_argument("-k", type=int, default=10, help="Results per query (default 10).")
    p.add_argument(
        "--corpus-root",
        default="",
        help="Strip this prefix from file paths so they match labelled ground truth.",
    )
    p.add_argument("--no-reranker", action="store_true", help="Skip the cross-encoder pass.")
    p.add_argument(
        "--db-path",
        default="",
        help="SQLite index to query. Defaults to the configured one (the real index).",
    )
    p.add_argument(
        "--lancedb-dir",
        default="",
        help="LanceDB directory to query. Defaults to the configured one.",
    )
    return p


async def main_async(args) -> int:
    apply_path_overrides(args.db_path, args.lancedb_dir)
    use_reranker = not args.no_reranker

    if args.labelled:
        return await run_labelled(args.labelled, args.k, use_reranker, args.corpus_root)

    queries = list(args.queries)
    if args.queries_file:
        queries += [
            line.strip()
            for line in args.queries_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]

    if not queries:
        print("No queries given. Pass query strings, --queries-file, or --labelled.")
        return 2

    empty = await run_unlabelled(queries, args.k, use_reranker)
    if empty:
        print(f"\n{empty}/{len(queries)} queries returned nothing - is the index built?")
        return 1
    return 0


def cli_main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    cli_main()
