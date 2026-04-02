import re
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
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
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def _format_file_stats(stats: Dict[str, Any]) -> str:
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


def _deduplicate_by_file(results: List[Dict[str, Any]], max_per_file: int = 2) -> List[Dict[str, Any]]:
    """Limit snippets per file to improve source diversity in the context window.

    Keeps at most *max_per_file* chunks from the same file, preferring
    higher-scored ones (the list is assumed to be pre-sorted by score).
    """
    file_counts: Dict[str, int] = {}
    deduped: List[Dict[str, Any]] = []
    for res in results:
        fp = res.get("file_path", "")
        file_counts[fp] = file_counts.get(fp, 0) + 1
        if file_counts[fp] <= max_per_file:
            deduped.append(res)
    return deduped

def _format_snippets(
    deduplicated: List[Dict[str, Any]],
    remaining_tokens: int,
) -> List[str]:
    context_parts: List[str] = []
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
            part = f"{label}{_truncate_to_tokens(text, max(1, remaining_tokens - used_tokens - label_tokens))}\n---\n"
            part_tokens = _token_count(part)
            if used_tokens + part_tokens > remaining_tokens:
                break

        context_parts.append(part)
        used_tokens += part_tokens
    return context_parts

def build_context(
    retrieved_results: List[Dict[str, Any]],
    max_tokens: int = 0,
    file_stats: Optional[Dict[str, Any]] = None,
    folder_profiles_text: str = "",
    metadata_insights: Optional[str] = None,
) -> str:
    """Formats retrieved snippets into a single context string for the LLM.

    Optimisations over the original:
    - Deduplicates by file path (max 2 snippets per file) for diversity.
    - Truncates each snippet to a character budget so one long chunk
      doesn't monopolise the context window.
    - Prioritises high-scoring snippets first.
    """
    if not retrieved_results and not file_stats and not folder_profiles_text and not metadata_insights:
        return "No relevant context found."

    if max_tokens <= 0:
        max_tokens = settings.context_max_tokens

    context_parts: List[str] = []
    used_tokens = 0

    # ── 1. Metadata Insights (Highest Priority Factual Data) ─────
    if metadata_insights:
        part = _truncate_to_tokens(metadata_insights, max_tokens - used_tokens)
        if part:
            context_parts.append(part)
            used_tokens += _token_count(part)

    # ── 2. Folder Profiles (Project-level context) ──────────────
    if folder_profiles_text and used_tokens < max_tokens:
        part = _truncate_to_tokens(folder_profiles_text, max_tokens - used_tokens)
        if part:
            context_parts.append(part)
            used_tokens += _token_count(part)

    # ── 3. File Statistics (Aggregate Data) ────────────────────
    if file_stats and used_tokens < max_tokens:
        stats_block = _format_file_stats(file_stats)
        part = _truncate_to_tokens(stats_block, max_tokens - used_tokens)
        if part:
            context_parts.append(part)
            used_tokens += _token_count(part)

    # ── 4. Semantic Snippets (Chunk-level data) ────────────────
    # Deduplicate: max 2 snippets from the same file for diversity
    deduplicated = _deduplicate_by_file(retrieved_results)

    # Drop snippets scoring < 20% of the top score (noise reduction)
    if deduplicated:
        top_score = deduplicated[0].get("score", 1.0)
        if top_score > 0:
            score_threshold = top_score * 0.2
            deduplicated = [r for r in deduplicated if r.get("score", 1.0) >= score_threshold]

    snippet_parts = _format_snippets(deduplicated, max(0, max_tokens - used_tokens))
    context_parts.extend(snippet_parts)

    final_context = _compress_text("\n".join(context_parts))
    # Final hard stop to guarantee budget.
    return _truncate_to_tokens(final_context, max_tokens)
