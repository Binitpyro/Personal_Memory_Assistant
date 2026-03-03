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
# Keys are (query, file_type, folder_tag)
# Values are List[Dict[str, Any]]
_retrieval_cache: OrderedDict[Tuple[str, Optional[str], Optional[str]], List[Dict[str, Any]]] = OrderedDict()
_CACHE_MAX_SIZE = 500  # 5x increase for better hit rate
_cache_lock = threading.Lock()

def clear_retrieval_cache():
    """Invalidates the retrieval cache. Call this after indexing or clearing DB."""
    with _cache_lock:
        _retrieval_cache.clear()
        logger.info("Retrieval cache cleared.")

_INVENTORY_RE = re.compile(
    r'\b(?:what|which|list|show|how many|count|do i have|files? do i|'
    r'files? i have|my files|all files|all my|total|size|'
    r'breakdown|statistics|stats|types? of files?|extensions?|'
    r'storage|disk|space|largest|smallest|recent|oldest)\b',
    re.IGNORECASE,
)

_PROJECT_RE = re.compile(
    r'\b(?:project|projects|describe|overview|about|summary|folder|'
    r'tell me about|what is|what are|unreal|unity|godot|react|node|'
    r'python|rust|java|c\+\+|go|flutter|workspace|'
    r'codebase|repo|repository|tech stack|framework|language)\b',
    re.IGNORECASE,
)

_UNREAL_RE = re.compile(r'\b(?:unreal|uasset|umap|uproject|ue5|ue4|engine)\b', re.IGNORECASE)

def _is_inventory_query(query: str) -> bool:
    """Heuristic: does the user want file-level stats rather than content?"""
    return bool(_INVENTORY_RE.search(query))

def _is_project_query(query: str) -> bool:
    """Heuristic: does the user want project-level information?"""
    return bool(_PROJECT_RE.search(query))

def _is_unreal_query(query: str) -> bool:
    """Heuristic: does the user explicitly ask about Unreal Engine data?"""
    return bool(_UNREAL_RE.search(query))

def _append_unreal_fact_lines(lines: List[str], unreal_facts: List[Dict[str, Any]]) -> None:
    lines.append("Unreal project summary:")
    for item in unreal_facts[:5]:
        lines.append(
            f"- {item['project_name']} ({item['folder_path']}) on engine {item['engine_version']}: "
            f"{item['total_assets']} assets, {item['map_count']} maps, "
            f"{item['environment_assets']} environment assets, "
            f"{item['character_blueprints']} character blueprints, "
            f"{item['pawn_blueprints']} pawn blueprints, "
            f"{item['skeletal_meshes']} skeletal meshes, "
            f"{item['material_count']} materials, "
            f"{item['niagara_systems']} Niagara systems."
        )
    lines.append(
        "Tip: import richer Unreal metadata JSON via /unreal/import for best "
        "character/environment detail."
    )

def _append_unreal_profile_hint(lines: List[str], unreal_profiles: List[Dict[str, Any]]) -> None:
    detected = ", ".join(profile.get("folder_tag", "Unknown") for profile in unreal_profiles[:4])
    lines.append(
        "Detected Unreal project(s): "
        f"{detected}. Would you like me to run deeper Unreal analysis "
        "for environment, characters, maps, and gameplay assets?"
    )
    lines.append(
        "To enable this, import Unreal metadata JSON with POST /unreal/import "
        "(json_path + optional folder_tag)."
    )

def _append_project_profile_lines(lines: List[str], folder_profiles: List[Dict[str, Any]]) -> None:
    lines.append("Indexed project/folder profiles:")
    for profile in folder_profiles[:8]:
        size_mb = round((profile.get("total_size_bytes", 0) or 0) / (1024 * 1024), 2)
        lines.append(
            f"- {profile['folder_tag']} ({profile['project_type']}): "
            f"{profile['file_count']} files, {size_mb} MB, path: {profile['folder_path']}"
        )

def _append_inventory_type_lines(lines: List[str], file_stats: Dict[str, Any]) -> None:
    top_types = file_stats.get("by_type", [])[:6]
    if not top_types:
        return
    lines.append(
        "Top file types: "
        + ", ".join(f"{item['ext']} ({item['count']})" for item in top_types)
    )

def _build_fast_answer(
    query: str,
    file_stats: Optional[Dict[str, Any]],
    folder_profiles: List[Dict[str, Any]],
    unreal_facts: List[Dict[str, Any]],
) -> Optional[str]:
    """Build a deterministic answer for inventory/project questions."""
    inventory = _is_inventory_query(query)
    project = _is_project_query(query)
    unreal = _is_unreal_query(query)

    if not (inventory or project or unreal):
        return None

    lines: List[str] = []
    unreal_profiles = [
        profile for profile in folder_profiles
        if "unreal" in str(profile.get("project_type", "")).lower()
    ]

    if file_stats:
        lines.append(
            f"You currently have {file_stats['total_files']} indexed files "
            f"(~{file_stats['total_size_mb']} MB)."
        )

    if unreal and unreal_facts:
        _append_unreal_fact_lines(lines, unreal_facts)

    if unreal_profiles and not unreal_facts:
        _append_unreal_profile_hint(lines, unreal_profiles)

    if project and folder_profiles:
        _append_project_profile_lines(lines, folder_profiles)

    if inventory and file_stats:
        _append_inventory_type_lines(lines, file_stats)

    if not lines:
        return None
    return "\n".join(lines)

_FTS5_OPERATOR_RE = re.compile(r'["*^]|\bAND\b|\bOR\b|\bNOT\b|\bNEAR\b', re.IGNORECASE)

