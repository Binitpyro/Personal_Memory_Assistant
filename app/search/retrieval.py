import asyncio
import json
import logging
import re
import time
import threading
from collections import OrderedDict
from typing import AsyncGenerator, List, Dict, Any, Optional, Set, Tuple
from app.storage.db import DatabaseManager
from app.embeddings.service import EmbeddingService
from app.vector_store.chroma_client import ChromaClient
from app.search.context_builder import build_context
from app.search.llm_client import LLMClient
from app.search.reranker import rerank
from app.config import settings

logger = logging.getLogger(__name__)

# Phase 3.1: Result Cache (LRU)
# Keys are (query, file_type, folder_tag, index_gen)
# Values are List[Dict[str, Any]]
_retrieval_cache: OrderedDict[Tuple[str, Optional[str], Optional[str], int], List[Dict[str, Any]]] = OrderedDict()
_CACHE_MAX_SIZE = 500  # 5x increase for better hit rate
_cache_lock = threading.Lock()

# Full-RAG response cache (caches LLM answers for repeat queries)
# Keys are (query, file_type, folder_tag, index_gen)
# Values are Dict[str, Any] (full response)
_rag_response_cache: OrderedDict[Tuple[str, Optional[str], Optional[str], int], Dict[str, Any]] = OrderedDict()
_RAG_CACHE_MAX_SIZE = 200
_rag_cache_lock = threading.Lock()

# Index generation counter — incremented on each cache clear (after re-indexing)
# Included in cache keys so stale entries are never hit.
_index_generation: int = 0

def clear_retrieval_cache():
    """Invalidates the retrieval cache. Call this after indexing or clearing DB."""
    global _index_generation
    with _cache_lock:
        _retrieval_cache.clear()
    with _rag_cache_lock:
        _rag_response_cache.clear()
    _index_generation += 1
    logger.info("Retrieval + RAG response caches cleared (generation=%d).", _index_generation)

_INVENTORY_RE = re.compile(
    r'\b(?:how many|count|do i have|files? do i|'
    r'files? i have|my files|all files|all my|total size|'
    r'breakdown|statistics|stats|types? of files?|extensions?|'
    r'storage|disk space|largest folders?|smallest folders?|'
    r'how big|how large|how much space|file count|indexed files?)\b',
    re.IGNORECASE,
)

_LATEST_RE = re.compile(r'\b(?:latest|recent|newest|added lately|last updated|last modified)\b', re.IGNORECASE)
_LARGEST_RE = re.compile(r'\b(?:largest|biggest|huge|oversized|most space|taking up space)\b', re.IGNORECASE)

