from typing import List, Dict, Any, Optional, Set
from pathlib import Path
from app.config import settings

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


def build_context(
    retrieved_results: List[Dict[str, Any]],
    max_tokens: int = 0,
    file_stats: Optional[Dict[str, Any]] = None,
    folder_profiles_text: str = "",
) -> str:
    """Formats retrieved snippets into a single context string for the LLM.

    Optimisations over the original:
    - Deduplicates by file path (max 2 snippets per file) for diversity.
    - Truncates each snippet to a character budget so one long chunk
      doesn't monopolise the context window.
    - Prioritises high-scoring snippets first.
    """
    if not retrieved_results and not file_stats and not folder_profiles_text:
        return "No relevant context found."

    if max_tokens <= 0:
        max_tokens = settings.context_max_tokens

    context_parts = []
    total_len = 0
    max_chars = max_tokens * 4  # ~4 chars per token heuristic
    max_snippet_chars = max_chars // max(len(retrieved_results), 1)  # Fair share per snippet
    max_snippet_chars = max(max_snippet_chars, 600)  # But at least 600 chars

    # Prepend folder profiles when available (project-level understanding)
    if folder_profiles_text:
        context_parts.append(folder_profiles_text)
        total_len += len(folder_profiles_text)

    # Prepend file statistics when available
    if file_stats:
        stats_block = _format_file_stats(file_stats)
        context_parts.append(stats_block)
        total_len += len(stats_block)

    # Deduplicate: max 2 snippets from the same file for diversity
    deduplicated = _deduplicate_by_file(retrieved_results)
    
    for i, res in enumerate(deduplicated):
        snippet_id = i + 1
        path = res.get("file_path", "Unknown File")
        text = res.get("text", "")
        
        # Truncate individual snippets to their fair share of the budget
        if len(text) > max_snippet_chars:
            text = text[:max_snippet_chars] + "…"

        part = f"""Snippet {snippet_id} [{path}]:
{text}
---
"""
        if total_len + len(part) > max_chars:
            break
            
        context_parts.append(part)
        total_len += len(part)
            
    return "\n".join(context_parts)