def _sanitize_fts_query(query: str) -> str:
    cleaned = _FTS5_OPERATOR_RE.sub(' ', query)
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    if not tokens:
        return '"' + query.replace('"', '') + '"'
    return ' '.join(f'"{t}"' for t in tokens)

async def _fts_search(db: DatabaseManager, query: str, k: int) -> List[Dict[str, Any]]:
    try:
        fts_match = _sanitize_fts_query(query)
        fts_sql = "SELECT rowid, chunks_text FROM chunk_fts WHERE chunk_fts MATCH ? ORDER BY rank LIMIT ?"
        rows = await db.execute_query(fts_sql, (fts_match, 2 * k))
        return [{"id": str(row[0]), "text": row[1]} for row in rows]
    except Exception as e:
        logger.warning("FTS5 Search failed: %s", e)
        return []

async def _semantic_search_with_emb(
    chroma_client: ChromaClient,
    query_emb: List[float],
    k: int,
) -> List[Dict[str, Any]]:
    raw = await chroma_client.semantic_search(query_emb, k=2 * k)
    results: List[Dict[str, Any]] = []
    if raw.get("ids") and raw["ids"][0]:
        ids = raw["ids"][0]
        dists = raw.get("distances", [[]])[0]
        for i, doc_id in enumerate(ids):
            results.append({
                "id": doc_id,
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
        if raw.get("metadatas") and raw["metadatas"][0]:
            for meta in raw["metadatas"][0]:
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
        text_prefix = text[:100]
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
    cache_key = (query.strip().lower(), file_type, folder_tag)
    with _cache_lock:
        if cache_key in _retrieval_cache:
            _retrieval_cache.move_to_end(cache_key)
            logger.info("Retrieval cache hit for query: '%s'", query)
            return _retrieval_cache[cache_key]

    # Adaptive recall_k: short/simple queries need fewer candidates
    query_words = len(query.split())
    if query_words <= 3:
        recall_k = max(20, k * 2)
    elif query_words <= 8:
        recall_k = max(35, k * 2)
    else:
        recall_k = max(50, k * 2)

    # Launch FTS & embedding concurrently
    fts_task = asyncio.create_task(_fts_search(db, query, recall_k))
    emb_task = asyncio.create_task(embedding_service.embed_query(query))
    query_emb = await emb_task
    
    # Launch semantic & summary search concurrently
    semantic_task = asyncio.create_task(_semantic_search_with_emb(chroma_client, query_emb, recall_k))
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
        f"SELECT c.id, c.text_preview, f.path, f.folder_tag "
        f"FROM chunks c JOIN files f ON c.file_id = f.id "
        f"WHERE c.id IN ({placeholders})"
    )
    rows = await db.execute_query(query_sql, tuple(chunk_ids_ordered))
    row_map: Dict[int, Any] = {}
    for row in rows:
        row_map[row[0]] = row

    results = _build_candidate_results(chunk_ids_ordered, row_map, score_map, relevant_doc_paths)

    # Confidence-based reranker bypass:
    # If the RRF top score is 2x+ the runner-up, ranking is already decisive
    # and the expensive cross-encoder can be skipped.
    if results and use_reranker:
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
            results = await rerank(query, results, top_k=k, text_key="text")

    final_results = results[:k]
    
    # Update Cache
    with _cache_lock:
        if len(_retrieval_cache) >= _CACHE_MAX_SIZE:
            _retrieval_cache.popitem(last=False)
        _retrieval_cache[cache_key] = final_results
        
    return final_results

async def full_rag(
    query: str,
    db: DatabaseManager,
    embedding_service: EmbeddingService,
    chroma_client: ChromaClient,
    llm_client: LLMClient,
    k: int = settings.retrieval_top_k,
    file_type: Optional[str] = None,
    folder_tag: Optional[str] = None,
) -> Dict[str, Any]:
    t_start = time.perf_counter()
    inventory = _is_inventory_query(query)
    project = _is_project_query(query)
    unreal = _is_unreal_query(query)

    folder_profiles, file_stats, unreal_facts = await _load_query_metadata(
        db, inventory=inventory, project=project, unreal=unreal,
    )

    fast_answer = _build_fast_answer(query, file_stats, folder_profiles, unreal_facts)
    if fast_answer:
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
        answer = await llm_client.generate_answer(query, context)
    llm_ms = round((time.perf_counter() - t_llm) * 1000, 1)
    
    total_ms = round((time.perf_counter() - t_start) * 1000, 1)
    
    return {
        "answer": answer,
        "sources": retrieved,
        "retrieved_count": len(retrieved),
        "latency_ms": total_ms,
        "mode": "full_rag",
        "timing": {"retrieval_ms": retrieval_ms, "llm_ms": llm_ms, "total_ms": total_ms},
    }

async def full_rag_stream(
    query: str,
    db: DatabaseManager,
    embedding_service: EmbeddingService,
    chroma_client: ChromaClient,
    llm_client: LLMClient,
    k: int = settings.retrieval_top_k,
    file_type: Optional[str] = None,
    folder_tag: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Retrieves context and yields answer chunks + initial metadata."""
    t_start = time.perf_counter()
    inventory = _is_inventory_query(query)
    project = _is_project_query(query)
    unreal = _is_unreal_query(query)

    folder_profiles, file_stats, unreal_facts = await _load_query_metadata(
        db, inventory=inventory, project=project, unreal=unreal,
    )

    fast_answer = _build_fast_answer(query, file_stats, folder_profiles, unreal_facts)
    if fast_answer:
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
        async for chunk in llm_client.stream_answer(query, context):
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
