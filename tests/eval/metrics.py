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
from collections import Counter
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


# ── Chunk-level metrics ──────────────────────────────────────────────────────
# Everything above scores *documents*: `ranked_files` deduplicates to file paths
# first, so several chunks of one relevant file count once. That is the right
# shape for the routing and balancing ablations and the wrong shape for
# chunking, which changes what is inside a chunk without necessarily changing
# which file it came from (CLAUDE.md 8.7 D2).
#
# These two read the answer *span* labels instead, and they pull in opposite
# directions on purpose - which is what makes a chunk-size sweep legible rather
# than a single number that could move for any reason:
#
#   precision falls as chunks grow   - a bigger chunk carries more non-answer text
#   coverage  rises as chunks grow   - a bigger chunk is less likely to split the answer
#
# A change that improves one while destroying the other has not improved
# chunking, it has moved along the tradeoff. Report both, always. This is the
# same lesson section 8.4a records for recall against nDCG on the summary leg,
# where a configuration that looked excellent on recall alone had wrecked the
# ranking.


def _merge_intervals(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union of half-open intervals, so overlap is never counted twice.

    Not incidental: `chunk_overlap` is 50 characters by default, so adjacent
    retrieved chunks genuinely share text. Summing their contributions without
    merging would let coverage exceed 1.0 and would reward *more* overlap rather
    than better boundaries.
    """
    if not spans:
        return []
    ordered = sorted(spans)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _result_span(res: dict) -> tuple[str, int, int] | None:
    """(file_path, start, end) for a retrieval result, or None if unusable.

    Offsets address the *extracted text stream*, not the file's bytes. Those
    coincide today because the indexer reads in text mode and the corpus is
    written LF, so a CRLF checkout still decodes to the same string - the two
    only diverge if some reader stops normalising. tests/eval/corpus_large is
    pinned to LF so that cannot happen silently; see
    scripts/generate_eval_corpus.py.
    """
    path = res.get("file_path")
    start = res.get("start_offset")
    end = res.get("end_offset")
    if not path or start is None or end is None:
        return None
    try:
        start_i, end_i = int(start), int(end)
    except (TypeError, ValueError):
        return None
    if end_i <= start_i:
        return None
    return path, start_i, end_i


def chunk_span_precision_at_k(
    results: Sequence[dict], answer_spans: Sequence[dict], k: int
) -> float:
    """Fraction of the top *k* retrieved chunks that touch a labelled answer span.

    This is the context window's signal-to-noise: at ``max_chunks = 15`` and a
    93-token median chunk, it says how many of those fifteen slots carried any
    of the passage the question was about.

    A chunk counts if it overlaps at all. Deliberately generous - partial credit
    for *how much* of the chunk is answer would conflate this with coverage
    below, and the two are supposed to move independently.
    """
    if k <= 0 or not results or not answer_spans:
        return 0.0

    by_file: dict[str, list[tuple[int, int]]] = {}
    for span in answer_spans:
        by_file.setdefault(span["file"], []).append((int(span["start"]), int(span["end"])))

    window = list(results[:k])
    if not window:
        return 0.0

    hits = 0
    for res in window:
        spanned = _result_span(res)
        if spanned is None:
            continue
        path, start, end = spanned
        if any(start < s_end and end > s_start for s_start, s_end in by_file.get(path, [])):
            hits += 1
    return hits / len(window)


def answer_span_coverage_at_k(
    results: Sequence[dict], answer_spans: Sequence[dict], k: int
) -> float:
    """Mean fraction of each labelled answer span delivered by the top *k* chunks.

    The metric chunking is actually accountable to. A 672-character answer split
    across three chunks of which one is retrieved scores ~0.33: the reader was
    handed a third of the sentence that answers them, which document-level
    recall would have scored a perfect 1.0 because the right *file* was found.

    Averaged per span rather than pooled over all characters, so one long answer
    cannot dominate several short ones.
    """
    if k <= 0 or not answer_spans:
        return 0.0

    retrieved_by_file: dict[str, list[tuple[int, int]]] = {}
    for res in list(results[:k]):
        spanned = _result_span(res)
        if spanned is None:
            continue
        path, start, end = spanned
        retrieved_by_file.setdefault(path, []).append((start, end))

    for path in retrieved_by_file:
        retrieved_by_file[path] = _merge_intervals(retrieved_by_file[path])

    fractions: list[float] = []
    for span in answer_spans:
        s_start, s_end = int(span["start"]), int(span["end"])
        length = s_end - s_start
        if length <= 0:
            continue
        covered = sum(
            max(0, min(end, s_end) - max(start, s_start))
            for start, end in retrieved_by_file.get(span["file"], [])
        )
        fractions.append(min(1.0, covered / length))

    return sum(fractions) / len(fractions) if fractions else 0.0


# ── Granularity: how much of what we retrieved was actually the answer ───────
# `chunk_span_precision_at_k` above counts a chunk as a full hit if it *touches*
# an answer span, however much unrelated text it also carries. That is blind to
# dilution by construction: a 2048-character chunk containing a 175-character
# answer scores identically to a 512-character one containing the same answer.
#
# It matters. CLAUDE.md 8.7b records "precision was expected to fall as chunks
# grew and instead rose" - which a chunk-granular metric *cannot* show, so that
# observation could not have meant what it was read to mean. The published
# chunking evaluations (Chroma's, and the chunking-taxonomy survey) all measure
# at token granularity for exactly this reason, and report IoU alongside
# precision to penalise redundant overlap.
#
# Characters, not tokens, and deliberately so:
#   * the labels are already character ranges, so no source text is needed;
#   * `metrics.py` stays pure - no file I/O, no tokenizer - which is what lets
#     tests/test_eval_corpus_spans.py run in the default suite with no models;
#   * chars/token is near-constant on prose (measured 4.8-6.2 on this corpus),
#     so the ratio a precision metric reports is materially the same either way.
# A token-exact version would need the source text threaded in; if that is ever
# built, it belongs beside these rather than replacing them.
#
# Recall is NOT re-added here: `answer_span_coverage_at_k` already is it, per
# span rather than pooled.


def _retrieved_regions(results: Sequence[dict], k: int) -> dict[str, list[tuple[int, int]]]:
    """Merged retrieved character ranges per file, for the top *k*."""
    by_file: dict[str, list[tuple[int, int]]] = {}
    for res in list(results[:k]):
        spanned = _result_span(res)
        if spanned is None:
            continue
        path, start, end = spanned
        by_file.setdefault(path, []).append((start, end))
    return {p: _merge_intervals(v) for p, v in by_file.items()}


def _relevant_regions(answer_spans: Sequence[dict]) -> dict[str, list[tuple[int, int]]]:
    by_file: dict[str, list[tuple[int, int]]] = {}
    for span in answer_spans:
        by_file.setdefault(span["file"], []).append((int(span["start"]), int(span["end"])))
    return {p: _merge_intervals(v) for p, v in by_file.items()}


def _overlap(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> int:
    total = 0
    for a_start, a_end in a:
        for b_start, b_end in b:
            total += max(0, min(a_end, b_end) - max(a_start, b_start))
    return total


def _length(regions: list[tuple[int, int]]) -> int:
    return sum(end - start for start, end in regions)


def char_precision_at_k(results: Sequence[dict], answer_spans: Sequence[dict], k: int) -> float:
    """Fraction of the retrieved characters that are answer characters.

    The dilution metric. Falls as chunks grow, which is the behaviour a chunk
    size sweep needs in order to have two forces to trade off against each other
    rather than one that only ever rises.
    """
    if k <= 0 or not answer_spans:
        return 0.0
    retrieved = _retrieved_regions(results, k)
    if not retrieved:
        return 0.0
    total = sum(_length(v) for v in retrieved.values())
    if total <= 0:
        return 0.0
    relevant = _relevant_regions(answer_spans)
    hit = sum(_overlap(v, relevant.get(p, [])) for p, v in retrieved.items())
    return hit / total


def char_iou_at_k(results: Sequence[dict], answer_spans: Sequence[dict], k: int) -> float:
    """Jaccard overlap of retrieved and answer character regions.

    Reported alongside precision because it is the one number that punishes both
    failure directions at once - missing the answer, and burying it in text the
    model has to read anyway. Chunk overlap counts once, not twice, because both
    sides are merged first.
    """
    if k <= 0 or not answer_spans:
        return 0.0
    retrieved = _retrieved_regions(results, k)
    relevant = _relevant_regions(answer_spans)
    inter = sum(_overlap(v, relevant.get(p, [])) for p, v in retrieved.items())
    union = (
        sum(_length(v) for v in retrieved.values())
        + sum(_length(v) for v in relevant.values())
        - inter
    )
    return inter / union if union > 0 else 0.0


# ── Delivery stage: what the model actually receives ─────────────────────────
# Everything above scores RETRIEVAL. The product does not stop there:
# `full_rag`/`stream_rag` continue through `attach_parent_windows` and
# `build_context`, and that path expands, deduplicates by file, applies a
# relevance cutoff, truncates to `max_chunks`, and gives each surviving snippet
# a per-snippet token budget. Any of those can remove the answer *after*
# retrieval has been scored a success.
#
# Measured 2026-09-02 at chunk_size=512: retrieval-stage coverage 0.684 against
# delivery-stage 0.287 with parent windows off - the retrieval metric overstated
# what reached the model by 2.4x, and two queries scored a perfect 1.000 at
# retrieval while delivering ~0.06. Saturation at chunk_size=2048 had hidden it.
#
# This takes STRINGS, not spans, because by this point there are no offsets left
# - the context is an assembled document with labels, separators and truncation
# markers in it. That makes it a genuinely different computation from
# `answer_span_coverage_at_k`, which is why it is named differently. Reporting
# both under one name is what let the two get conflated in the first place.


def context_answer_coverage(context: str, answer: str) -> float:
    """Fraction of *answer* present in *context* as one contiguous run.

    Whitespace-normalised, so re-wrapping and the snippet formatter's joins do
    not count as damage. Whole answer present -> 1.0; absent -> 0.0; truncated
    -> the fraction that survived.

    Contiguous by design. An answer chopped in half by a snippet budget has not
    half-arrived in any useful sense - the model sees a sentence that stops
    mid-clause - so scoring the longest surviving run is closer to what the
    reader experiences than counting matched words anywhere.
    """
    a = " ".join(answer.split())
    c = " ".join(context.split())
    if not a:
        return 0.0
    if a in c:
        return 1.0
    words = a.split()
    # Longest prefix-anchored run is not enough: truncation cuts the tail, but
    # per-file dedup can drop a leading chunk instead. Search every window.
    for size in range(len(words) - 1, 0, -1):
        for i in range(len(words) - size + 1):
            if " ".join(words[i : i + size]) in c:
                return size / len(words)
    return 0.0


# ── Generation stage: what the model wrote back ──────────────────────────────
# The families above score what was RETRIEVED and what was DELIVERED. Neither
# says anything about the answer, and the answer is the product. These do.
#
# Deterministic on purpose. RAGAS computes the analogous "answer correctness"
# with an LLM judge, which PMA cannot afford twice over: a cloud judge
# contradicts section 1.4, and a local judge adds its own sampling variance to a
# fixture that already resolves ties differently per build (section 8.4a). The
# labelled answer spans make a judge unnecessary - the reference string is known
# exactly, so the comparison is arithmetic.
#
# Normalisation is the SQuAD convention (lowercase, punctuation to space,
# articles dropped) and deliberately NOT a general stopword list. Comparability
# with published extractive-QA numbers is the only reason to prefer token
# overlap over the span metrics above, and a bespoke stopword list forfeits it.
#
# **Read recall, not F1, when the question is "did the answer survive".**
# prompts/rag_system.txt instruction 1 orders the model to be "detailed,
# comprehensive" and instruction 5 orders it to emit [source_index] citations.
# Both inflate precision's denominator with tokens the prompt itself demanded,
# so F1 scores obedience to the prompt as much as it scores the answer, and it
# does so by an amount that is roughly constant across chunking configurations -
# which is exactly the amount that cancels in a comparison and swamps it in an
# absolute figure. F1 is kept because it is the published convention. Recall is
# what moves when chunking changes.

_ARTICLES = frozenset({"a", "an", "the"})


def _answer_tokens(text: str) -> list[str]:
    """SQuAD normalisation: lowercase, punctuation to space, articles dropped."""
    folded = "".join(c if c.isalnum() or c.isspace() else " " for c in text.lower())
    return [t for t in folded.split() if t not in _ARTICLES]


def _token_overlap(generated: str, reference: str) -> tuple[int, int, int]:
    """``(overlap, len(generated), len(reference))`` counted as multisets.

    Multisets, not sets: a reference that says "cache the cache" twice is not
    satisfied by an answer that says it once, and set intersection would call
    that a full hit.
    """
    gen = _answer_tokens(generated)
    ref = _answer_tokens(reference)
    overlap = sum((Counter(gen) & Counter(ref)).values())
    return overlap, len(gen), len(ref)


def answer_token_recall(generated: str, reference: str) -> float:
    """Fraction of the reference answer's tokens that reached the answer.

    The primary generation signal. Unlike ``answer_token_f1`` it is indifferent
    to how much *else* the model said, which is the right property here because
    verbosity is set by the system prompt rather than by anything under test.

    It cannot distinguish a real answer from a fluent restatement of the
    question that happens to share vocabulary. Read it next to the abstention
    rate, which is what catches that.
    """
    overlap, _, n_ref = _token_overlap(generated, reference)
    return overlap / n_ref if n_ref else 0.0


def answer_token_f1(generated: str, reference: str) -> float:
    """SQuAD token-level F1 of the answer against the labelled reference.

    Reported for comparability with published extractive-QA numbers, not as the
    quantity to optimise - see the section comment above for why precision is
    partly a measure of the system prompt.
    """
    overlap, n_gen, n_ref = _token_overlap(generated, reference)
    if not overlap or not n_gen or not n_ref:
        return 0.0
    precision = overlap / n_gen
    recall = overlap / n_ref
    return 2 * precision * recall / (precision + recall)