async def _get_metadata_insights(
    query: str,
    db: DatabaseManager,
    file_stats: Optional[Dict[str, Any]],
    folder_profiles: List[Dict[str, Any]],
    unreal_facts: List[Dict[str, Any]],
) -> Optional[str]:
    """
    Builds a concise factual metadata summary when the query requests inventory, project, or Unreal-related information.
    
    When the query contains indicators for "latest", "largest", inventory totals (e.g., "how many files", "total size"), project overview/summary, or Unreal (e.g., "unreal", "ue5", "uproject"), this function assembles a multi-line, human-readable string that may include:
    - a "Recently modified files" section (top 5 by modification time),
    - a "Largest indexed files" section (top 5 by size in MB),
    - overall indexed file totals and size,
    - Unreal facts or a hint listing detected Unreal project profiles,
    - folder/project profile summaries.
    
    Parameters:
        query (str): The user's query text used to detect requested metadata.
        db (DatabaseManager): Database manager used to run lightweight queries for latest/largest files.
        file_stats (Optional[Dict[str, Any]]): Precomputed file statistics; expected to include at least
            'total_files' and 'total_size_mb' when present.
        folder_profiles (List[Dict[str, Any]]): Indexed folder/project profile records used for project summaries.
        unreal_facts (List[Dict[str, Any]]): Extracted Unreal-related facts to include when available.
    
    Returns:
        Optional[str]: A formatted multi-line metadata summary when relevant indicators are present in the query,
        or `None` if the query does not request metadata insights.
    """
    inventory = bool(_LATEST_RE.search(query) or _LARGEST_RE.search(query) or "how many files" in query.lower())
    project = "project" in query.lower() or "overview" in query.lower() or "summary" in query.lower()
    unreal = "unreal" in query.lower() or "ue5" in query.lower() or "uproject" in query.lower()
    latest = bool(_LATEST_RE.search(query))
    largest = bool(_LARGEST_RE.search(query))

    if not (inventory or project or unreal or latest or largest):
        return None

    lines: List[str] = ["=== Metadata Insights (Factual Source of Truth) ==="]
    
    if latest:
        rows = await db.execute_query("SELECT path, modified_at FROM files ORDER BY modified_at DESC LIMIT 5")
        if rows:
            lines.append("Recently modified files:")
            for r in rows:
                name = Path(r[0]).name
                lines.append(f"- {name} (last changed {r[1]})")
    
    if largest:
        rows = await db.execute_query("SELECT path, size FROM files ORDER BY size DESC LIMIT 5")
        if rows:
            lines.append("Largest indexed files:")
            for r in rows:
                name = Path(r[0]).name
                size_mb = round(r[1] / (1024 * 1024), 2)
                lines.append(f"- {name} ({size_mb} MB)")

    unreal_profiles = [
        profile for profile in folder_profiles
        if "unreal" in str(profile.get("project_type", "")).lower()
    ]

    if inventory and file_stats:
        lines.append(
            f"Total indexed files: {file_stats['total_files']}. Total size: ~{file_stats['total_size_mb']} MB."
        )
        _append_inventory_type_lines(lines, file_stats)

    if unreal and unreal_facts:
        _append_unreal_fact_lines(lines, unreal_facts)
    elif unreal and unreal_profiles:
        _append_unreal_profile_hint(lines, unreal_profiles)

    if project and folder_profiles:
        _append_project_profile_lines(lines, folder_profiles)

    lines.append("=" * 50)
    return "\n".join(lines)

def _append_unreal_fact_lines(lines: List[str], unreal_facts: List[Dict[str, Any]]) -> None:
    """
    Append formatted Unreal project fact lines to the provided list.
    
    Each entry in `unreal_facts` becomes a bulleted summary line containing the project name,
    engine version, and counts for assets, maps, character blueprints, and materials.
    
    Parameters:
        lines (List[str]): Mutable list to which formatted lines will be appended.
        unreal_facts (List[Dict[str, Any]]): List of Unreal project fact dictionaries. Each dictionary should contain:
            - 'project_name' (str): Project name.
            - 'engine_version' (str): Engine/UE version string.
            - 'total_assets' (int): Total number of assets.
            - 'map_count' (int): Number of maps.
            - 'character_blueprints' (int): Number of character blueprints.
            - 'material_count' (int): Number of materials.
    """
    lines.append("Unreal project summary:")
    for uf in unreal_facts:
        lines.append(
            f"  - {uf['project_name']} (UE {uf['engine_version']}): "
            f"{uf['total_assets']} assets, {uf['map_count']} maps, "
            f"{uf['character_blueprints']} char BPs, {uf['material_count']} materials."
        )

def _append_unreal_profile_hint(lines: List[str], unreal_profiles: List[Dict[str, Any]]) -> None:
    """
    Append a brief summary of detected Unreal Engine projects to the provided list of lines.
    
    Each profile is added as a bullet line formatted as "  - {folder_tag} ({file_count} files)".
    
    Parameters:
        lines (List[str]): Mutable list of lines to append summary text to.
        unreal_profiles (List[Dict[str, Any]]): Profiles where each dict must contain
            'folder_tag' (str) and 'file_count' (int).
    """
    lines.append("Unreal Engine projects detected:")
    for up in unreal_profiles:
        lines.append(f"  - {up['folder_tag']} ({up['file_count']} files)")

