import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_session: Any = None  # onnxruntime.InferenceSession
_tokenizer: Any = None  # tokenizers.Tokenizer
_reranker_lock = threading.Lock()
_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_MAX_RERANKER_INPUT_LEN = (
    2048  # P-08: Increased from 256 to 2048 to better utilize model's 512-token context.
)


def _load_onnx_model():
    """Lazily load the cross-encoder ONNX model and tokenizer."""
    global _session, _tokenizer
    if _session is not None:
        return

    import onnxruntime as ort
    from tokenizers import Tokenizer

    model_path = Path("models") / _MODEL_NAME.replace("/", "_")
    if not model_path.exists():
        model_path = Path(_MODEL_NAME)

    tokenizer_json = model_path / "tokenizer.json"
    if not tokenizer_json.exists():
        tokenizer_json = model_path / "onnx" / "tokenizer.json"

    onnx_file = model_path / "model.onnx"
    if not onnx_file.exists():
        onnx_file = model_path / "onnx" / "model.onnx"

    if not onnx_file.exists():
        raise FileNotFoundError(f"ONNX reranker model not found at {onnx_file}")

    _tokenizer = Tokenizer.from_file(str(tokenizer_json))
    _tokenizer.enable_truncation(max_length=512)
    _tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")  # noqa: S106

    providers = ["CPUExecutionProvider"]
    _session = ort.InferenceSession(str(onnx_file), providers=providers)
    logger.info("ONNX Reranker loaded from %s", model_path)


def _get_model_assets():
    if _session is None:
        with _reranker_lock:
            if _session is None:
                _load_onnx_model()
    return _session, _tokenizer


def preload_reranker() -> None:
    """Pre-load the reranker model."""
    try:
        _get_model_assets()
    except Exception as e:
        logger.debug("Reranker preloading failed (likely missing model file): %s", e)


async def rerank(
    query: str,
    results: list[dict[str, Any]],
    top_k: int = 10,
    text_key: str = "text",
    time_budget_ms: float = 500.0,
) -> list[dict[str, Any]]:
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

    def _run_rerank():
        session, tokenizer = _get_model_assets()

        # Prepare pairs for tokenization
        pairs = [[query, item[text_key][:_MAX_RERANKER_INPUT_LEN]] for item in candidates]
        encoded = tokenizer.encode_batch(pairs)

        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        token_type_ids = np.array([e.type_ids for e in encoded], dtype=np.int64)

        onnx_inputs = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }

        # Run inference
        model_output = session.run(None, onnx_inputs)
        # Cross-encoder output is usually logits of shape [batch, 1]
        scores = model_output[0].flatten()
        return scores.tolist()

    try:
        scores = await loop.run_in_executor(None, _run_rerank)
    except Exception as e:
        logger.error("ONNX Reranking failed: %s", e)
        # Return original results if reranking fails
        return results[:top_k]

    for item, score in zip(candidates, scores, strict=False):
        item["rerank_score"] = round(float(score), 6)

    ranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
    top = ranked[:top_k]

    elapsed_ms = (time.perf_counter() - t0) * 1000
    if elapsed_ms > time_budget_ms:
        logger.warning(
            "Reranker exceeded budget: %.0f ms > %.0f ms budget (%d candidates)",
            elapsed_ms,
            time_budget_ms,
            len(candidates),
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
