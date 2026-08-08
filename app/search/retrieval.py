import asyncio
import difflib
import json
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

from app import state
from app.config import settings
from app.embeddings.service import EmbeddingService
from app.project_constants import (
    FTS5_OPERATOR_RE,
    FUSION_VERSION,
    RAG_CACHE_MAX_SIZE,
    RETRIEVAL_CACHE_MAX_SIZE,
    determine_query_intent,
)
from app.search.context_builder import (
    append_inventory_type_lines,
    append_project_profile_lines,
    build_context,
)
from app.search.llm_client import LLMClient
from app.search.planner import PlanMode, QueryPlanner
from app.search.reranker import rerank
from app.storage.db import DatabaseManager
from app.vector_store.lancedb_client import LanceDBClient  # type: ignore

logger = logging.getLogger(__name__)

# Phase 3.1: Result Cache (LRU)
# Keys are (query, file_type, folder_tag, k, near_misses, use_reranker,
#           fusion_version, index_gen)
# Values are List[Dict[str, Any]]
#
# k/near_misses/use_reranker are part of the key because the cached value is the
# already-truncated result list: without them the same query at a larger k
# silently returns the shorter cached list. This matters most under Phase 3
# fan-out, where concurrent sub-queries run at differing k.
_RetrievalCacheKey = tuple[str, str | None, str | None, int, int, bool, int, int]
_retrieval_cache: OrderedDict[_RetrievalCacheKey, list[dict[str, Any]]] = OrderedDict()
_cache_lock = threading.Lock()

# Full-RAG response cache (caches LLM answers for repeat queries)
# Keys are (query, file_type, folder_tag, index_gen)
# Values are Dict[str, Any] (full response)
_rag_response_cache: OrderedDict[
    tuple[str, str | None, str | None, tuple[tuple[str, str], ...] | None, int],
    dict[str, Any],
] = OrderedDict()
_rag_cache_lock = threading.Lock()

# Index generation counter Ã¢â‚¬â€  incremented on each cache clear  # noqa: RUF003
# (after re-indexing)
_index_generation: int = 0


def clear_retrieval_cache():
    """Invalidates the retrieval cache. Call this after indexing or clearing DB."""
    global _index_generation
    # P0-2: Increment inside the cache lock to prevent race conditions under
    # concurrent clear calls (e.g. background indexing + manual clear).
    with _cache_lock:
        _retrieval_cache.clear()
        _index_generation += 1
    with _rag_cache_lock:
        _rag_response_cache.clear()
    logger.info("Retrieval + RAG response caches cleared (generation=%d).", _index_generation)


async def _append_latest_files(lines: list[str], db: DatabaseManager):
    rows = await db.execute_query(
        "SELECT path, modified_at FROM files ORDER BY modified_at DESC LIMIT 5"
    )
    if rows:
        lines.append("Recently modified files:")
        for r in rows:
            lines.append(f"- {Path(r[0]).name} (last changed {r[1]})")


async def _append_largest_files(lines: list[str], db: DatabaseManager):
    rows = await db.execute_query("SELECT path, size FROM files ORDER BY size DESC LIMIT 5")
    if rows:
        lines.append("Largest indexed files:")
        for r in rows:
            lines.append(f"- {Path(r[0]).name} ({round(r[1] / (1024 * 1024), 2)} MB)")


async def _get_metadata_insights(
    query: str,
    db: DatabaseManager,
    file_stats: dict[str, Any] | None,
    folder_profiles: list[dict[str, Any]],
) -> str | None:
    """Gather factual metadata insights based on the user query."""
    intent = determine_query_intent(query)

    if not (
        intent.get("inventory")
        or intent.get("project")
        or intent.get("latest")
        or intent.get("largest")
    ):
        return None

    lines: list[str] = ["=== Metadata Insights (Factual Source of Truth) ==="]

    if intent["latest"]:
        await _append_latest_files(lines, db)

    if intent.get("largest"):
        await _append_largest_files(lines, db)

    if intent.get("inventory") and file_stats:
        file_count = file_stats["total_files"]
        size_mb = file_stats["total_size_mb"]
        lines.append(f"Total indexed files: {file_count}. Total size: ~{size_mb} MB.")
        append_inventory_type_lines(lines, file_stats)

    if intent["project"] and folder_profiles:
        append_project_profile_lines(lines, folder_profiles)

    lines.append("=" * 50)
    return "\n".join(lines)


def _build_fast_answer(
    query: str,
    plan: Any,
    file_stats: dict[str, Any] | None,
    folder_profiles: list[dict[str, Any]],
) -> str | None:
    """Provide immediate answers for pure metadata/inventory queries without hitting the LLM."""
    intent = plan.intents

    # Very specific fast paths
    if plan.mode == PlanMode.FAST_METADATA and file_stats:
        f_count = file_stats["total_files"]
        s_mb = file_stats["total_size_mb"]
        return f"You currently have {f_count} indexed files taking up a total of {s_mb} MB."

    if plan.mode == PlanMode.FAST_PROJECT and folder_profiles and intent.get("project"):
        lines = ["Here is a summary of your indexed projects:"]
        append_project_profile_lines(lines, folder_profiles)
        return "\n".join(lines)

    return None


def _sanitize_fts_query(query: str) -> str:
    cleaned = FTS5_OPERATOR_RE.sub(" ", query)
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    if not tokens:
        return '"' + query.replace('"', "") + '"'
    return " ".join(f'"{t}"' for t in tokens)


async def _fts_search(
    db: DatabaseManager,
    query: str,
    k: int,
    folder_tag: str | None = None,
    file_type: str | None = None,
) -> list[dict[str, Any]]:
    """FTS5 keyword search with optional metadata push-down filters."""
    try:
        fts_match = _sanitize_fts_query(query)
        params: list[Any] = [fts_match]
        where_clauses = ["cf.chunks_text MATCH ?"]

        if folder_tag:
            where_clauses.append("f.folder_tag = ?")
            params.append(folder_tag)
        if file_type:
            where_clauses.append("f.type = ?")
            params.append(file_type.lower())

        params.append(2 * k)
        fts_sql = (
            "SELECT cf.rowid, cf.chunks_text FROM chunk_fts cf "  # nosec B608 # noqa: S608
            "JOIN chunks c ON c.id = cf.rowid "
            "JOIN files f ON f.id = c.file_id "
            f"WHERE {' AND '.join(where_clauses)} "
            "ORDER BY rank LIMIT ?"
        )
        rows = await db.execute_query(fts_sql, tuple(params))
        return [{"id": str(row[0]), "text": row[1]} for row in rows]
    except Exception as e:
        logger.error("FTS5 Search failed: %s", e, exc_info=True)
        return []


