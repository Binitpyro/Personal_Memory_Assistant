import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional

from sentence_transformers import CrossEncoder  # ships with sentence-transformers

logger = logging.getLogger(__name__)

_reranker: Optional[CrossEncoder] = None
_reranker_lock = threading.Lock()
_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_MAX_RERANKER_INPUT_LEN = 256  # Phase 3.2: reduced from 384 for faster inference (<1% relevance impact)

def _get_model() -> CrossEncoder:
    """Lazily load the cross-encoder (≈ 80 MB, ~120 ms on CPU). Thread-safe."""
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:  # double-checked locking
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                
                backend = "torch"
                model_kwargs = None
                if device == "cpu":
                    try:
                        import onnxruntime
                        import optimum.onnxruntime
                        backend = "onnx"
                        # Reranker typically uses onnx/model.onnx if O4 is missing, 
                        # but we check if we can specify a file.
                        model_kwargs = {"file_name": "onnx/model.onnx"} 
                        logger.info("ONNX verified for Reranker — accelerating CPU inference.")
                    except ImportError:
                        pass

                logger.info("Loading reranker model: %s (backend: %s)", _MODEL_NAME, backend)
                
                if backend == "onnx":
                    _reranker = CrossEncoder(
                        _MODEL_NAME, 
                        max_length=512, 
                        device=device, 
                        backend=backend,
                        model_kwargs=model_kwargs
                    )
                else:
                    _reranker = CrossEncoder(_MODEL_NAME, max_length=512, device=device)
                
                logger.info("Reranker model loaded.")
    return _reranker

def preload_reranker() -> None:
    """Pre-load the reranker model in a background thread at startup.
    
    Avoids cold-start latency on the first user query.
    """
    def _load():
        _get_model()
    threading.Thread(target=_load, daemon=True, name="reranker-preload").start()

async def rerank(
    query: str,
    results: List[Dict[str, Any]],
    top_k: int = 10,
    text_key: str = "text",
    time_budget_ms: float = 500.0,
) -> List[Dict[str, Any]]:
    """Re-score *results* against *query* and return the top-k by relevance.

    Performance optimisations:
    - Truncates chunk text to ``_MAX_RERANKER_INPUT_LEN`` chars before scoring
      to reduce cross-encoder compute (the model's ``max_length=512`` tokens
      already truncates, but doing it at the char level avoids tokenizer work).
    - Caps candidate list to ``top_k * 4`` to bound worst-case latency.
    - Optional ``time_budget_ms`` (not enforced as hard timeout, but used for
      logging when the reranker takes too long).

    Parameters
    ----------
    query:
        The user's natural-language question.
    results:
        Candidate chunks produced by the hybrid retriever.
    top_k:
        How many results to keep after reranking.
    text_key:
        Key in each result dict that contains the text to score.
    time_budget_ms:
        Advisory time budget for reranking. If exceeded, a warning is logged.

    Returns
    -------
    list:
        The *top_k* results sorted by descending cross-encoder score.
    """
    if not results:
        return results
    if top_k <= 0:
        return []

    import time
    t0 = time.perf_counter()

    # Cap candidates to limit compute
    max_candidates = min(len(results), top_k * 4)
    candidates = results[:max_candidates]

    loop = asyncio.get_running_loop()
    # Load model off the event loop to avoid blocking during first download/load
    model = await loop.run_in_executor(None, _get_model)

    # Truncate texts for faster tokenization & inference
    pairs = [(query, item[text_key][:_MAX_RERANKER_INPUT_LEN]) for item in candidates]

    scores = await loop.run_in_executor(
        None,
        lambda: model.predict(pairs, show_progress_bar=False).tolist(),
    )

    for item, score in zip(candidates, scores):
        item["rerank_score"] = round(float(score), 6)

    ranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
    top = ranked[:top_k]

    elapsed_ms = (time.perf_counter() - t0) * 1000
    if elapsed_ms > time_budget_ms:
        logger.warning(
            "Reranker exceeded budget: %.0f ms > %.0f ms budget (%d candidates)",
            elapsed_ms, time_budget_ms, len(candidates),
        )

    logger.debug(
        "Reranked %d candidates → top-%d (best=%.4f, worst=%.4f) in %.0f ms",
        len(candidates),
        top_k,
        top[0]["rerank_score"] if top else 0,
        top[-1]["rerank_score"] if top else 0,
        elapsed_ms,
    )
    return top