def _append_project_profile_lines(lines: List[str], folder_profiles: List[Dict[str, Any]]) -> None:
    """
    Append formatted lines describing indexed project/folder profiles to `lines`.
    
    Each entry in `folder_profiles` is rendered as:
    "  - {folder_tag} ({project_type}): {file_count} files, {size_mb} MB"
    where `size_mb` is `total_size_bytes` converted to megabytes and rounded to two decimal places.
    
    Parameters:
        lines (List[str]): Mutable list of strings to which profile lines will be appended.
        folder_profiles (List[Dict[str, Any]]): List of folder/profile dictionaries. Each dictionary must contain
            the keys `folder_tag`, `project_type`, `file_count`, and `total_size_bytes`.
    """
    lines.append("Indexed project/folder profiles:")
    for fp in folder_profiles:
        size_mb = round(fp["total_size_bytes"] / (1024 * 1024), 2)
        lines.append(
            f"  - {fp['folder_tag']} ({fp['project_type']}): {fp['file_count']} files, {size_mb} MB"
        )

def _build_fast_answer(
    query: str,
    file_stats: Optional[Dict[str, Any]],
    folder_profiles: List[Dict[str, Any]],
    unreal_facts: List[Dict[str, Any]],
) -> Optional[str]:
    """
    Return a concise precomputed answer for simple inventory, project, or Unreal queries when available.
    
    Parameters:
    	query (str): The user query text.
    	file_stats (Optional[Dict[str, Any]]): Summary statistics containing keys like `total_files` and `total_size_mb` used for inventory responses.
    	folder_profiles (List[Dict[str, Any]]): Indexed project/folder profiles used to construct project overview summaries.
    	unreal_facts (List[Dict[str, Any]]): Facts about Unreal projects used to build an Unreal-specific summary.
    
    Returns:
    	A short answer string for matching fast-path queries (inventory totals, project summaries, or Unreal summaries), or `None` if no fast-path applies.
    """
    query_lower = query.lower()
    
    # Very specific fast paths
    if "how many files" in query_lower or "total size" in query_lower or "disk space" in query_lower:
        if file_stats:
            return f"You currently have {file_stats['total_files']} indexed files taking up a total of {file_stats['total_size_mb']} MB."

    if "unreal" in query_lower and ("overview" in query_lower or "summary" in query_lower) and unreal_facts:
        lines = ["Here is your Unreal project summary:"]
        _append_unreal_fact_lines(lines, unreal_facts)
        return "\n".join(lines)

    if "project" in query_lower and ("overview" in query_lower or "summary" in query_lower) and folder_profiles:
        lines = ["Here is a summary of your indexed projects:"]
        _append_project_profile_lines(lines, folder_profiles)
        return "\n".join(lines)

    return None

_FTS5_OPERATOR_RE = re.compile(r'["*^]|\bAND\b|\bOR\b|\bNOT\b|\bNEAR\b', re.IGNORECASE)

def _sanitize_fts_query(query: str) -> str:
    cleaned = _FTS5_OPERATOR_RE.sub(' ', query)
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    if not tokens:
        return '"' + query.replace('"', '') + '"'
    return ' '.join(f'"{t}"' for t in tokens)

