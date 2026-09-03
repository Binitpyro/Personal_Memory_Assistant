"""Build a span-labelled eval corpus from SQuAD, reshaped into real documents.

`tests/eval/corpus_large` is 8 queries over 24 generated Markdown files, and by
2026-09-03 every knob swept against it returned null at about one standard
deviation (CLAUDE.md 8.7f). The limit is the fixture, not the pipeline: with 8
queries a single-query failure moves the mean by ~0.12 against a noise floor of
sd 0.07-0.12, so nothing smaller than that can be decided. More queries is the
fix, and they have to be real.

**Why SQuAD, and why reshaped.** PMA's span metrics need an answer's character
offsets, and SQuAD is the one widely-used public set that ships exactly that -
`answer_start` into the paragraph the question was written against. BEIR does
not: it judges whole documents, which is why `scripts/fetch_beir.py` explicitly
says it cannot speak about chunking.

But a SQuAD paragraph is ~700 characters - **shorter than a single chunk at the
shipped chunk_size=2048** - so used natively it would exercise no chunking at
all, and every answer would sit alone in its own chunk. So the paragraphs of each
article are concatenated into one document and every offset is recomputed into
that document. The result is a real Wikipedia article of ~20k characters holding
many answers, which is the shape the chunker and the delivery path actually face.

**Every label is verified, not trusted**: `document[start:end] == answer_text` is
asserted for each span, and any that fails is dropped with a count. An offset
that is off by one is invisible in a metric and poisons everything downstream.

Known limitation, stated rather than discovered later: SQuAD answers are 1-5
words, so per-query coverage is close to binary and this corpus will NOT stress
long-answer delivery the way corpus_large's long spans do. It buys statistical
power, not answer length. Keep corpus_large for the long-answer signal.

    .venv\\Scripts\\python.exe scripts/fetch_squad_corpus.py --max-queries 100
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = "rajpurkar/squad"
# Paragraphs are joined with a blank line, so the offset shift for paragraph i is
# the running length plus this separator each time. Any change here changes every
# offset, which is why it is a named constant rather than a literal in the loop.
_SEP = "\n\n"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(name: str) -> str:
    cleaned = unicodedata.normalize("NFKD", name)
    cleaned = _UNSAFE.sub("_", cleaned)
    return cleaned.strip("_")[:80] or "untitled"


def _read_parquet(path: Path) -> list[dict]:
    import pyarrow.parquet as pq

    rows: list[dict] = pq.read_table(path).to_pylist()
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description="Build a span-labelled corpus from SQuAD.")
    p.add_argument("--split", default="validation", choices=["validation", "train"])
    p.add_argument("--out", type=Path, default=Path("tests/eval/corpus_squad"))
    p.add_argument("--queries-out", type=Path, default=Path("tests/eval/queries_squad.json"))
    p.add_argument("--max-docs", type=int, default=0, help="0 = every article in the split")
    p.add_argument("--max-queries", type=int, default=100)
    p.add_argument("--seed", type=int, default=20260903)
    p.add_argument(
        "--min-answer-chars",
        type=int,
        default=12,
        help=(
            "drop answers shorter than this (default 12). A two-character answer is "
            "matched by chance inside almost any delivered context, which would make "
            "coverage look excellent while measuring nothing."
        ),
    )
    args = p.parse_args()

    from huggingface_hub import hf_hub_download

    print(f"fetching {REPO} [{args.split}] ...")
    fname = f"plain_text/{args.split}-00000-of-00001.parquet"
    rows = _read_parquet(Path(hf_hub_download(REPO, fname, repo_type="dataset")))
    print(f"  {len(rows)} question rows")

    # Group by article, preserving first-seen paragraph order so offsets are
    # reproducible for a given input file.
    articles: dict[str, list[str]] = defaultdict(list)
    seen_ctx: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        title, ctx = r["title"], r["context"]
        if ctx not in seen_ctx[title]:
            seen_ctx[title].add(ctx)
            articles[title].append(ctx)

    titles = sorted(articles)
    if args.max_docs:
        titles = titles[: args.max_docs]
    keep = set(titles)

    args.out.mkdir(parents=True, exist_ok=True)
    docs_dir = args.out / "squad"
    docs_dir.mkdir(parents=True, exist_ok=True)

    # paragraph text -> (relative file path, offset of that paragraph in the doc)
    placement: dict[tuple[str, str], tuple[str, int]] = {}
    doc_text: dict[str, str] = {}
    for title in titles:
        parts = articles[title]
        rel = f"squad/{_safe(title)}.txt"
        offset = 0
        for para in parts:
            placement[(title, para)] = (rel, offset)
            offset += len(para) + len(_SEP)
        text = _SEP.join(parts)
        doc_text[rel] = text
        with (args.out / rel).open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    total_chars = sum(len(t) for t in doc_text.values())
    print(f"  wrote {len(doc_text)} documents, {total_chars:,} characters")
    print(f"  mean document length: {total_chars // max(len(doc_text), 1):,} characters")

    candidates = []
    dropped_short = 0
    dropped_mismatch = 0
    for r in rows:
        if r["title"] not in keep:
            continue
        answers = r["answers"]
        texts, starts = answers["text"], answers["answer_start"]
        if not texts:
            continue
        atext, astart = texts[0], int(starts[0])
        if len(atext) < args.min_answer_chars:
            dropped_short += 1
            continue
        rel, base = placement[(r["title"], r["context"])]
        gstart, gend = base + astart, base + astart + len(atext)
        # Verified, never trusted: an off-by-one offset is invisible in a metric.
        if doc_text[rel][gstart:gend] != atext:
            dropped_mismatch += 1
            continue
        candidates.append(
            {
                "id": f"squad-{r['id']}",
                "query": r["question"].strip(),
                "type": "squad",
                "answer_len": "short",
                "relevant_files": [rel],
                "expected_domains": ["squad"],
                "answer_spans": [{"file": rel, "start": gstart, "end": gend}],
            }
        )

    # Seeds a REPRODUCIBLE sample of eval queries. A
    # cryptographic generator would be actively wrong here - the seed is recorded
    # in provenance so the same corpus regenerates the same query set.
    rng = random.Random(args.seed)  # noqa: S311
    rng.shuffle(candidates)
    # Spread across articles: one per document first, then fill. A sample that
    # piled onto a few articles would measure those articles, not the corpus.
    by_file: dict[str, list[dict]] = defaultdict(list)
    for c in candidates:
        by_file[c["relevant_files"][0]].append(c)
    queries: list[dict] = []
    while len(queries) < args.max_queries and any(by_file.values()):
        for rel in sorted(by_file):
            if by_file[rel] and len(queries) < args.max_queries:
                queries.append(by_file[rel].pop())

    payload = {
        "_readme": (
            f"Generated by scripts/fetch_squad_corpus.py from {REPO} [{args.split}], "
            "CC BY-SA 4.0. Paragraphs of each article are concatenated into one "
            "document and every answer offset recomputed into it, because a bare "
            "SQuAD paragraph is shorter than one chunk and would exercise no "
            "chunking. Every span is asserted to slice back to its answer text. "
            "Answers are 1-5 words, so this buys statistical power, NOT long-answer "
            "coverage - keep corpus_large for that."
        ),
        "_provenance": {
            "generated_at": datetime.now(UTC).isoformat(),
            "dataset": REPO,
            "split": args.split,
            "seed": args.seed,
            "separator": _SEP,
            "min_answer_chars": args.min_answer_chars,
            "documents": len(doc_text),
            "queries": len(queries),
            "candidates_before_sampling": len(candidates),
            "dropped_answer_too_short": dropped_short,
            "dropped_offset_mismatch": dropped_mismatch,
        },
        "queries": queries,
    }
    args.queries_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"  {len(candidates)} verified candidates; dropped {dropped_short} short, "
        f"{dropped_mismatch} offset mismatches"
    )
    print(f"  wrote {len(queries)} queries to {args.queries_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
