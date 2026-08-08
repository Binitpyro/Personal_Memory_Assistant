"""Retrieval quality metrics.

Measurement scaffolding, deliberately kept under tests/ rather than app/ - it
is not shipped product. No new dependencies: these are short enough to write
directly, and pulling in an IR library for four functions would be a worse
trade than maintaining them.

Every function takes *file paths*, not chunk ids. Retrieval returns chunks, but
ground truth is per-document: several chunks of one relevant file should not
count as several hits. Callers deduplicate to an ordered file list first via
`ranked_files`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def ranked_files(results: Sequence[dict], k: int | None = None) -> list[str]:
    """Ordered, deduplicated file paths from retrieval results.

    Rank is first appearance: a file's best-scoring chunk determines where the
    document sits, which is what per-document ground truth is comparing against.
    """
    seen: set[str] = set()
    out: list[str] = []
    for r in results:
        path = r.get("file_path")
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(path)
        if k is not None and len(out) >= k:
            break
    return out


def recall_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Fraction of relevant documents appearing in the top k.

    Returns 0.0 when nothing is relevant, so an empty label set cannot silently
    score a perfect 1.0 and flatter a configuration.
    """
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    hits = len(relevant_set.intersection(retrieved[:k]))
    return hits / len(relevant_set)


def precision_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Fraction of the top k that is relevant."""
    if k <= 0 or not retrieved:
        return 0.0
    window = retrieved[:k]
    relevant_set = set(relevant)
    return len([p for p in window if p in relevant_set]) / len(window)


def mrr(retrieved: Sequence[str], relevant: Iterable[str]) -> float:
    """Reciprocal rank of the first relevant document (0.0 if none appear)."""
    relevant_set = set(relevant)
    for i, path in enumerate(retrieved):
        if path in relevant_set:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Normalised discounted cumulative gain with binary relevance.

    Binary because the ground truth is binary - inventing graded relevance for
    a hand-labelled fixture would be making up numbers.
    """
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0

    dcg = sum(
        1.0 / math.log2(i + 2) for i, path in enumerate(retrieved[:k]) if path in relevant_set
    )
    ideal_hits = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg else 0.0


def domain_coverage(results: Sequence[dict], expected_domains: Iterable[str], k: int) -> float:
    """Fraction of expected domains represented in the top k chunks.

    The source-balanced fusion metric. It has no standard IR equivalent because
    it measures something ranking quality alone does not: whether the window
    reaches every corpus the question spans, or whether one dense domain took
    every slot and produced a confident answer from a single source.

    Operates on chunks rather than deduplicated files - the window is a chunk
    budget, and it is chunk slots that a dense domain monopolises.
    """
    expected = set(expected_domains)
    if not expected:
        return 0.0
    present = {r.get("folder_tag") or "" for r in results[:k]}
    return len(expected.intersection(present)) / len(expected)


def summarize(rows: Sequence[dict], keys: Sequence[str]) -> dict[str, float]:
    """Mean of each metric across per-query rows. Missing keys count as 0.0."""
    if not rows:
        return dict.fromkeys(keys, 0.0)
    return {key: sum(float(r.get(key, 0.0)) for r in rows) / len(rows) for key in keys}
