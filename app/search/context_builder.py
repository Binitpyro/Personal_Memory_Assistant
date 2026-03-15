import re
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
from app.config import settings

def _compress_text(text: str) -> str:
    """Normalize whitespace and remove excessive newlines to save tokens."""
    # Replace 3+ newlines with 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Replace multiple spaces with single space
    text = re.sub(r' +', ' ', text)
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

def _format_snippets(deduplicated: List[Dict[str, Any]], max_chars: int, total_len: int) -> List[str]:
    context_parts = []
    for i, res in enumerate(deduplicated):
        snippet_id = i + 1
        path = res.get("file_path", "Unknown File")
        text = res.get("text", "")
        
        # Phase 4.1: Top-2 snippets get 40% of budget, rest share remainder
        if i < 2:
            budget = int(max_chars * 0.2)  # 20% each for top 2 = 40% total
        else:
            remaining_count = max(len(deduplicated) - 2, 1)
            budget = int((max_chars * 0.6) / remaining_count)
        budget = max(budget, min(600, max_chars))  # At least 600 chars

        if len(text) > budget:
            text = text[:budget] + "…"

        part = f"Snippet {snippet_id} [{path}]:\n{text}\n---\n"
        if total_len + len(part) > max_chars:
            break
            
        context_parts.append(part)
        total_len += len(part)
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

    context_parts = []
    total_len = 0
    max_chars = max_tokens * 4  # ~4 chars per token heuristic

    # ── 1. Metadata Insights (Highest Priority Factual Data) ─────
    if metadata_insights:
        context_parts.append(metadata_insights)
        total_len += len(metadata_insights)

    # ── 2. Folder Profiles (Project-level context) ──────────────
    if folder_profiles_text:
        context_parts.append(folder_profiles_text)
        total_len += len(folder_profiles_text)

    # ── 3. File Statistics (Aggregate Data) ────────────────────
    if file_stats:
        stats_block = _format_file_stats(file_stats)
        context_parts.append(stats_block)
        total_len += len(stats_block)

    # ── 4. Semantic Snippets (Chunk-level data) ────────────────
    # Deduplicate: max 2 snippets from the same file for diversity
    deduplicated = _deduplicate_by_file(retrieved_results)

    # Drop snippets scoring < 20% of the top score (noise reduction)
    if deduplicated:
        top_score = deduplicated[0].get("score", 1.0)
        if top_score > 0:
            score_threshold = top_score * 0.2
            deduplicated = [r for r in deduplicated if r.get("score", 1.0) >= score_threshold]
    
    snippet_parts = _format_snippets(deduplicated, max_chars, total_len)
    context_parts.extend(snippet_parts)
            
    final_context = "\n".join(context_parts)
    return _compress_text(final_context)
