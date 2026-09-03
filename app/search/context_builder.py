import functools
import re
from typing import Any

from app.config import settings

try:
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover - tiktoken is a declared dependency
    tiktoken = None  # type: ignore[assignment]

# None = not yet resolved, False = resolution failed, otherwise the Encoding.
# tiktoken is a declared dependency now, so the ImportError branch above should
# be unreachable - but get_encoding() itself can still fail on a cold cache, and
# _token_count silently degrades to len(text)//4 when it does.
_ENCODING: Any = None


def _get_encoding() -> Any:
    """Lazily initialize tokenizer encoding for token-accurate budgeting."""
    global _ENCODING
    if _ENCODING is not None:
        return _ENCODING

    if tiktoken is None:
        _ENCODING = False
        return _ENCODING

    try:
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _ENCODING = False
    return _ENCODING


@functools.lru_cache(maxsize=1024)
def _get_tokens(text: str) -> list[int]:
    """Tokenize text using tiktoken with caching (P-03)."""
    enc = _get_encoding()
    if not enc:
        return []
    return enc.encode(text)  # type: ignore


def _token_count(text: str) -> int:
    enc = _get_encoding()
    if not enc:
        # Conservative fallback when tiktoken is unavailable.
        return max(1, len(text) // 4)
    return len(_get_tokens(text))


def token_count(text: str) -> int:
    """Public alias for the module's token counter.

    The agentic loop budgets in the same units the context builder spends in,
    so both must go through one implementation.
    """
    return _token_count(text)


def count_tokens_uncached(text: str) -> int:
    """Token count for one-shot text: a whole prompt, a whole answer.

    Deliberately bypasses the ``_get_tokens`` LRU. Those inputs are unique per
    request, so caching them evicts reusable chunk entries and retains
    multi-thousand-token id lists that will never be read again.

    Callers must not use ``len(_get_tokens(text))`` for this: when tiktoken is
    unavailable ``_get_tokens`` returns ``[]`` rather than raising, so a
    ``len()`` of it reports a confident zero and any surrounding ``except``
    fallback never runs.
    """
    enc = _get_encoding()
    if not enc:
        return max(1, len(text) // 4)
    return len(enc.encode(text))  # type: ignore[union-attr]


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    if max_tokens <= 0 or not text:
        return ""
    enc = _get_encoding()
    if not enc:
        return text[: max_tokens * 4]
    token_ids = enc.encode(text)
    if len(token_ids) <= max_tokens:
        return text
    return str(enc.decode(token_ids[:max_tokens])).rstrip() + "…"


def _compress_text(text: str) -> str:
    """Normalize whitespace and remove excessive newlines to save tokens."""
    # Replace 3+ newlines with 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _format_file_stats(stats: dict[str, Any]) -> str:
    """Format aggregate file statistics into a readable preamble for the LLM."""
    lines = [
        "=== File Statistics (from your indexed files) ===",
        f"Total indexed files: {stats['total_files']}",
        f"Total size: {stats['total_size_mb']} MB",
        "",
        "Files by type:",
    ]
    for t in stats["by_type"]:
        lines.append(f"  {t['ext']}: {t['count']} files ({t['size_mb']} MB)")
    lines.append("")
    lines.append("Files by folder/project:")
    for f in stats["by_folder"]:
        lines.append(f"  {f['folder']}: {f['count']} files")
    lines.append("=" * 50)
    return "\n".join(lines)


def _span_overlap_ratio(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Fraction of the shorter of two spans that both spans cover."""
    overlap = min(a[1], b[1]) - max(a[0], b[0])
    if overlap <= 0:
        return 0.0
    shorter = min(a[1] - a[0], b[1] - b[0])
    return overlap / shorter if shorter > 0 else 0.0


def _chunk_span(res: dict[str, Any]) -> tuple[Any, tuple[int, int]] | None:
    """``(file_id, (start, end))`` for a result, or None if it carries no offsets."""
    file_id = res.get("file_id")
    start, end = res.get("start_offset"), res.get("end_offset")
    if file_id is None or start is None or end is None:
        return None
    return file_id, (int(start), int(end))


def _deduplicate_redundant(
    results: list[dict[str, Any]], min_span_overlap: float = 0.7
) -> list[dict[str, Any]]:
    """Drop chunks that add no text the context does not already carry.

    Two exact tests replace the MinHash pass that used to live here:

    * identical text after whitespace normalisation, from any file;
    * at least *min_span_overlap* of the shorter span shared with an already
      kept chunk of the same file, read straight off ``file_id`` /
      ``start_offset`` / ``end_offset``.

    Both are integer-cheap and exact. The MinHash version spent ~64k hashes per
    chunk approximating the first test and could not perform the second at all:
    it signed a 200-character middle slice, so two chunks with similar middles
    and different heads and tails were dropped as duplicates. What is genuinely
    given up is *near*-duplicate (not identical) text across different files -
    datasketch priced that at ~98 MB of transitive scipy, which the 4 GB
    hardware target does not justify.
    """
    deduped: list[dict[str, Any]] = []
    seen_texts: set[str] = set()
    spans_by_file: dict[Any, list[tuple[int, int]]] = {}

    for res in results:
        if len(deduped) >= 100:
            break

        text = res.get("text", "")
        if len(text) < 50:
            deduped.append(res)
            continue

        normalized = " ".join(text.split())
        if normalized in seen_texts:
            continue

        spanned = _chunk_span(res)
        if spanned is not None:
            file_id, span = spanned
            kept = spans_by_file.setdefault(file_id, [])
            if any(_span_overlap_ratio(span, other) >= min_span_overlap for other in kept):
                continue
            kept.append(span)

        seen_texts.add(normalized)
        deduped.append(res)

    return deduped


def append_project_profile_lines(lines: list[str], folder_profiles: list[dict[str, Any]]) -> None:
    lines.append("Indexed project/folder profiles:")
    for fp in folder_profiles:
        size_mb = round(fp["total_size_bytes"] / (1024 * 1024), 2)
        lines.append(
            f"  - {fp['folder_tag']} ({fp['project_type']}): {fp['file_count']} files, {size_mb} MB"
        )


def append_inventory_type_lines(lines: list[str], file_stats: dict[str, Any]) -> None:
    by_type = file_stats.get("by_type", [])
    if by_type:
        lines.append("Breakdown by file type:")
        for t in by_type[:10]:
            lines.append(f"  - {t['ext'] or 'unknown'}: {t['count']} files ({t['size_mb']} MB)")


def _apply_relevance_cutoff(
    results: list[dict[str, Any]], score_multiplier: float
) -> list[dict[str, Any]]:
    """Drop weak chunks, reading whichever score scale actually ordered the list.

    Two incompatible scales reach this point:

    * ``rerank_score`` - cross-encoder logits, **signed**, roughly -10..+10
    * ``score`` - RRF, strictly positive

    The cutoff used to be a ratio of ``deduplicated[0]["score"]`` while the list
    was ordered by ``rerank_score``, so the threshold came from whatever RRF
    score the top-reranked chunk happened to hold: too low and the filter did
    nothing, too high and it dropped exactly the chunks the reranker had
    promoted.

    The ratio cannot simply be repointed at ``rerank_score`` either. Multiplying
    a *negative* top logit by ``score_multiplier`` raises the bar instead of
    lowering it, so a healthy top result near -1.5 would filter the whole list
    away and hand the LLM an empty context. On the cross-encoder scale the
    correct test is an absolute floor.

    When no chunk carries ``rerank_score`` the reranker did not run, the list is
    genuinely in RRF order, and the original ratio cutoff applies. When only
    some carry it the two scales are mixed and no single threshold is
    meaningful, so nothing is dropped.
    """
    assessed = [r for r in results if r.get("rerank_score") is not None]

    if len(assessed) == len(results):
        floor = settings.agentic_evidence_score_floor
        return [r for r in results if r["rerank_score"] >= floor] or results[:1]

    if not assessed:
        top_score = results[0].get("score", 1.0)
        if top_score > 0:
            threshold = top_score * score_multiplier
            return [r for r in results if r.get("score", 1.0) >= threshold]
        return results

    # Mixed scales - not assessable as one ranking.
    return results


def _deduplicate_by_file(
    results: list[dict[str, Any]], max_per_file: int = 2
) -> list[dict[str, Any]]:
    """Limit snippets per file to improve source diversity in the context window.

    Keeps at most *max_per_file* chunks from the same file, preferring
    higher-scored ones (the list is assumed to be pre-sorted by score).
    """
    file_counts: dict[str, int] = {}
    deduped: list[dict[str, Any]] = []
    for res in results:
        fp = res.get("file_path", "")
        file_counts[fp] = file_counts.get(fp, 0) + 1
        if file_counts[fp] <= max_per_file:
            deduped.append(res)

    # The single dedup pass. It runs here, after reranking, so the reranker's
    # ordering decides which of two redundant chunks survives.
    return _deduplicate_redundant(deduped)


def _format_snippets(
    deduplicated: list[dict[str, Any]],
    remaining_tokens: int,
    head_share: float | None = None,
) -> list[str]:
    if head_share is None:
        head_share = settings.context_snippet_head_share
    context_parts: list[str] = []
    used_tokens = 0

    for i, res in enumerate(deduplicated):
        if used_tokens >= remaining_tokens:
            break

        snippet_id = i + 1
        path = res.get("file_path", "Unknown File")
        # `parent_text` is the widened window when parent-window expansion ran
        # (app/search/retrieval.py:attach_parent_windows); `text` is the child
        # chunk that actually matched. Prefer the window for the model, and note
        # that everything ABOVE this point - dedup, the relevance cutoff, the
        # per-file cap - has already run against the child, which is the point
        # of small-to-big rather than an oversight.
        text = res.get("parent_text") or res.get("text", "")

        remaining_for_snippets = max(remaining_tokens - used_tokens, 0)
        remaining_count = max(len(deduplicated) - i, 1)
        if i < 3:
            # Reserve more budget for top-ranked chunks while still hard-limited.
            #
            # This decay is a HARD CEILING on how much of the budget can ever be
            # used, and it binds hardest exactly where there is least room. Three
            # snippets each taking `share` of what remains can reach at most
            # 1 - (1 - share)^3 of the budget: 71.2% at 0.34. For 3b_local, whose
            # max_chunks is 3, every snippet is in this branch, so 29% of its
            # context budget is unreachable by construction - measured 1,796
            # tokens delivered against a 2,520 budget, predicted 1,789 (8.7f).
            # Sweeping max_chunks and max_per_file both came back flat because of
            # this, not because more evidence was unavailable.
            snippet_budget = max(120, int(remaining_for_snippets * head_share))
        else:
            snippet_budget = max(80, int(remaining_for_snippets / remaining_count))
        snippet_budget = min(snippet_budget, remaining_for_snippets)
        if snippet_budget <= 0:
            break

        chunk_id = res.get("chunk_id", snippet_id)
        label = f"Snippet {snippet_id} [ID: {chunk_id}] [{path}]:\n"
        label_tokens = _token_count(label)
        if label_tokens >= snippet_budget:
            continue

        body_budget = max(1, snippet_budget - label_tokens - 3)  # reserve for delimiter
        text = _truncate_to_tokens(text, body_budget)
        part = f"{label}{text}\n---\n"
        part_tokens = _token_count(part)
        if used_tokens + part_tokens > remaining_tokens:
            token_budget = max(1, remaining_tokens - used_tokens - label_tokens)
            text_truncated = _truncate_to_tokens(text, token_budget)
            part = f"{label}{text_truncated}\n---\n"
            part_tokens = _token_count(part)
            if used_tokens + part_tokens > remaining_tokens:
                break

        context_parts.append(part)
        used_tokens += part_tokens
    return context_parts


def _add_metadata_insights(
    context_parts: list[str], insights: str | None, max_tokens: int, used_tokens: int
) -> int:
    if insights:
        part = _truncate_to_tokens(insights, max_tokens - used_tokens)
        if part:
            context_parts.append(part)
            return _token_count(part)
    return 0


def _add_folder_profiles(
    context_parts: list[str], text: str, max_tokens: int, used_tokens: int
) -> int:
    if text and used_tokens < max_tokens:
        header = "### PROJECT ARCHITECTURE (High-Level Summaries)\n"
        h_tokens = _token_count(header)
        body = _truncate_to_tokens(text, max_tokens - used_tokens - h_tokens)
        if body:
            part = f"{header}{body}\n\n"
            context_parts.append(part)
            return _token_count(part)
    return 0


def _add_file_stats(
    context_parts: list[str], stats: dict[str, Any] | None, max_tokens: int, used_tokens: int
) -> int:
    if stats and used_tokens < max_tokens:
        stats_block = _format_file_stats(stats)
        part = _truncate_to_tokens(stats_block, max_tokens - used_tokens)
        if part:
            context_parts.append(part)
            return _token_count(part)
    return 0


def _add_graph_paths(context_parts: list[str], text: str, max_tokens: int, used_tokens: int) -> int:
    if text and used_tokens < max_tokens:
        header = "### GRAPH RELATIONSHIPS (Dependencies and Calls)\n"
        h_tokens = _token_count(header)
        body = _truncate_to_tokens(text, max_tokens - used_tokens - h_tokens)
        if body:
            part = f"{header}<graph_relationships>\n{body}\n</graph_relationships>\n\n"
            context_parts.append(part)
            return _token_count(part)
    return 0


def compute_context_budget(model_class: str, history_turns: int) -> int:
    """Adaptive context budget based on model's effective capacity."""
    EFFECTIVE_CEILINGS = {  # noqa: N806
        "cloud": 100_000,
        "7b_local": 10_000,
        # A setting so it can be swept. The small class is the one that binds:
        # after the head-share fix it uses 93.5% of its budget (2,357 of 2,520),
        # so the ceiling, not the allocation, is now what limits it.
        "3b_local": settings.context_ceiling_small,
    }
    ceiling = EFFECTIVE_CEILINGS.get(model_class, 8000)

    # Fixed costs
    system_prompt = 400
    output_reserve = min(1000, ceiling // 4)
    query_overhead = 80
    history_cost = history_turns * 400

    context_budget = ceiling - system_prompt - output_reserve - query_overhead - history_cost
    return max(1000, context_budget)


def build_context(
    retrieved_results: list[dict[str, Any]],
    max_tokens: int = 0,
    file_stats: dict[str, Any] | None = None,
    folder_profiles_text: str = "",
    metadata_insights: str | None = None,
    graph_paths_text: str = "",
    model_class: str = "cloud",
) -> tuple[str, int]:
    """Formats retrieved snippets into a single context string for the LLM.

    Optimisations:
    - P10-5: Separates context into Architecture vs Implementation headers.
    - Deduplicates by file path (max 2 snippets per file).
    - Truncates each snippet to a budget.
    """
    if (
        not retrieved_results
        and not file_stats
        and not folder_profiles_text
        and not metadata_insights
    ):
        msg = "No relevant context found."
        return msg, _token_count(msg)

    if max_tokens <= 0:
        max_tokens = settings.context_max_tokens

    # Adaptive changes for 3b_local
    if model_class == "3b_local":
        folder_profiles_text = ""
        graph_paths_text = ""
        max_per_file = settings.context_max_per_file_small
        score_multiplier = 0.4
        max_chunks = settings.context_max_chunks_small
        head_share = settings.context_snippet_head_share_small
    else:
        max_per_file = 2
        score_multiplier = 0.2
        max_chunks = 15
        head_share = settings.context_snippet_head_share

    context_parts: list[str] = []
    used_tokens = 0

    # 1. High-level Metadata (Stats/Insights)
    used_tokens += _add_file_stats(context_parts, file_stats, max_tokens, used_tokens)
    used_tokens += _add_metadata_insights(context_parts, metadata_insights, max_tokens, used_tokens)

    # 2. Architectural Context (Folder Profiles)
    used_tokens += _add_folder_profiles(
        context_parts, folder_profiles_text, max_tokens, used_tokens
    )

    # 3. Graph Relationships
    used_tokens += _add_graph_paths(context_parts, graph_paths_text, max_tokens, used_tokens)

    # 4. Implementation Details (Chunks)
    if retrieved_results and used_tokens < max_tokens:
        header = "### IMPLEMENTATION DETAILS (Specific Code/Text Chunks)\n"
        h_tokens = _token_count(header)
        if used_tokens + h_tokens < max_tokens:
            context_parts.append(header)
            used_tokens += h_tokens

            deduplicated = _deduplicate_by_file(retrieved_results, max_per_file=max_per_file)

            if deduplicated:
                deduplicated = _apply_relevance_cutoff(deduplicated, score_multiplier)

            # Keep only top max_chunks
            deduplicated = deduplicated[:max_chunks]

            snippet_parts = _format_snippets(
                deduplicated, max(0, max_tokens - used_tokens), head_share
            )
            context_parts.extend(snippet_parts)

    final_context = _compress_text("\n".join(context_parts))
    truncated = _truncate_to_tokens(final_context, max_tokens)
    return truncated, _token_count(truncated)