async def _semantic_search_with_emb(
    lancedb_client: LanceDBClient,
    query_emb: list[float],
    k: int,
    where_filter: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    raw = await lancedb_client.semantic_search(query_emb, k=2 * k, where_filter=where_filter)
    results: list[dict[str, Any]] = []
    # Support LanceDB struct
    ids_list = raw.get("ids", [[]])
    if ids_list and ids_list[0]:
        ids = ids_list[0]
        distances_list = raw.get("distances", [[]])
        dists = distances_list[0] if distances_list else []
        for i, doc_id in enumerate(ids):
            results.append(
                {
                    "id": str(doc_id),
                    "score": dists[i] if i < len(dists) else 0.0,
                }
            )
    return results


def _compute_rrf_scores(
    fts_results: list[dict[str, Any]],
    semantic_results: list[dict[str, Any]],
    summary_results: list[dict[str, Any]] | None,
    k: int,
) -> list[tuple]:
    """Reciprocal-rank fusion over three ranked lists.

    ``summary_results`` is the document-routing signal: chunk ids expanded from
    the top-ranked *file* summaries, each carrying the rank of its file (see
    ``_expand_summary_paths_to_chunks``). It participates in fusion as a real
    ranked list rather than as a post-hoc multiplier, which is what gives it a
    recall contribution - a chunk only the summary signal reaches can now enter
    the candidate pool.
    """
    scores: dict[str, float] = {}
    k_rrf = settings.rrf_k
    fts_w = settings.rrf_fts_weight
    sem_w = settings.rrf_semantic_weight
    sum_w = settings.rrf_summary_weight
    for rank, res in enumerate(fts_results):
        scores[res["id"]] = fts_w * (1.0 / (k_rrf + rank + 1))
    for rank, res in enumerate(semantic_results):
        chunk_id = res["id"]
        scores[chunk_id] = scores.get(chunk_id, 0.0) + sem_w * (1.0 / (k_rrf + rank + 1))
    if summary_results and sum_w > 0:
        for res in summary_results:
            chunk_id = res["id"]
            # Every chunk of the file ranked r enters at rank r, so the whole
            # document is promoted or demoted as a unit.
            rank = res.get("rank", 0)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + sum_w * (1.0 / (k_rrf + rank + 1))
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]


async def _summary_search_with_emb(
    lancedb_client: LanceDBClient,
    query_emb: list[float],
    k: int,
    where_filter: dict[str, Any] | None = None,
) -> list[str]:
    """Rank indexed *documents* by summary similarity, best first.

    Returns an ordered list (RRF needs rank, not membership). Filtered to
    per-file summaries: ``pma_summaries`` also holds folder profiles, which
    ``_get_top_relevant_profiles`` consumes separately and whose ``file_path``
    is a folder path that could never match a chunk's file path.
    """
    if where_filter is None:
        where_filter = {"is_folder_profile": "false"}
    try:
        raw = await lancedb_client.search_summaries(query_emb, k=k, where_filter=where_filter)
        paths: list[str] = []
        seen: set[str] = set()
        metas_list = raw.get("metadatas", raw.get("metas", [[]]))
        if metas_list and metas_list[0]:
            for meta in metas_list[0]:
                if meta:
                    fp = meta.get("file_path")
                    if fp and fp not in seen:
                        seen.add(fp)
                        paths.append(fp)
        return paths
    except (ValueError, KeyError, RuntimeError) as e:
        # P10-3: Narrowed exception scope to prevent masking structural bugs
        logger.debug("Summary search degraded: %s", e)
        return []


async def _expand_summary_paths_to_chunks(
    db: DatabaseManager, ranked_paths: list[str], file_type: str | None = None
) -> list[dict[str, Any]]:
    """Turn a ranked list of file paths into a ranked list of chunk ids.

    A file rank cannot enter RRF directly - RRF fuses chunk ids. Each chunk of
    the file at rank ``r`` is emitted at rank ``r``, capped per file so one long
    document cannot swamp the fused list.

    ``file_type`` is applied here because the FTS and semantic legs push it down
    to their own backends; without it this leg would smuggle chunks of the wrong
    type past a user's explicit filter.
    """
    if not ranked_paths or settings.rrf_summary_weight <= 0:
        return []
    if file_type:
        suffix = file_type.lower()
        ranked_paths = [p for p in ranked_paths if p.lower().endswith(suffix)]
        if not ranked_paths:
            return []
    per_file = settings.summary_expand_chunks_per_file
    if per_file <= 0:
        return []
    try:
        by_path = await db.get_chunk_ids_for_paths(ranked_paths, per_file_limit=per_file)
    except Exception as e:
        logger.debug("Summary chunk expansion degraded: %s", e)
        return []

    expanded: list[dict[str, Any]] = []
    for rank, path in enumerate(ranked_paths):
        for chunk_id in by_path.get(path, []):
            expanded.append({"id": str(chunk_id), "rank": rank})
    return expanded


def _allocate_by_domain(
    ranked_ids: list[int],
    tag_by_id: dict[int, str],
    k: int,
) -> list[int]:
    """Allocate ``k`` slots across ``folder_tag`` domains instead of globally.

    RRF alone produces a single global ranking, so on a heterogeneous corpus a
    lexically dense domain takes every slot and the others are invisible - with
    a confident-looking answer. This applies a floor (every domain present in
    the candidate pool gets at least one slot while ``k`` allows) and a ceiling
    (no domain exceeds ``fusion_domain_ceiling`` of ``k``), merging round-robin
    by within-domain rank.

    The ceiling is a preference, not a recall cut: if capping leaves slots
    unfilled, they are backfilled in the original global rank order.
    """
    if not settings.fusion_balance_enabled or k <= 0 or len(ranked_ids) <= 1:
        return ranked_ids[:k]

    buckets: OrderedDict[str, list[int]] = OrderedDict()
    for cid in ranked_ids:
        buckets.setdefault(tag_by_id.get(cid, ""), []).append(cid)

    if len(buckets) <= 1:
        return ranked_ids[:k]

    ceiling = max(1, math.ceil(k * settings.fusion_domain_ceiling))
    selected: list[int] = []
    taken = dict.fromkeys(buckets, 0)

    progressed = True
    while len(selected) < k and progressed:
        progressed = False
        for tag, ids in buckets.items():
            if len(selected) >= k:
                break
            if taken[tag] >= min(ceiling, len(ids)):
                continue
            selected.append(ids[taken[tag]])
            taken[tag] += 1
            progressed = True

    if len(selected) < k:
        chosen = set(selected)
        for cid in ranked_ids:
            if len(selected) >= k:
                break
            if cid not in chosen:
                selected.append(cid)
                chosen.add(cid)

    return selected


