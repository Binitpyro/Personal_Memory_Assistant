import difflib
import re
from typing import Any

from app.config import settings

try:
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    tiktoken = None

_ENCODING = None


def _get_encoding():
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


def _token_count(text: str) -> int:
    enc = _get_encoding()
    if not enc:
        # Conservative fallback when tiktoken is unavailable.
        return max(1, len(text) // 4)
    return len(enc.encode(text))


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    if max_tokens <= 0 or not text:
        return ""
    enc = _get_encoding()
    if not enc:
        return text[: max_tokens * 4]
    token_ids = enc.encode(text)
    if len(token_ids) <= max_tokens:
        return text
    return enc.decode(token_ids[:max_tokens]).rstrip() + "…"


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


def _semantic_deduplicate(
    results: list[dict[str, Any]], similarity_threshold: float = 0.85
) -> list[dict[str, Any]]:
    """Drop snippets that are semantically (textually) >85% similar to already selected chunks."""
    deduped: list[dict[str, Any]] = []
    for res in results:
        # P10-1: Added safety cap to prevent O(N²) CPU spikes on huge result sets.
        if len(deduped) > 100:
            deduped.append(res)
            continue

        text = res.get("text", "")
        if len(text) < 50:
            deduped.append(res)
            continue

        is_duplicate = False
        for saved in deduped:
            saved_text = saved.get("text", "")
            len_ratio = len(text) / max(1, len(saved_text))
            if 0.7 < len_ratio < 1.3:
                sim = difflib.SequenceMatcher(None, text, saved_text).ratio()
                if sim > similarity_threshold:
                    is_duplicate = True
                    break

        if not is_duplicate:
            deduped.append(res)
    return deduped


def append_unreal_fact_lines(lines: list[str], unreal_facts: list[dict[str, Any]]) -> None:
    lines.append("Unreal project summary:")
    for uf in unreal_facts:
        lines.append(
            f"  - {uf['project_name']} (UE {uf['engine_version']}): "
            f"{uf['total_assets']} assets, {uf['map_count']} maps, "
            f"{uf['character_blueprints']} char BPs, {uf['material_count']} materials."
        )


def append_unreal_profile_hint(lines: list[str], unreal_profiles: list[dict[str, Any]]) -> None:
    lines.append("Unreal Engine projects detected:")
    for up in unreal_profiles:
        lines.append(f"  - {up['folder_tag']} ({up['file_count']} files)")


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

    # Apply semantic deduplication for overlapping chunks (e.g. overlap windows)
    return _semantic_deduplicate(deduped)


def _format_snippets(
    deduplicated: list[dict[str, Any]],
    remaining_tokens: int,
) -> list[str]:
    context_parts: list[str] = []
    used_tokens = 0

    for i, res in enumerate(deduplicated):
        if used_tokens >= remaining_tokens:
            break

        snippet_id = i + 1
        path = res.get("file_path", "Unknown File")
        text = res.get("text", "")

        remaining_for_snippets = max(remaining_tokens - used_tokens, 0)
        remaining_count = max(len(deduplicated) - i, 1)
        if i < 3:
            # Reserve more budget for top-ranked chunks while still hard-limited.
            snippet_budget = max(120, int(remaining_for_snippets * 0.34))
        else:
            snippet_budget = max(80, int(remaining_for_snippets / remaining_count))
        snippet_budget = min(snippet_budget, remaining_for_snippets)
        if snippet_budget <= 0:
            break

        label = f"Snippet {snippet_id} [{path}]:\n"
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


def build_context(
    retrieved_results: list[dict[str, Any]],
    max_tokens: int = 0,
    file_stats: dict[str, Any] | None = None,
    folder_profiles_text: str = "",
    metadata_insights: str | None = None,
) -> str:
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
        return "No relevant context found."

    if max_tokens <= 0:
        max_tokens = settings.context_max_tokens

    context_parts: list[str] = []
    used_tokens = 0

    # 1. High-level Metadata (Stats/Insights)
    used_tokens += _add_file_stats(context_parts, file_stats, max_tokens, used_tokens)
    used_tokens += _add_metadata_insights(context_parts, metadata_insights, max_tokens, used_tokens)

    # 2. Architectural Context (Folder Profiles)
    used_tokens += _add_folder_profiles(
        context_parts, folder_profiles_text, max_tokens, used_tokens
    )

    # 3. Implementation Details (Chunks)
    if retrieved_results and used_tokens < max_tokens:
        header = "### IMPLEMENTATION DETAILS (Specific Code/Text Chunks)\n"
        h_tokens = _token_count(header)
        if used_tokens + h_tokens < max_tokens:
            context_parts.append(header)
            used_tokens += h_tokens

            deduplicated = _deduplicate_by_file(retrieved_results)

            if deduplicated:
                top_score = deduplicated[0].get("score", 1.0)
                if top_score > 0:
                    score_threshold = top_score * 0.2
                    deduplicated = [
                        r for r in deduplicated if r.get("score", 1.0) >= score_threshold
                    ]

            snippet_parts = _format_snippets(deduplicated, max(0, max_tokens - used_tokens))
            context_parts.extend(snippet_parts)

    final_context = _compress_text("\n".join(context_parts))
    return _truncate_to_tokens(final_context, max_tokens)
