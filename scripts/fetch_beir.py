"""Materialise a BEIR dataset as a folder corpus the eval harness can index.

The point is a number that means something outside this repo. `tests/eval` is
8 queries over 24 generated documents; any search loop will overfit that inside
ten iterations and nothing in the repo would notice. SciFact is 5,183 real
documents and 300 real queries, and its nDCG@10 is directly comparable to the
published 53.9 that bge-small-en-v1.5 scores on the BEIR average
(`PMA Obsidian/files/RETRIEVAL_BENCHMARKS.md`).

**Read what it does and does not validate.** BEIR scores *document* ranking
against document-level qrels, and relevance is max-pooled from the best chunk, so
chunking differences are averaged away by construction. This validates the
embedder and the reranker. It says nothing about chunk size, and quoting it as if
it did would be the mistake CLAUDE.md 8.7e exists to record.

No new dependency: `huggingface_hub` is already used for the model pins and
`pyarrow` arrives with LanceDB.

    .venv\\Scripts\\python.exe scripts/fetch_beir.py

Explicit opt-in, run by hand. It is **not** wired into any test - section 11
forbids network access and downloads at test time.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# BEIR splits the corpus from its relevance judgements across two repos.
CORPUS_REPO = "BeIR/{name}"
QRELS_REPO = "BeIR/{name}-qrels"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(doc_id: str) -> str:
    """Filenames are the join key back to the qrels, so this must be injective.

    Substitution alone is not: "a/b" and "a_b" would collide and silently merge
    two documents into one, which reads as a retrieval failure later. The raw id
    is appended when substitution changed anything.
    """
    cleaned = _UNSAFE.sub("_", doc_id)
    if cleaned == doc_id:
        return cleaned
    # blake2b, not hash(): str.__hash__ is salted per process (PYTHONHASHSEED),
    # so the same document would land on a different filename on every run and
    # the qrels join would silently rot between the corpus and the queries file.
    digest = hashlib.blake2b(doc_id.encode("utf-8"), digest_size=4).hexdigest()
    return f"{cleaned}-{digest}"


def _read_parquet(path: Path) -> list[dict]:
    import pyarrow.parquet as pq

    rows: list[dict] = pq.read_table(path).to_pylist()
    return rows


def _fetch(repo: str, filename: str, revision: str) -> Path:
    from huggingface_hub import hf_hub_download

    return Path(
        hf_hub_download(repo_id=repo, filename=filename, repo_type="dataset", revision=revision)
    )


def _resolved_sha(repo: str, revision: str) -> str:
    """The dataset commit, recorded so a later run can tell whether the corpus
    moved underneath a comparison."""
    try:
        from huggingface_hub import HfApi

        return str(HfApi().dataset_info(repo, revision=revision).sha)
    except Exception as exc:  # pragma: no cover - provenance is best effort
        return f"unresolved: {type(exc).__name__}"


def _load_qrels(path: Path) -> dict[str, dict[str, int]]:
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            score = int(row["score"])
            if score > 0:
                qrels[row["query-id"]][row["corpus-id"]] = score
    return dict(qrels)


def main() -> int:
    p = argparse.ArgumentParser(description="Download a BEIR dataset into a folder corpus.")
    p.add_argument("--name", default="scifact", help="BEIR dataset name (default scifact)")
    p.add_argument("--split", default="test", help="qrels split (default test)")
    p.add_argument("--revision", default="main")
    p.add_argument("--out", type=Path, default=Path("tests/eval/corpus_scifact"))
    p.add_argument("--queries-out", type=Path, default=Path("tests/eval/queries_scifact.json"))
    p.add_argument(
        "--max-queries",
        type=int,
        default=0,
        help=(
            "keep only the first N queries (0 = all). The corpus is NEVER subset: "
            "shrinking it would make retrieval trivially easy and the nDCG meaningless."
        ),
    )
    args = p.parse_args()

    corpus_repo = CORPUS_REPO.format(name=args.name)
    qrels_repo = QRELS_REPO.format(name=args.name)
    print(f"fetching {corpus_repo} @ {args.revision} ...")

    corpus_rows = _read_parquet(
        _fetch(corpus_repo, "corpus/corpus-00000-of-00001.parquet", args.revision)
    )
    query_rows = _read_parquet(
        _fetch(corpus_repo, "queries/queries-00000-of-00001.parquet", args.revision)
    )
    qrels = _load_qrels(_fetch(qrels_repo, f"{args.split}.tsv", args.revision))
    print(f"  corpus={len(corpus_rows)} queries={len(query_rows)} judged_queries={len(qrels)}")

    # One subdirectory, so the corpus is a single folder_tag. Source-balanced
    # fusion short-circuits at a single bucket (retrieval.py, len(buckets) <= 1),
    # so this neither helps nor distorts - unlike sharding into synthetic
    # "domains", which would make the balancer allocate across meaningless groups.
    domain = args.name
    docs_dir = args.out / domain
    docs_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, str] = {}
    for row in corpus_rows:
        doc_id = str(row["_id"])
        name = _safe(doc_id) + ".txt"
        title = (row.get("title") or "").strip()
        text = (row.get("text") or "").strip()
        body = f"{title}\n\n{text}\n" if title else f"{text}\n"
        (docs_dir / name).write_text(body, encoding="utf-8")
        written[doc_id] = f"{domain}/{name}"
    print(f"  wrote {len(written)} documents to {docs_dir}")

    by_id = {str(r["_id"]): (r.get("text") or "").strip() for r in query_rows}
    queries = []
    for qid, judged in sorted(qrels.items()):
        text = by_id.get(qid)
        relevant = [written[d] for d in judged if d in written]
        if not text or not relevant:
            continue
        queries.append(
            {
                "id": f"{args.name}-{qid}",
                "query": text,
                "type": args.name,
                "answer_len": "",
                "relevant_files": sorted(relevant),
                "expected_domains": [domain],
                # Deliberately empty. BEIR judges documents, not spans, so the
                # span metrics must report nothing here rather than a column of
                # 0.000 that reads as a failure.
                "answer_spans": [],
            }
        )
    if args.max_queries:
        queries = queries[: args.max_queries]

    payload = {
        "_readme": (
            f"Generated by scripts/fetch_beir.py from {corpus_repo} and {qrels_repo}. "
            "Document-level judgements only: answer_spans is empty by design, so the "
            "span metrics report nothing. Validates the embedder and reranker; says "
            "nothing about chunking, because BEIR max-pools chunk differences away."
        ),
        "_provenance": {
            "generated_at": datetime.now(UTC).isoformat(),
            "dataset": args.name,
            "split": args.split,
            "revision": args.revision,
            "corpus_sha": _resolved_sha(corpus_repo, args.revision),
            "qrels_sha": _resolved_sha(qrels_repo, args.revision),
            "documents": len(written),
            "queries": len(queries),
        },
        "queries": queries,
    }
    args.queries_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {len(queries)} queries to {args.queries_out}")
    print(
        "\nnext: index it with tests/eval/harness.EvalIndex(corpus_dir=...) and report "
        "nDCG@10 against the published 53.9 - see RETRIEVAL_BENCHMARKS.md for the caveats."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