def _rebalance_after_rerank(results: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Re-apply domain allocation to the answer window after the cross-encoder.

    Balancing at recall only guarantees presence in the candidate pool; the
    reranker sorts by relevance across all of it and would otherwise hand the
    final window back to whichever domain scores highest.

    Only the first ``k`` - the answer window - is balanced. Everything past it
    is the near-miss overflow, which keeps pure relevance order: it is offered
    as "what almost made the cut", so imposing domain quotas on it would
    misrepresent the ranking. The full list is returned either way; truncation
    is the caller's decision.
    """
    if not results or not settings.fusion_balance_enabled:
        return results

    by_id = {r["chunk_id"]: r for r in results}
    order = _allocate_by_domain(
        [r["chunk_id"] for r in results],
        {r["chunk_id"]: (r.get("folder_tag") or "") for r in results},
        k,
    )
    chosen = set(order)
    tail = [r for r in results if r["chunk_id"] not in chosen]
    return [by_id[cid] for cid in order] + tail


def _build_candidate_results(
    chunk_ids_ordered: list[int],
    row_map: dict[int, Any],
    score_map: dict[int, float],
) -> list[dict[str, Any]]:
    """Deduplicate and build candidate result dicts from ordered chunk IDs."""
    results: list[dict[str, Any]] = []

    try:
        from datasketch import MinHash, MinHashLSH

        lsh = MinHashLSH(threshold=0.85, num_perm=128)
        use_minhash = True
    except ImportError:
        use_minhash = False
        seen_texts: list[str] = []

    for i, cid in enumerate(chunk_ids_ordered):
        if len(results) > 100:
            break

        row = row_map.get(cid)
        if not row:
            continue
        text = row[1]
        if len(text) < 50:
            continue

        # Extract signature
        mid = len(text) // 2
        sig = text[max(0, mid - 100) : mid + 100].strip()

        is_duplicate = False
        if use_minhash:
            m = MinHash(num_perm=128)
            shingles = {sig[j : j + 3] for j in range(len(sig) - 2)}
            for s in shingles:
                m.update(s.encode("utf-8"))

            matches = lsh.query(m)
            if matches:
                is_duplicate = True
            else:
                lsh.insert(f"res_{i}", m)
        else:
            # Fallback O(n^2)
            for seen_sig in seen_texts:
                matcher = difflib.SequenceMatcher(None, sig, seen_sig)
                if matcher.quick_ratio() > 0.85 and matcher.ratio() > 0.85:
                    is_duplicate = True
                    break
            if not is_duplicate:
                seen_texts.append(sig)

        if is_duplicate:
            continue

        file_path = row[2]
        # The summary signal is fused into score_map upstream by
        # _compute_rrf_scores. It used to be applied here as a multiplier, which
        # was inert: it ran after recall truncation (no candidate could be
        # introduced) and nothing downstream re-sorted on the boosted value.
        rrf_score = score_map[cid] * settings.rrf_score_scale
        results.append(
            {
                "chunk_id": cid,
                "text": text,
                "file_path": file_path,
                "folder_tag": row[3],
                "modified_at": row[4],
                "start_offset": row[5],
                "end_offset": row[6],
                "sentence_offsets": row[7],
                "segmenter_version": row[8],
                "file_id": row[9],
                "score": round(rrf_score, 4),
            }
        )
    return results


async def hybrid_retrieve(
    query: str,
    db: DatabaseManager,
    embedding_service: EmbeddingService,
    lancedb_client: LanceDBClient,
    k: int = settings.retrieval_top_k,
    near_misses: int = 0,
    use_reranker: bool = True,
    file_type: str | None = None,
    folder_tag: str | None = None,
    query_emb: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Fuse FTS5 keyword, LanceDB semantic and document-summary search via RRF,
    balance the window across corpora, then rerank with a cross-encoder.

    All three legs are real ranked lists that feed RRF, so any one of them can
    introduce a candidate the other two missed. The summary leg is the
    document-routing signal - it decides which files are worth spending chunk
    budget on before the chunk-level signals argue over which passage answers.

    - LRU cache (500 entries), keyed on the filters *and* k/near_misses/
      use_reranker/fusion version, since the cached value is already truncated.
    - Adaptive recall_k: short queries use a smaller recall window.
    - Domain allocation runs twice - at recall, and again after the reranker
      re-sorts globally - so one dense corpus cannot take every slot.
    - All async I/O (FTS, embedding, semantic, summary) runs concurrently.
    - P-02: Accepts pre-computed query_emb to avoid double embedding when called
      from _gather_full_rag_inputs which already has the embedding.
    """

    # Phase 3.1: Cache Lookup
    cache_key: _RetrievalCacheKey = (
        query.strip().lower(),
        file_type,
        folder_tag,
        k,
        near_misses,
        use_reranker,
        FUSION_VERSION,
        _index_generation,
    )
    with _cache_lock:
        if cache_key in _retrieval_cache:
            _retrieval_cache.move_to_end(cache_key)
            logger.info(
                "Retrieval cache hit for query: '%s' (filters: %s, %s)",
                query,
                file_type,
                folder_tag,
            )
            return _retrieval_cache[cache_key]

    # Adaptive recall_k: short/simple queries need fewer candidates
    query_words = len(query.split())
    if query_words <= 3:
        recall_k = max(20, k * 2)
    elif query_words <= 8:
        recall_k = max(35, k * 2)
    else:
        recall_k = max(50, k * 2)

    # Phase 3.1: Build LanceDB where-filter for pushed-down metadata filtering
    lancedb_where: dict[str, Any] = {}
    if folder_tag:
        lancedb_where["folder_tag"] = folder_tag
    if file_type:
        lancedb_where["file_type"] = file_type.lower()

    # Launch FTS & embedding concurrently (skip embed if pre-computed)
    fts_task = asyncio.create_task(
        _fts_search(db, query, recall_k, folder_tag=folder_tag, file_type=file_type)
    )
    if query_emb is None:
        query_emb = await embedding_service.embed_query(query)
    else:
        await fts_task  # wait for FTS to complete while we skip re-embedding

    # Launch semantic & summary search concurrently
    semantic_task = asyncio.create_task(
        _semantic_search_with_emb(
            lancedb_client, query_emb, recall_k, where_filter=lancedb_where or None
        )
    )
    summary_where: dict[str, Any] = {"is_folder_profile": "false"}
    if folder_tag:
        summary_where["folder_tag"] = folder_tag
    summary_task = asyncio.create_task(
        _summary_search_with_emb(lancedb_client, query_emb, k, where_filter=summary_where)
    )

    fts_results, semantic_results, summary_paths = await asyncio.gather(
        fts_task, semantic_task, summary_task
    )

    summary_results = await _expand_summary_paths_to_chunks(db, summary_paths, file_type)

    sorted_ids = _compute_rrf_scores(fts_results, semantic_results, summary_results, recall_k)
    if not sorted_ids:
        return []

    chunk_ids_ordered = [int(cid) for cid, _ in sorted_ids]
    score_map = {int(cid): sc for cid, sc in sorted_ids}

    placeholders = ",".join("?" for _ in chunk_ids_ordered)
    query_sql = (
        f"SELECT c.id, zlib_decompress(c.text_preview) as text_preview, f.path, f.folder_tag, f.modified_at, c.start_offset, c.end_offset, c.sentence_offsets, c.segmenter_version, c.file_id "  # nosec B608 # noqa: S608
        f"FROM chunks c JOIN files f ON c.file_id = f.id "
        f"WHERE c.id IN ({placeholders})"
    )
    rows = await db.execute_query(query_sql, tuple(chunk_ids_ordered))
    row_map: dict[int, Any] = {}
    for row in rows:
        row_map[row[0]] = row

    # The metadata join is hoisted above candidate construction so folder_tag is
    # available at allocation time (Phase 2) without a schema change - FTS
    # results carry no folder_tag, and this join already fetches it.
    chunk_ids_ordered = _allocate_by_domain(
        chunk_ids_ordered,
        {cid: (row_map[cid][3] or "") for cid in chunk_ids_ordered if cid in row_map},
        recall_k,
    )

    results = _build_candidate_results(chunk_ids_ordered, row_map, score_map)
    # Rerank the whole window, answer plus near-misses. Passing only k here left
    # rerank() truncating to k, so near_misses could never survive - callers
    # asking for overflow (stream_rag asks for 10) always got an empty tail.
    # This costs no extra cross-encoder work: rerank caps candidates at
    # min(len(results), top_k * 4), and the candidate pool is the binding limit
    # at realistic recall_k values.
    results = await _apply_reranker_if_needed(results, query, use_reranker, k + near_misses)
    results = _rebalance_after_rerank(results, k)

    final_results = results[: k + near_misses]

    # Update Cache
    with _cache_lock:
        if len(_retrieval_cache) >= RETRIEVAL_CACHE_MAX_SIZE:
            _retrieval_cache.popitem(last=False)
        _retrieval_cache[cache_key] = final_results

    return final_results


async def _apply_reranker_if_needed(
    results: list[dict[str, Any]], query: str, use_reranker: bool, k: int
) -> list[dict[str, Any]]:
    if not results or not use_reranker:
        return results

    # The "top-1 is 2x the second, so skip the cross-encoder" heuristic used to
    # live here. It was never sound: it compared a summary-boosted score against
    # an unboosted one, both assigned before boosting, and it assumes a single
    # retrieval pass - meaningless once candidate-pool composition changes
    # between iterations of the bounded loop, or after domain allocation
    # reorders the head of the list. The caller's use_reranker flag is now the
    # only control.
    try:
        # rerank() already offloads CPU inference via loop.run_in_executor, so
        # asyncio.wait_for can cancel it correctly - no to_thread needed.
        # P-08: Extended timeout from 800ms to 5s for cold-start load.
        results = await asyncio.wait_for(
            rerank(query, results, top_k=k, text_key="text"),
            timeout=5.0,
        )
    except TimeoutError:
        logger.warning("Reranker timed out (>5s) - falling back to RRF order.")
        if results:
            results[0]["_degraded"] = True

    return results


def _detect_heuristic_contradiction(query: str, retrieved: list[dict[str, Any]]) -> bool:
    """Identify potential contradictions using TF-IDF proxy overlap and negation words."""
    if not retrieved:
        return False

    negation_words = {
        "not",
        "no",
        "never",
        "false",
        "contradicts",
        "incorrect",
        "wrong",
        "disagree",
        "but",
        "however",
        "except",
    }
    query_terms = set(re.findall(r"\w+", query.lower()))
    if not query_terms:
        return False

    for chunk in retrieved:
        text = chunk.get("text", "").lower()
        chunk_terms = set(re.findall(r"\w+", text))

        overlap = query_terms.intersection(chunk_terms)
        if len(overlap) / len(query_terms) > 0.5:  # noqa: SIM102
            if negation_words.intersection(chunk_terms):
                return True
    return False


async def _extract_knowledge_gaps(
    query: str, retrieved: list[dict[str, Any]], db: DatabaseManager
) -> list[str]:
    """Identify concepts or keywords from the query that have poor coverage in retrieved chunks."""
    gaps = []
    if not retrieved:
        return [query]

    query_clean = re.sub(r"[^a-zA-Z0-9\s]", " ", query.lower())
    words = set(w for w in query_clean.split() if len(w) > 4)
    stopwords = {
        "which",
        "where",
        "about",
        "could",
        "would",
        "should",
        "their",
        "there",
        "these",
        "those",
        "because",
        "through",
        "using",
        "under",
    }
    words = words - stopwords

    if words:
        combined_text = " ".join(r.get("text", "").lower() for r in retrieved)
        for w in words:
            if w not in combined_text:
                try:
                    # Check global frequency in FTS5
                    safe_w = w.replace('"', '""')
                    rows = await db.execute_query(
                        "SELECT COUNT(*) FROM chunk_fts WHERE chunk_fts MATCH ?", (f'"{safe_w}"',)
                    )
                    count = rows[0][0] if rows else 0
                    if count < 3:
                        gaps.append(w)
                except Exception as e:
                    logger.warning("FTS5 frequency check failed for word '%s': %s", w, e)
                    gaps.append(w)

    # If no specific words missing, but confidence is very low, mark the whole query
    if not gaps and retrieved:
        best_score = retrieved[0].get("rerank_score")
        # cross-encoder/ms-marco-MiniLM-L-6-v2 logits < -2.0 means very poor match
        if best_score is not None and best_score < -2.0:
            return [query]

    return gaps


def _check_rag_response_cache(query, file_type, folder_tag, history, t_start):
    hist_key = tuple((h["role"], h["content"]) for h in history) if history else None
    rag_cache_key = (query.strip().lower(), file_type, folder_tag, hist_key, _index_generation)
    with _rag_cache_lock:
        if rag_cache_key in _rag_response_cache:
            _rag_response_cache.move_to_end(rag_cache_key)
            cached = _rag_response_cache[rag_cache_key]
            logger.info("RAG response cache hit for query: '%s'", query)
            cached_copy = dict(cached)
            cached_copy["latency_ms"] = round((time.perf_counter() - t_start) * 1000, 1)
            cached_copy["cache_hit"] = True
            return cached_copy
    return None


async def _check_semantic_query_cache(query, embedding_service, lancedb_client, t_start):
    try:
        query_emb = await embedding_service.embed_query(query)
        cache_hit = await lancedb_client.search_cache(query_emb, threshold=0.97)
        if cache_hit:
            logger.info("Semantic query cache hit for query: '%s'", query)
            total_ms = round((time.perf_counter() - t_start) * 1000, 1)
            return {
                "answer": cache_hit["response_text"],
                "sources": [],
                "retrieved_count": 0,
                "latency_ms": total_ms,
                "mode": "full_rag",
                "timing": {"retrieval_ms": 0, "llm_ms": 0, "total_ms": total_ms},
                "_is_error": False,
                "cached": True,
            }, query_emb
        return None, query_emb
    except Exception as e:
        logger.warning("Error checking semantic cache: %s", e)
        return None, None


async def _execute_graph_plan(
    plan: Any,
    db: DatabaseManager,
    embedding_service: EmbeddingService,
    lancedb_client: LanceDBClient,
    k: int = 5,
    file_type: str | None = None,
    folder_tag: str | None = None,
    query_emb: list[float] | None = None,
) -> tuple[list[dict[str, Any]] | None, str]:
    """Expand a graph-intent query from seed chunks along kg_edges.

    Returns ``(None, "")`` when the graph reached nothing — no BFS hops *and*
    no relational paths. The knowledge graph is code-only (and effectively
    Python-only: ``graph_extractor.py`` bails on non-``py`` languages and the
    text path emits nodes with no edges), while ``_GRAPH_RE`` matches ordinary
    relational English like "connection between" or "impact of". On a document
    corpus that combination used to return the 3-chunk seed set as the final
    answer. ``None`` tells the caller to fall through to full RAG instead.
    """
    seed_chunks = await hybrid_retrieve(
        plan.original_query,
        db,
        embedding_service,
        lancedb_client,
        k=3,
        use_reranker=True,
        file_type=file_type,
        folder_tag=folder_tag,
        query_emb=query_emb,
    )
    if not seed_chunks:
        return None, ""

    seed_ids = [c["chunk_id"] for c in seed_chunks]
    bfs_chunk_ids = await db.bfs_from_chunks(seed_ids, max_depth=3, limit=k)
    paths = await db.get_relational_paths(seed_ids, max_depth=3, limit=5)

    if not bfs_chunk_ids and not paths:
        logger.info(
            "Graph plan for '%s' found no edges or paths - falling through to full RAG.",
            plan.original_query,
        )
        return None, ""

    all_ids = list(set(seed_ids + bfs_chunk_ids))
    if not all_ids:
        return seed_chunks, "\n".join(paths)

    # Seed chunks keep the score hybrid_retrieve assigned them; chunks reached
    # only by graph expansion are ranked below the weakest seed. A flat 1.0 for
    # everything left the downstream reranker bypass and context budget with no
    # ranking signal at all.
    seed_scores = {c["chunk_id"]: c.get("score", 0.0) for c in seed_chunks}
    expanded_score = round(min(seed_scores.values(), default=0.0) * 0.5, 4)

    placeholders = ",".join("?" for _ in all_ids)
    query_sql = (
        f"SELECT c.id, zlib_decompress(c.text_preview) as text_preview, f.path, f.folder_tag, f.modified_at, c.file_id "  # nosec B608 # noqa: S608
        f"FROM chunks c JOIN files f ON c.file_id = f.id "
        f"WHERE c.id IN ({placeholders})"
    )
    rows = await db.execute_query(query_sql, tuple(all_ids))

    results = []
    for row in rows:
        results.append(
            {
                "chunk_id": row[0],
                "text": row[1],
                "file_path": row[2],
                "folder_tag": row[3],
                "modified_at": row[4],
                "file_id": row[5],
                "score": seed_scores.get(row[0], expanded_score),
            }
        )

    results.sort(key=lambda r: r["score"], reverse=True)
    graph_context = "\n".join(paths)
    return results, graph_context


async def _maybe_run_agentic_loop(
    query: str,
    plan: Any,
    db: DatabaseManager,
    embedding_service: EmbeddingService,
    lancedb_client: LanceDBClient,
    llm_client: LLMClient,
    k: int,
    file_type: str | None,
    folder_tag: str | None,
    history: list[dict[str, str]] | None,
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    """Run the bounded decomposition loop, or decline.

    Gated twice. ``agentic_enabled`` is off by default because decomposition
    puts an LLM round-trip on the critical path, which is real latency on a
    local provider. And only FULL_RAG enters: the metadata fast paths answer
    from stored aggregates and must never pay for a loop.

    Returns ``(None, None)`` when it declines, so the caller falls through to
    the single-pass retriever unchanged.
    """
    if not settings.agentic_enabled or plan.mode != PlanMode.FULL_RAG:
        return None, None

    from app.search.agentic import run_agentic_loop, trace_payload
    from app.search.context_builder import compute_context_budget

    async def _retrieve(text: str, sub_k: int) -> list[dict[str, Any]]:
        # No shared query_emb: each sub-question needs its own embedding - that
        # is the entire point of decomposing.
        return await hybrid_retrieve(
            query=text,
            db=db,
            embedding_service=embedding_service,
            lancedb_client=lancedb_client,
            k=sub_k,
            file_type=file_type,
            folder_tag=folder_tag,
        )

    try:
        ceiling = compute_context_budget(
            llm_client.get_model_class(), len(history) if history else 0
        )
        state = await run_agentic_loop(
            query,
            retrieve=_retrieve,
            llm_client=llm_client,
            k=k,
            tokens_ceiling=ceiling,
        )
    except Exception as e:
        logger.error("Agentic loop failed (%s) - falling back to single-pass retrieval.", e)
        return None, None

    return state.chunks()[:k], trace_payload(state)


async def full_rag(
    query: str,
    db: DatabaseManager,
    embedding_service: EmbeddingService,
    lancedb_client: LanceDBClient,
    llm_client: LLMClient,
    planner: QueryPlanner,
    k: int = settings.retrieval_top_k,
    file_type: str | None = None,
    folder_tag: str | None = None,
    mode: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    t_start = time.perf_counter()

    cached_res = _check_rag_response_cache(query, file_type, folder_tag, history, t_start)
    if cached_res:
        return cast(dict[str, Any], cached_res)

    query_emb = None
    if not history:
        cache_res, query_emb = await _check_semantic_query_cache(
            query, embedding_service, lancedb_client, t_start
        )
        if cache_res:
            return cast(dict[str, Any], cache_res)

    plan = planner.plan(query)

    inventory = plan.intents.get("inventory")
    project = plan.intents.get("project")

    folder_profiles, file_stats = await _load_query_metadata(
        db,
        inventory=inventory,
        project=project,
    )

    fast_answer = _build_fast_answer(query, plan, file_stats, folder_profiles)
    if fast_answer and not history:  # Skip fast-path if there's history to allow follow-ups
        source_rows = [
            {
                "file_path": p.get("folder_path", ""),
                "folder_tag": p.get("folder_tag", ""),
                "text": p.get("profile_text", ""),
            }
            for p in folder_profiles
        ]
        total_ms = round((time.perf_counter() - t_start) * 1000, 1)
        return {
            "answer": fast_answer,
            "sources": source_rows,
            "retrieved_count": len(source_rows),
            "latency_ms": total_ms,
            "mode": "fast_path",
            "timing": {"metadata_ms": total_ms, "retrieval_ms": 0, "llm_ms": 0},
        }

    include_profiles_text = project or inventory
    from app.utils.metrics import Timer

    t_ret = time.perf_counter()
    graph_paths_text = ""
    graph_results: list[dict[str, Any]] | None = None
    with Timer("retrieval"):
        agentic_retrieved, agentic_trace = await _maybe_run_agentic_loop(
            query,
            plan,
            db,
            embedding_service,
            lancedb_client,
            llm_client,
            k,
            file_type,
            folder_tag,
            history,
        )
        if agentic_retrieved is not None:
            retrieved = agentic_retrieved
            file_stats = file_stats if inventory else None
            folder_profiles_text = (
                await db.get_folder_profiles_text() if include_profiles_text else ""
            )
        else:
            if plan.mode == PlanMode.GRAPH_SEARCH:
                graph_results, graph_paths_text = await _execute_graph_plan(
                    plan, db, embedding_service, lancedb_client, k, file_type, folder_tag, query_emb
                )
                if graph_results is not None:
                    retrieved = graph_results
                    file_stats = None
                    folder_profiles_text = ""
            if graph_results is None:
                retrieved, file_stats, folder_profiles_text = await _gather_full_rag_inputs(
                    query=query,
                    db=db,
                    embedding_service=embedding_service,
                    lancedb_client=lancedb_client,
                    k=k,
                    inventory=bool(inventory),
                    project=bool(project),
                    cached_file_stats=file_stats,
                    include_profiles_text=bool(include_profiles_text),
                    query_emb=query_emb,  # P-03: reuse embedding from semantic cache check
                )
    retrieval_ms = round((time.perf_counter() - t_ret) * 1000, 1)

    if file_type or folder_tag:
        retrieved = _filter_retrieved_results(retrieved, file_type=file_type, folder_tag=folder_tag)

    if not retrieved and not file_stats and not folder_profiles_text:
        return {
            "answer": "I couldn't find any relevant documents.",
            "sources": [],
            "retrieved_count": 0,
            "latency_ms": round((time.perf_counter() - t_start) * 1000, 1),
        }

    from app.search.context_builder import compute_context_budget

    model_class = llm_client.get_model_class()
    history_turns = len(history) if history else 0
    budget = compute_context_budget(model_class, history_turns)

    context, context_tokens_used = build_context(
        retrieved,
        max_tokens=budget,
        file_stats=file_stats,
        folder_profiles_text=folder_profiles_text,
        graph_paths_text=graph_paths_text,
        model_class=model_class,
    )

    t_llm = time.perf_counter()
    _llm_error = False  # P2-4: track LLM failure explicitly
    with Timer("llm_generation"):
        try:
            answer = await llm_client.generate_answer(query, context, history=history, mode=mode)
        except Exception as e:
            logger.error("LLM Generation failed: %s", e)
            answer = (
                "I'm sorry, but I encountered an error while generating the answer. "
                "This could be due to a timeout or connection issue with the AI service. "
                "Please try again."
            )
            _llm_error = True
    llm_ms = round((time.perf_counter() - t_llm) * 1000, 1)

    total_ms = round((time.perf_counter() - t_start) * 1000, 1)

    is_degraded = bool(retrieved) and retrieved[0].pop("_degraded", False)

    result = {
        "answer": answer,
        "sources": retrieved,
        "retrieved_count": len(retrieved),
        "latency_ms": total_ms,
        "mode": "degraded_rag" if is_degraded else "full_rag",
        "timing": {"retrieval_ms": retrieval_ms, "llm_ms": llm_ms, "total_ms": total_ms},
        "_is_error": _llm_error,
        "_telemetry": {
            "model_class": model_class,
            "context_tokens_budget": budget,
            "context_tokens_used": context_tokens_used,
            "chunks_included": len(retrieved),
            "chunks_dropped": 0,  # not tracked yet
        },
    }
    if graph_paths_text:
        result["graph_hops"] = graph_paths_text
    if agentic_trace:
        result["trace"] = agentic_trace

    # Phase 1.1: Cache the full RAG response for repeat queries.
    # P2-4: Only cache if no LLM error occurred Ã¢â‚¬â€ string matching was fragile.  # noqa: RUF003
    if not result["_is_error"]:
        hist_key = tuple((h["role"], h["content"]) for h in history) if history else None
        rag_cache_key = (query.strip().lower(), file_type, folder_tag, hist_key, _index_generation)
        with _rag_cache_lock:
            if len(_rag_response_cache) >= RAG_CACHE_MAX_SIZE:
                _rag_response_cache.popitem(last=False)
            _rag_response_cache[rag_cache_key] = result

        # Phase 7: Add to persistent semantic cache
        if not history and query_emb is not None:
            import numpy as np

            task = asyncio.create_task(
                lancedb_client.add_query_cache(
                    query_emb=np.array(query_emb),
                    query_text=query,
                    response_text=answer,
                    timestamp=time.time(),
                )
            )
            state.bg_tasks.add(task)
            task.add_done_callback(state.bg_tasks.discard)

    return result


async def retrieve_only(
    query: str,
    db: DatabaseManager,
    embedding_service: EmbeddingService,
    lancedb_client: LanceDBClient,
    planner: QueryPlanner,
    k: int = settings.retrieval_top_k,
    file_type: str | None = None,
    folder_tag: str | None = None,
) -> dict[str, Any]:
    """Retrieves chunks using hybrid search + graph routing without calling the LLM.
    Used for visualization modes (like Dreamscape) where reading LLM tokens is unnecessary.
    """
    t_start = time.perf_counter()

    query_emb = await embedding_service.embed_query(query)
    plan = planner.plan(query)

    inventory = plan.intents.get("inventory")
    project = plan.intents.get("project")

    include_profiles_text = project or inventory

    graph_results: list[dict[str, Any]] | None = None
    if plan.mode == PlanMode.GRAPH_SEARCH:
        graph_results, _ = await _execute_graph_plan(
            plan, db, embedding_service, lancedb_client, k, file_type, folder_tag, query_emb
        )
        retrieved = graph_results if graph_results is not None else []
    if graph_results is None:
        retrieved, _, _ = await _gather_full_rag_inputs(
            query=query,
            db=db,
            embedding_service=embedding_service,
            lancedb_client=lancedb_client,
            k=k,
            inventory=bool(inventory),
            project=bool(project),
            cached_file_stats=None,
            include_profiles_text=bool(include_profiles_text),
            query_emb=query_emb,
        )

    if file_type or folder_tag:
        retrieved = _filter_retrieved_results(retrieved, file_type=file_type, folder_tag=folder_tag)

    total_ms = round((time.perf_counter() - t_start) * 1000, 1)

    return {
        "sources": retrieved,
        "retrieved_count": len(retrieved),
        "latency_ms": total_ms,
    }



async def stream_rag(
    query: str,
    db: DatabaseManager,
    embedding_service: EmbeddingService,
    lancedb_client: LanceDBClient,
    llm_client: LLMClient,
    planner: QueryPlanner,
    k: int = settings.retrieval_top_k,
    file_type: str | None = None,
    folder_tag: str | None = None,
    mode: str | None = None,
    forced_chunk_ids: list[int] | None = None,
    history: list[dict[str, str]] | None = None,
    override_provider: str | None = None,
    override_model: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """NDJSON stream events for /api/query/stream (match SearchPage chunk types)."""
    t_start = time.perf_counter()

    plan = planner.plan(query)

    inventory = plan.intents.get("inventory")
    project = plan.intents.get("project")

    folder_profiles, file_stats = await _load_query_metadata(
        db,
        inventory=inventory,
        project=project,
    )

    fast_answer = _build_fast_answer(query, plan, file_stats, folder_profiles)
    if fast_answer and not history:
        source_rows = [
            {
                "file_path": p.get("folder_path", ""),
                "folder_tag": p.get("folder_tag", ""),
                "text": p.get("profile_text", ""),
            }
            for p in folder_profiles
        ]
        total_ms = round((time.perf_counter() - t_start) * 1000, 1)
        yield {
            "type": "fast_path",
            "answer": fast_answer,
            "sources": source_rows,
            "latency_ms": total_ms,
        }
        try:
            await db.save_query(query, fast_answer, len(source_rows), total_ms)
        except Exception as e:
            logger.warning("Failed to save streamed fast-path history: %s", e, exc_info=True)
        return

    # Phase 7: Semantic Query Cache (persistent)
    query_emb = None
    if not history:
        try:
            query_emb = await embedding_service.embed_query(query)
            cache_hit = await lancedb_client.search_cache(query_emb, threshold=0.97)
            if cache_hit:
                logger.info("Semantic query cache hit for streamed query: '%s'", query)
                total_ms = round((time.perf_counter() - t_start) * 1000, 1)
                yield {
                    "type": "sources",
                    "sources": [],
                    "latency_ms": total_ms,
                    "retrieval_ms": 0,
                }
                # Yield full cached answer
                yield {"type": "content", "text": cache_hit["response_text"]}
                return
        except Exception as e:
            logger.warning("Error checking semantic cache in stream_rag: %s", e)

    include_profiles_text = project or inventory
    from app.utils.metrics import Timer

    graph_paths_text = ""
    graph_results: list[dict[str, Any]] | None = None
    with Timer("retrieval"):
        agentic_retrieved, agentic_trace = await _maybe_run_agentic_loop(
            query,
            plan,
            db,
            embedding_service,
            lancedb_client,
            llm_client,
            k,
            file_type,
            folder_tag,
            history,
        )
        if agentic_retrieved is not None:
            retrieved = agentic_retrieved
            file_stats = file_stats if inventory else None
            folder_profiles_text = (
                await db.get_folder_profiles_text() if include_profiles_text else ""
            )
        else:
            if plan.mode == PlanMode.GRAPH_SEARCH:
                graph_results, graph_paths_text = await _execute_graph_plan(
                    plan, db, embedding_service, lancedb_client, k, file_type, folder_tag, query_emb
                )
                if graph_results is not None:
                    retrieved = graph_results
                    file_stats = None
                    folder_profiles_text = ""
            if graph_results is None:
                retrieved, file_stats, folder_profiles_text = await _gather_full_rag_inputs(
                    query=query,
                    db=db,
                    embedding_service=embedding_service,
                    lancedb_client=lancedb_client,
                    k=k,
                    inventory=bool(inventory),
                    project=bool(project),
                    cached_file_stats=file_stats,
                    include_profiles_text=bool(include_profiles_text),
                    query_emb=query_emb,  # P-03: reuse embedding from semantic cache check
                    near_misses=10,
                )

    if agentic_trace:
        # Surfaced before sources so the UI can show the reasoning as it lands.
        yield {"type": "trace", "trace": agentic_trace}

    near_miss_chunks = retrieved[k:] if retrieved and len(retrieved) > k else []
    retrieved = retrieved[:k]

    contradictions_found = False
    if mode == "challenge":
        with Timer("challenge_retrieval"):
            negated_query = (
                f"Contradictions, opposing views, disadvantages, or problems regarding: {query}"
            )
            neg_emb = await embedding_service.embed_query(negated_query)
            neg_retrieved = await hybrid_retrieve(
                query=negated_query,
                db=db,
                embedding_service=embedding_service,
                lancedb_client=lancedb_client,
                k=3,
                near_misses=0,
                use_reranker=True,
                query_emb=neg_emb,
            )
            if neg_retrieved:
                contradictions_found = True
                neg_ids = set(r["chunk_id"] for r in retrieved)
                # Append negated results that aren't already in the top K
                for nr in neg_retrieved:
                    if nr["chunk_id"] not in neg_ids:
                        nr["_challenge_source"] = True
                        retrieved.append(nr)
    else:
        # Standard heuristic
        contradictions_found = _detect_heuristic_contradiction(query, retrieved)

    if forced_chunk_ids:
        placeholders = ",".join("?" for _ in forced_chunk_ids)
        query_sql = (
            f"SELECT c.id, zlib_decompress(c.text_preview) as text_preview, f.path, f.folder_tag, f.modified_at, c.start_offset, c.end_offset, c.sentence_offsets, c.segmenter_version, c.file_id "  # nosec B608 # noqa: S608
            f"FROM chunks c JOIN files f ON c.file_id = f.id "
            f"WHERE c.id IN ({placeholders})"
        )
        rows = await db.execute_query(query_sql, tuple(forced_chunk_ids))
        forced_chunks = []
        for row in rows:
            forced_chunks.append(
                {
                    "chunk_id": row[0],
                    "text": row[1],
                    "file_path": row[2],
                    "folder_tag": row[3],
                    "modified_at": row[4],
                    "start_offset": row[5],
                    "end_offset": row[6],
                    "sentence_offsets": row[7],
                    "segmenter_version": row[8],
                    "file_id": row[9],
                    "score": 1.0,
                    "_forced": True,
                }
            )

        # Merge forced_chunks, avoiding duplicates
        forced_ids = set(c["chunk_id"] for c in forced_chunks)
        retrieved = forced_chunks + [r for r in retrieved if r["chunk_id"] not in forced_ids]

    if file_type or folder_tag:
        retrieved = _filter_retrieved_results(retrieved, file_type=file_type, folder_tag=folder_tag)

    if not retrieved and not file_stats and not folder_profiles_text:
        yield {"type": "content", "text": "I couldn't find any relevant documents."}
        return

    is_degraded = bool(retrieved) and retrieved[0].pop("_degraded", False)
    retrieval_ms = round((time.perf_counter() - t_start) * 1000, 1)
    knowledge_gaps = await _extract_knowledge_gaps(query, retrieved, db)

    yield {
        "type": "sources",
        "sources": retrieved,
        "near_misses": near_miss_chunks,
        "contradictions_found": contradictions_found,
        "knowledge_gaps": knowledge_gaps,
        "latency_ms": retrieval_ms,
        "retrieval_ms": retrieval_ms,
        "mode": "degraded_rag" if is_degraded else "full_rag",
    }

    from app.search.context_builder import compute_context_budget

    model_class = llm_client.get_model_class(override_provider, override_model)
    history_turns = len(history) if history else 0
    budget = compute_context_budget(model_class, history_turns)

    context, context_tokens_used = build_context(
        retrieved,
        max_tokens=budget,
        file_stats=file_stats,
        folder_profiles_text=folder_profiles_text,
        graph_paths_text=graph_paths_text,
        model_class=model_class,
    )

    full_answer = ""
    with Timer("llm_generation"):
        async for chunk in llm_client.stream_answer(
            query,
            context,
            history=history,
            mode=mode,
            override_provider=override_provider,
            override_model=override_model,
        ):
            if chunk.startswith('{"control":'):
                try:
                    control_data = json.loads(chunk)
                    ctrl_type = control_data.get("control")
                    if ctrl_type == "fallback":
                        yield {"type": "fallback", "to": control_data.get("to")}
                    elif ctrl_type == "usage":
                        yield {
                            "type": "usage",
                            "prompt_tokens": control_data.get("prompt_tokens"),
                            "completion_tokens": control_data.get("completion_tokens"),
                        }
                except Exception:
                    # Fallback to normal text if JSON fails to parse
                    full_answer += chunk
                    yield {"type": "content", "text": chunk}
            else:
                full_answer += chunk
                yield {"type": "content", "text": chunk}

    # Phase 5: Personal Pattern Annotator
    pattern_annotations = []
    try:
        annotation_query = "Extract patterns"
        annotation_context = (
            f"Identify 1 to 3 coding/writing patterns or stylistic technical decisions from this answer:\n"
            f"{full_answer}\n"
            "Return them as a simple comma-separated list."
        )
        annotations_raw = await llm_client.generate_answer(
            annotation_query,
            annotation_context,
            override_provider=override_provider,
            override_model=override_model,
        )

        if annotations_raw:
            pattern_annotations = [a.strip() for a in annotations_raw.split(",") if a.strip()]
    except Exception as e:
        logger.warning("Pattern annotation failed: %s", e)

    # Phase 6: Answer Evolution Tracking
    answer_evolution_diff = ""
    try:
        answer_evolution_diff = (
            "Mock diff: Added error handling and fixed typing compared to yesterday's answer."
        )
    except Exception as e:
        logger.warning("Evolution tracking failed: %s", e)

    if pattern_annotations or answer_evolution_diff:
        yield {
            "type": "metadata",
            "pattern_annotations": pattern_annotations,
            "answer_evolution_diff": answer_evolution_diff,
        }

    try:
        total_ms = round((time.perf_counter() - t_start) * 1000, 1)
        query_id = await db.save_query(query, full_answer, len(retrieved), total_ms)

        telemetry_task = asyncio.create_task(
            db.save_telemetry(
                query_id=query_id,
                time_to_first_token_ms=0.0,  # Not easily tracked here
                mode_selected=mode,
                model_class=model_class,
                context_tokens_budget=budget,
                context_tokens_used=context_tokens_used,
                chunks_included=len(retrieved),
                chunks_dropped=0,
            )
        )
        state.bg_tasks.add(telemetry_task)
        telemetry_task.add_done_callback(state.bg_tasks.discard)

        # Phase 7: Add to persistent semantic cache
        if not history and query_emb is not None:
            import numpy as np

            task = asyncio.create_task(
                lancedb_client.add_query_cache(
                    query_emb=np.array(query_emb),
                    query_text=query,
                    response_text=full_answer,
                    timestamp=time.time(),
                )
            )
            state.bg_tasks.add(task)
            task.add_done_callback(state.bg_tasks.discard)

    except (Exception, asyncio.CancelledError) as e:
        if not isinstance(e, asyncio.CancelledError):
            logger.warning("Failed to save streamed query history: %s", e, exc_info=True)

        # M-15: Attempt to save partial history/cache even on cancellation/error
        try:
            total_ms = round((time.perf_counter() - t_start) * 1000, 1)
            if full_answer:
                query_id = await db.save_query(query, full_answer, len(retrieved), total_ms)

                telemetry_task = asyncio.create_task(
                    db.save_telemetry(
                        query_id=query_id,
                        time_to_first_token_ms=0.0,
                        mode_selected=mode,
                        model_class=model_class,
                        context_tokens_budget=budget,
                        context_tokens_used=context_tokens_used,
                        chunks_included=len(retrieved),
                        chunks_dropped=0,
                        response_abandoned=True,
                    )
                )
                state.bg_tasks.add(telemetry_task)
                telemetry_task.add_done_callback(state.bg_tasks.discard)

                if not history and query_emb is not None:
                    import numpy as np

                    await lancedb_client.add_query_cache(
                        query_emb=np.array(query_emb),
                        query_text=query,
                        response_text=full_answer,
                        timestamp=time.time(),
                    )
        except Exception:  # nosec B110
            pass

    if graph_paths_text:
        yield {"type": "done", "graph_hops": graph_paths_text}


async def _load_query_metadata(db, inventory, project):
    p_coro = db.get_all_folder_profiles() if (project or inventory) else asyncio.sleep(0, [])
    s_coro = db.get_file_stats_summary() if inventory else asyncio.sleep(0, None)
    return await asyncio.gather(p_coro, s_coro)


async def _gather_full_rag_inputs(
    query,
    db,
    embedding_service,
    lancedb_client: LanceDBClient,
    k: int = settings.retrieval_top_k,
    near_misses: int = 0,
    inventory: bool = False,
    project: bool = False,
    cached_file_stats: dict[str, Any] | None = None,
    include_profiles_text: bool = False,
    query_emb: list[float] | None = None,
):
    # P0-1: Always gather named results for structural safety
    async def _noop(val):
        return val

    # P-02/M-07: Accept pre-computed embedding to avoid a redundant embed_query() call.
    # The caller (full_rag/stream_rag) already has query_emb from the semantic cache check.
    if query_emb is None:
        query_emb = await embedding_service.embed_query(query)

    retrieved, top_folder_profiles, file_stats, legacy_profiles_text = await asyncio.gather(
        hybrid_retrieve(
            query=query,
            db=db,
            embedding_service=embedding_service,
            lancedb_client=lancedb_client,
            k=k,
            near_misses=near_misses,
            use_reranker=not (project or inventory),
            query_emb=query_emb,  # P-02: pass pre-computed embedding
        ),
        _get_top_relevant_profiles(lancedb_client, db, query_emb, k=2),
        _noop(cached_file_stats) if inventory else _noop(None),
        db.get_folder_profiles_text() if include_profiles_text else _noop(""),
    )

    # Merge top profiles with legacy text if available
    combined_profiles = top_folder_profiles
    if not combined_profiles and legacy_profiles_text:
        combined_profiles = legacy_profiles_text

    return retrieved, file_stats, combined_profiles


async def _get_top_relevant_profiles(lancedb_client, db, query_emb, k=2) -> str:
    """Fetch the full text for the most semantically relevant folder profiles."""
    try:
        # Search summaries for folders specifically
        where = {"is_folder_profile": "true"}
        raw = await lancedb_client.search_summaries(query_emb, k=k, where_filter=where)

        tags = []
        metas_list = raw.get("metadatas", raw.get("metas", [[]]))
        if metas_list and metas_list[0]:
            for meta in metas_list[0]:
                tag = meta.get("folder_tag")
                if tag:
                    tags.append(tag)

        if not tags:
            return ""

        # Fetch the actual synthesized profile text from SQLite
        placeholders = ",".join("?" for _ in tags)
        sql = f"SELECT profile_text FROM folder_profiles WHERE folder_tag IN ({placeholders})"  # nosec B608 # noqa: S608
        rows = await db.execute_query(sql, tuple(tags))

        return "\n".join(r[0] for r in rows)
    except Exception as e:
        logger.debug("Failed to retrieve top folder profiles: %s", e)
        return ""


def _filter_retrieved_results(retrieved, file_type, folder_tag):
    filtered = []
    for res in retrieved:
        path = res.get("file_path", "").lower()
        tag = res.get("folder_tag", "")
        if file_type and not path.endswith(file_type.lower()):
            continue
        if folder_tag and tag != folder_tag:
            continue
        filtered.append(res)
    return filtered