async def _fts_search(
    db: DatabaseManager, query: str, k: int,
    folder_tag: Optional[str] = None,
    file_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """FTS5 keyword search with optional metadata push-down filters."""
    try:
        fts_match = _sanitize_fts_query(query)
        params = [fts_match]
        where_clauses = ["cf.chunks_text MATCH ?"]
        
        if folder_tag:
            where_clauses.append("f.folder_tag = ?")
            params.append(folder_tag)
        if file_type:
            where_clauses.append("f.type = ?")
            params.append(file_type.lower())
            
        params.append(2 * k)
        fts_sql = (
            "SELECT cf.rowid, cf.chunks_text FROM chunk_fts cf "
            "JOIN chunks c ON c.id = cf.rowid "
            "JOIN files f ON f.id = c.file_id "
            f"WHERE {' AND '.join(where_clauses)} "
            "ORDER BY rank LIMIT ?"
        )
        rows = await db.execute_query(fts_sql, tuple(params))
        return [{"id": str(row[0]), "text": row[1]} for row in rows]
    except Exception as e:
        logger.warning("FTS5 Search failed: %s", e)
        return []

async def _semantic_search_with_emb(
    chroma_client: ChromaClient,
    query_emb: List[float],
    k: int,
    where_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    raw = await chroma_client.semantic_search(query_emb, k=2 * k, where_filter=where_filter)
    results: List[Dict[str, Any]] = []
    # Support both 'metadatas' and 'metas' for resilience across Chroma versions
    ids_list = raw.get("ids", [[]])
    if ids_list and ids_list[0]:
        ids = ids_list[0]
        distances_list = raw.get("distances", [[]])
        dists = distances_list[0] if distances_list else []
        for i, doc_id in enumerate(ids):
            results.append({
                "id": str(doc_id),
                "score": dists[i] if i < len(dists) else 0.0,
            })
    return results

def _compute_rrf_scores(
    fts_results: List[Dict[str, Any]],
    semantic_results: List[Dict[str, Any]],
    k: int,
) -> List[tuple]:
    scores: Dict[str, float] = {}
    k_rrf = settings.rrf_k
    fts_w = settings.rrf_fts_weight
    sem_w = settings.rrf_semantic_weight
    for rank, res in enumerate(fts_results):
        scores[res["id"]] = fts_w * (1.0 / (k_rrf + rank + 1))
    for rank, res in enumerate(semantic_results):
        chunk_id = res["id"]
        scores[chunk_id] = scores.get(chunk_id, 0.0) + sem_w * (1.0 / (k_rrf + rank + 1))
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]

async def _summary_search_with_emb(
    chroma_client: ChromaClient,
    query_emb: List[float],
    k: int,
) -> Set[str]:
    try:
        raw = await chroma_client.search_summaries(query_emb, k=k)
        paths: Set[str] = set()
        metas_list = raw.get("metadatas", raw.get("metas", [[]]))
        if metas_list and metas_list[0]:
            for meta in metas_list[0]:
                if meta:
                    fp = meta.get("file_path")
                    if fp:
                        paths.add(fp)
        return paths
    except Exception as e:
        logger.debug("Summary search unavailable: %s", e)
        return set()

def _build_candidate_results(
    chunk_ids_ordered: List[int],
    row_map: Dict[int, Any],
    score_map: Dict[int, float],
    relevant_doc_paths: Set[str],
) -> List[Dict[str, Any]]:
    """Deduplicate and build candidate result dicts from ordered chunk IDs."""
    results: List[Dict[str, Any]] = []
    seen_texts: Set[str] = set()
    for cid in chunk_ids_ordered:
        row = row_map.get(cid)
        if not row:
            continue
        text = row[1]
        if len(text) < 50:
            continue
        # Use a rolling hash or snippet for more robust deduplication
        text_prefix = text[:100].strip()
        if text_prefix in seen_texts:
            continue
        seen_texts.add(text_prefix)
        file_path = row[2]
        rrf_score = score_map[cid] * settings.rrf_score_scale
        if file_path in relevant_doc_paths:
            rrf_score *= settings.summary_boost_factor
        results.append({
            "chunk_id": cid,
            "text": text,
            "file_path": file_path,
            "folder_tag": row[3],
            "score": round(rrf_score, 4),
        })
    return results

async def hybrid_retrieve(
    query: str, 
    db: DatabaseManager, 
    embedding_service: EmbeddingService, 
    chroma_client: ChromaClient,
    k: int = settings.retrieval_top_k,
    use_reranker: bool = True,
    file_type: Optional[str] = None,
    folder_tag: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Combines FTS5 keyword + Chroma semantic + document-summary search using RRF,
    then reranks the candidates with a cross-encoder for maximum precision.

    Performance optimisations:
    - LRU cache (500 entries) for repeat queries.
    - Adaptive recall_k: short queries use smaller recall window.
    - Confidence-based reranker bypass: if RRF top-1 score is well above
      the pack, skip the expensive cross-encoder pass.
    - All async I/O (FTS, embedding, semantic, summary) runs concurrently.
    """

    # Phase 3.1: Cache Lookup
    cache_key = (query.strip().lower(), file_type, folder_tag, _index_generation)
    with _cache_lock:
        if cache_key in _retrieval_cache:
            _retrieval_cache.move_to_end(cache_key)
            logger.info("Retrieval cache hit for query: '%s' (filters: %s, %s)", query, file_type, folder_tag)
            return _retrieval_cache[cache_key]

    # Adaptive recall_k: short/simple queries need fewer candidates
    query_words = len(query.split())
    if query_words <= 3:
        recall_k = max(20, k * 2)
    elif query_words <= 8:
        recall_k = max(35, k * 2)
    else:
        recall_k = max(50, k * 2)

    # Phase 3.1: Build Chroma where-filter for pushed-down metadata filtering
    chroma_where: Dict[str, Any] = {}
    if folder_tag:
        chroma_where["folder_tag"] = folder_tag
    if file_type:
        chroma_where["file_type"] = file_type.lower()

    # Launch FTS & embedding concurrently
    fts_task = asyncio.create_task(_fts_search(db, query, recall_k, folder_tag=folder_tag, file_type=file_type))
    emb_task = asyncio.create_task(embedding_service.embed_query(query))
    query_emb = await emb_task
    
    # Launch semantic & summary search concurrently
    semantic_task = asyncio.create_task(
        _semantic_search_with_emb(chroma_client, query_emb, recall_k, where_filter=chroma_where or None)
    )
    summary_task = asyncio.create_task(_summary_search_with_emb(chroma_client, query_emb, k))
    
    fts_results, semantic_results, relevant_doc_paths = await asyncio.gather(
        fts_task, semantic_task, summary_task
    )

    sorted_ids = _compute_rrf_scores(fts_results, semantic_results, recall_k)
    if not sorted_ids:
        return []

    chunk_ids_ordered = [int(cid) for cid, _ in sorted_ids]
    score_map = {int(cid): sc for cid, sc in sorted_ids}

    placeholders = ",".join("?" for _ in chunk_ids_ordered)
    query_sql = (
        f"SELECT c.id, zlib_decompress(c.text_preview) as text_preview, f.path, f.folder_tag "
        f"FROM chunks c JOIN files f ON c.file_id = f.id "
        f"WHERE c.id IN ({placeholders})"
    )
    rows = await db.execute_query(query_sql, tuple(chunk_ids_ordered))
    row_map: Dict[int, Any] = {}
    for row in rows:
        row_map[row[0]] = row

    results = _build_candidate_results(chunk_ids_ordered, row_map, score_map, relevant_doc_paths)
    results = await _apply_reranker_if_needed(results, query, use_reranker, k)

    final_results = results[:k]
    
    # Update Cache
    with _cache_lock:
        if len(_retrieval_cache) >= _CACHE_MAX_SIZE:
            _retrieval_cache.popitem(last=False)
        _retrieval_cache[cache_key] = final_results
        
    return final_results

async def _apply_reranker_if_needed(
    results: List[Dict[str, Any]], 
    query: str, 
    use_reranker: bool, 
    k: int
) -> List[Dict[str, Any]]:
    if not results or not use_reranker:
        return results

    skip_reranker = False
    if len(results) >= 2:
        top_score = results[0]["score"]
        second_score = results[1]["score"]
        if second_score > 0 and (top_score / second_score) >= 2.0:
            skip_reranker = True
            logger.debug(
                "Reranker bypassed: top RRF score %.2f is %.1fx the second (%.2f)",
                top_score, top_score / second_score, second_score,
            )
            
    if not skip_reranker:
        try:
            results = await asyncio.wait_for(
                rerank(query, results, top_k=k, text_key="text"),
                timeout=0.8,
            )
        except asyncio.TimeoutError:
            logger.warning("Reranker timed out (>800ms) — falling back to RRF order.")
            
    return results

async def full_rag(
    query: str,
    db: DatabaseManager,
    embedding_service: EmbeddingService,
    chroma_client: ChromaClient,
    llm_client: LLMClient,
    k: int = settings.retrieval_top_k,
    file_type: Optional[str] = None,
    folder_tag: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Constructs a retrieval-augmented generation (RAG) answer for a user query by combining metadata fast-paths, hybrid retrieval, and LLM generation.
    
    Performs these observable behaviours:
    - Checks an in-process LRU cache and returns a cached full RAG response when available.
    - Loads query metadata (folder profiles, file stats, Unreal facts) and may return a lightweight "fast_path" answer without invoking retrieval or the LLM when the query matches inventory/project/unreal patterns and there is no conversation history.
    - Otherwise runs hybrid retrieval to gather sources, builds a context, and calls the LLM to produce a final answer ("full_rag" mode).
    - If no relevant documents or metadata are found, returns a short no-results answer.
    - Measures and returns timing for metadata, retrieval, and LLM stages.
    - Stores successful full RAG responses back into the LRU cache keyed by (query, file_type, folder_tag, history, index_generation); responses containing the substring "error" or "i'm sorry" (case-insensitive) are not cached.
    
    Parameters:
        query (str): The user's natural-language question.
        db (DatabaseManager): Database manager used to load metadata and chunk/file previews.
        embedding_service (EmbeddingService): Service used to embed the query for semantic retrieval.
        chroma_client (ChromaClient): Vector store client used for semantic and summary searches.
        llm_client (LLMClient): LLM client used to generate the final answer (or stream).
        k (int): Number of top retrieval candidates to request (retrieval depth).
        file_type (Optional[str]): Optional file extension or type to restrict results (e.g., ".md").
        folder_tag (Optional[str]): Optional folder/project tag to restrict results.
        history (Optional[List[Dict[str, str]]]): Optional conversational history as a list of {"role", "content"} dicts; included in cache key when present.
    
    Returns:
        dict: A result object with these keys:
            - answer (str): The generated or fast-path textual answer.
            - sources (List[dict]): Retrieved source items used for context (each item includes at least file_path, folder_tag, and text).
            - retrieved_count (int): Number of returned sources.
            - latency_ms (float): Total end-to-end latency in milliseconds.
            - mode (str): Either "fast_path" or "full_rag" indicating which path produced the answer.
            - timing (dict): Timing breakdown (keys include retrieval_ms, llm_ms, total_ms; metadata timing may be reflected in total_ms for fast_path).
    """
    t_start = time.perf_counter()

    # Phase 1.1: Full-RAG response cache — return cached LLM answer instantly
    # Include history in cache key if present
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

    inventory = bool(_LATEST_RE.search(query) or _LARGEST_RE.search(query) or "how many files" in query.lower())
    project = "project" in query.lower() or "overview" in query.lower() or "summary" in query.lower()
    unreal = "unreal" in query.lower() or "ue5" in query.lower() or "uproject" in query.lower()

    folder_profiles, file_stats, unreal_facts = await _load_query_metadata(
        db, inventory=inventory, project=project, unreal=unreal,
    )

    fast_answer = _build_fast_answer(query, file_stats, folder_profiles, unreal_facts)
    if fast_answer and not history: # Skip fast-path if there's history to allow follow-ups
        source_rows = [{"file_path": p.get("folder_path", ""), "folder_tag": p.get("folder_tag", ""), "text": p.get("profile_text", "")} for p in folder_profiles]
        total_ms = round((time.perf_counter() - t_start) * 1000, 1)
        return {
            "answer": fast_answer,
            "sources": source_rows,
            "retrieved_count": len(source_rows),
            "latency_ms": total_ms,
            "mode": "fast_path",
            "timing": {"metadata_ms": total_ms, "retrieval_ms": 0, "llm_ms": 0},
        }

    include_profiles_text = project or inventory or unreal
    from app.utils.metrics import Timer
    
    t_ret = time.perf_counter()
    with Timer("retrieval"):
        retrieved, file_stats, folder_profiles_text = await _gather_full_rag_inputs(
            query=query, db=db, embedding_service=embedding_service, chroma_client=chroma_client,
            k=k, inventory=inventory, project=project, unreal=unreal, cached_file_stats=file_stats,
            include_profiles_text=include_profiles_text,
        )
    retrieval_ms = round((time.perf_counter() - t_ret) * 1000, 1)

    if file_type or folder_tag:
        retrieved = _filter_retrieved_results(retrieved, file_type=file_type, folder_tag=folder_tag)
    
    if not retrieved and not file_stats and not folder_profiles_text:
        return {"answer": "I couldn't find any relevant documents.", "sources": [], "retrieved_count": 0, "latency_ms": round((time.perf_counter() - t_start) * 1000, 1)}
        
    context = build_context(retrieved, file_stats=file_stats, folder_profiles_text=folder_profiles_text)
    
    t_llm = time.perf_counter()
    with Timer("llm_generation"):
        try:
            answer = await llm_client.generate_answer(query, context, history=history)
        except Exception as e:
            logger.error("LLM Generation failed: %s", e)
            answer = "I'm sorry, but I encountered an error while generating the answer. This could be due to a timeout or connection issue with the AI service. Please try again."
    llm_ms = round((time.perf_counter() - t_llm) * 1000, 1)
    
    total_ms = round((time.perf_counter() - t_start) * 1000, 1)
    
    result = {
        "answer": answer,
        "sources": retrieved,
        "retrieved_count": len(retrieved),
        "latency_ms": total_ms,
        "mode": "full_rag",
        "timing": {"retrieval_ms": retrieval_ms, "llm_ms": llm_ms, "total_ms": total_ms},
    }

    # Phase 1.1: Cache the full RAG response for repeat queries (only if successful)
    if "error" not in result["answer"].lower() and "i'm sorry" not in result["answer"].lower():
        with _rag_cache_lock:
            if len(_rag_response_cache) >= _RAG_CACHE_MAX_SIZE:
                _rag_response_cache.popitem(last=False)
            _rag_response_cache[rag_cache_key] = result

    return result

async def full_rag_stream(
    query: str,
    db: DatabaseManager,
    embedding_service: EmbeddingService,
    chroma_client: ChromaClient,
    llm_client: LLMClient,
    k: int = settings.retrieval_top_k,
    file_type: Optional[str] = None,
    folder_tag: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> AsyncGenerator[str, None]:
    """
    Stream a retrieval-augmented generation (RAG) response for `query` as a sequence of JSON messages.
    
    Yields JSON-encoded strings (each terminated with a newline) that represent either:
    - a metadata message with retrieval sources and timing (type="metadata"), emitted once before content; or
    - content messages with partial text fragments as the LLM streams its answer (type="content").
    
    Behavior notes:
    - If a lightweight fast answer is available and no `history` is provided, yields a metadata message followed by a single content message containing the full fast answer and then returns.
    - If no relevant documents or metadata are found, yields a single content message indicating no relevant documents.
    - Attempts to save the completed query and full streamed answer to `db.save_query(...)` after streaming; failures are logged but do not affect yielded output.
    
    Returns:
        JSON message strings suitable for client consumption: metadata and content messages, each followed by a newline.
    """
    t_start = time.perf_counter()
    inventory = bool(_LATEST_RE.search(query) or _LARGEST_RE.search(query) or "how many files" in query.lower())
    project = "project" in query.lower() or "overview" in query.lower() or "summary" in query.lower()
    unreal = "unreal" in query.lower() or "ue5" in query.lower() or "uproject" in query.lower()

    folder_profiles, file_stats, unreal_facts = await _load_query_metadata(
        db, inventory=inventory, project=project, unreal=unreal,
    )

    fast_answer = _build_fast_answer(query, file_stats, folder_profiles, unreal_facts)
    if fast_answer and not history:
        # For fast path, just yield the whole thing as one chunk since it's instant
        metadata = {
            "type": "metadata",
            "sources": [{"file_path": p.get("folder_path", ""), "folder_tag": p.get("folder_tag", ""), "text": p.get("profile_text", "")} for p in folder_profiles],
            "latency_ms": round((time.perf_counter() - t_start) * 1000, 1)
        }
        yield json.dumps(metadata) + "\n"
        yield json.dumps({"type": "content", "text": fast_answer}) + "\n"
        return

    include_profiles_text = project or inventory or unreal
    from app.utils.metrics import Timer
    
    with Timer("retrieval"):
        retrieved, file_stats, folder_profiles_text = await _gather_full_rag_inputs(
            query=query, db=db, embedding_service=embedding_service, chroma_client=chroma_client,
            k=k, inventory=inventory, project=project, unreal=unreal, cached_file_stats=file_stats,
            include_profiles_text=include_profiles_text,
        )

    if file_type or folder_tag:
        retrieved = _filter_retrieved_results(retrieved, file_type=file_type, folder_tag=folder_tag)
    
    if not retrieved and not file_stats and not folder_profiles_text:
        yield json.dumps({"type": "content", "text": "I couldn't find any relevant documents."}) + "\n"
        return
        
    context = build_context(retrieved, file_stats=file_stats, folder_profiles_text=folder_profiles_text)
    
    # Yield sources immediately before starting LLM
    metadata = {
        "type": "metadata",
        "sources": retrieved,
        "latency_retrieval_ms": round((time.perf_counter() - t_start) * 1000, 1)
    }
    yield json.dumps(metadata) + "\n"

    full_answer = ""
    with Timer("llm_generation"):
        async for chunk in llm_client.stream_answer(query, context, history=history):
            full_answer += chunk
            yield json.dumps({"type": "content", "text": chunk}) + "\n"
    
    # Optional: save history at the end
    try:
        total_ms = round((time.perf_counter() - t_start) * 1000, 1)
        await db.save_query(query, full_answer, len(retrieved), total_ms)
    except Exception as e:
        logger.warning("Failed to save streamed query history: %s", e, exc_info=True)

async def _load_query_metadata(db, inventory, project, unreal):
    p_coro = db.get_all_folder_profiles() if (project or inventory or unreal) else asyncio.sleep(0, [])
    s_coro = db.get_file_stats_summary() if inventory else asyncio.sleep(0, None)
    u_coro = db.get_all_unreal_project_facts() if (unreal or project) else asyncio.sleep(0, [])
    return await asyncio.gather(p_coro, s_coro, u_coro)

async def _gather_full_rag_inputs(query, db, embedding_service, chroma_client, k, inventory, project, unreal, cached_file_stats, include_profiles_text):
    coros = [hybrid_retrieve(query=query, db=db, embedding_service=embedding_service, chroma_client=chroma_client, k=k, use_reranker=not (project or inventory or unreal))]
    if inventory: coros.append(asyncio.sleep(0, cached_file_stats))
    if include_profiles_text: coros.append(db.get_folder_profiles_text())
    results = await asyncio.gather(*coros)
    retrieved = results[0]
    file_stats = results[1] if inventory else None
    folder_profiles_text = ""
    if include_profiles_text and inventory:
        folder_profiles_text = results[2]
    elif include_profiles_text:
        folder_profiles_text = results[1]
    return retrieved, file_stats, folder_profiles_text

def _filter_retrieved_results(retrieved, file_type, folder_tag):
    filtered = []
    for res in retrieved:
        path = res.get("file_path", "").lower()
        tag = res.get("folder_tag", "")
        if file_type and not path.endswith(file_type.lower()): continue
        if folder_tag and tag != folder_tag: continue
        filtered.append(res)
    return filtered
