import asyncio
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings
from app.utils.model_integrity import load_models_lock, verify_file_sha256

logger = logging.getLogger(__name__)

_session: Any = None  # onnxruntime.InferenceSession
_tokenizer: Any = None  # tokenizers.Tokenizer
_reranker_lock = threading.Lock()
_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_MAX_RERANKER_INPUT_LEN = (
    2048  # P-08: Increased from 256 to 2048 to better utilize model's 512-token context.
)

# Why the reranker is unusable, or None when it is fine. Read by the API layer
# so "reranking is off because the model is not installed" can be stated once,
# where the user configures things, instead of being restated as a warning on
# every answer.
_unavailable_reason: str | None = None

# P-5: ONNX inference gets its own single-slot pool instead of the default
# executor. run_in_executor(None, ...) is shared with embed_texts and every
# LanceDB call, so a burst of reranks - which the agentic fan-out deliberately
# creates via asyncio.gather over sub-queries - consumed threads faster than
# they were released and put head-of-line blocking across the whole I/O layer.
# One worker also bounds peak memory: two concurrent batches of up to 100
# query/chunk pairs would double it, and two ORT sessions each defaulting
# intra_op_num_threads to the physical core count oversubscribe a 4-core target.
_onnx_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pma-rerank")


class RerankerNotInstalledError(RuntimeError):
    """The cross-encoder is absent or unverifiable on this install.

    A capability state, not a per-answer fault: it is true of every query until
    someone installs and pins the model, so it must not be reported as this
    answer having been degraded.
    """


class RerankerFailedError(RuntimeError):
    """The cross-encoder loaded but did not score this request.

    A per-answer fault - the capability exists and this answer did not get it,
    which is exactly what a degraded badge should mean.
    """


def _base_dir() -> Path:
    """Install root, resolving inside a PyInstaller bundle as well.

    ``Path("models")`` was CWD-relative, so a packaged sidecar - whose working
    directory is whatever launched it - resolved somewhere arbitrary, and the
    resulting FileNotFoundError was swallowed into un-reranked results.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent.parent


def _resolve_model_dir(entry: dict[str, Any]) -> Path:
    """Directory holding the pinned reranker files.

    A ``models/`` directory beside the install wins, for a bundled or fully
    offline layout. Otherwise the HuggingFace cache is resolved from the
    lockfile's repo_id/revision - the same mechanism the embedder uses - with
    ``local_files_only=True`` so a query never triggers a download.
    """
    root = _base_dir()
    for candidate in (
        root / "models" / _MODEL_NAME.replace("/", "_"),
        root / "models" / _MODEL_NAME,
    ):
        if candidate.exists():
            return candidate

    repo_id, revision = entry.get("repo_id"), entry.get("revision")
    if not repo_id:
        raise RerankerNotInstalledError(f"No repo_id pinned for {_MODEL_NAME}.")

    from app.utils.model_integrity import configure_hf_env

    configure_hf_env()

    from huggingface_hub import snapshot_download

    try:
        return Path(snapshot_download(repo_id=repo_id, revision=revision, local_files_only=True))
    except Exception as e:
        raise RerankerNotInstalledError(
            f"{_MODEL_NAME} is pinned but not in the local cache. "
            f"Run scripts/pin_models.py to fetch it. ({e})"
        ) from e


def _verify_against_lock(files: dict[str, Path], expected: dict[str, Any]) -> None:
    """Fail closed unless every loaded file matches its pinned digest.

    Symmetric with the embedder (``app/embeddings/service.py``): one of the two
    ONNX models on the inference path having no integrity check at all was the
    actual gap. This loader never fetches anything, so there is no download to
    gate - only the digests to honour.
    """
    for rel_key, path in files.items():
        sha = expected.get(rel_key, {}).get("sha256")
        if not sha:
            if not settings.reranker_allow_unpinned:
                raise RerankerNotInstalledError(f"No pinned sha256 for {rel_key} of {_MODEL_NAME}.")
            continue
        if not verify_file_sha256(path, sha, label=rel_key):
            raise RerankerNotInstalledError(
                f"{rel_key} of {_MODEL_NAME} failed its integrity check."
            )


def _load_onnx_model():
    """Lazily load the cross-encoder ONNX model and tokenizer."""
    global _session, _tokenizer
    if _session is not None:
        return

    import onnxruntime as ort
    from tokenizers import Tokenizer

    entry = load_models_lock(family="reranker").get(_MODEL_NAME)
    if not entry:
        if not settings.reranker_allow_unpinned:
            raise RerankerNotInstalledError(
                f"{_MODEL_NAME} is not pinned in models.lock.json. "
                "Pin it with scripts/pin_models.py, or set reranker_allow_unpinned."
            )
        entry = {}

    model_path = _resolve_model_dir(entry)
    expected = entry.get("files", {})

    # The lockfile names the exact artifacts to load, so the loader cannot drift
    # onto a different build than the one whose digest was pinned.
    onnx_rel = next((k for k in expected if k.endswith(".onnx")), "onnx/model_quantized.onnx")
    onnx_file = model_path / onnx_rel
    tokenizer_json = model_path / "tokenizer.json"

    if not onnx_file.exists() or not tokenizer_json.exists():
        raise RerankerNotInstalledError(f"ONNX reranker model not found under {model_path}")

    _verify_against_lock({onnx_rel: onnx_file, "tokenizer.json": tokenizer_json}, expected)

    tokenizer = Tokenizer.from_file(str(tokenizer_json))
    tokenizer.enable_truncation(max_length=512)
    tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")  # nosec B106 # noqa: S106

    options = ort.SessionOptions()
    # Copied from the embedder's measured configuration - but only this line.
    # enable_mem_pattern is already ORT's default, and inter_op_num_threads=2 is
    # dead under the default ORT_SEQUENTIAL execution mode and is the hardcoding
    # CLAUDE.md 7 forbids. The arena is the setting that carried the
    # measurement: 3848 MB -> 172 MB on batch-longest padding, which is exactly
    # what this tokenizer does.
    options.enable_cpu_mem_arena = False

    providers = ["CPUExecutionProvider"]
    _session = ort.InferenceSession(str(onnx_file), sess_options=options, providers=providers)
    _tokenizer = tokenizer
    logger.info("ONNX Reranker loaded from %s", model_path)


def _get_model_assets():
    global _unavailable_reason
    if _session is None:
        with _reranker_lock:
            if _session is None:
                try:
                    _load_onnx_model()
                except RerankerNotInstalledError as e:
                    _unavailable_reason = str(e)
                    raise
                except Exception as e:
                    _unavailable_reason = str(e)
                    raise RerankerNotInstalledError(str(e)) from e
                _unavailable_reason = None
    return _session, _tokenizer


def reranker_status() -> dict[str, Any]:
    """Install-level capability state, for Settings rather than per answer."""
    return {
        "available": _session is not None,
        "model": _MODEL_NAME,
        "reason": _unavailable_reason,
    }


def preload_reranker() -> None:
    """Pre-load the reranker model."""
    try:
        _get_model_assets()
    except Exception as e:
        logger.info("Reranker unavailable: %s", e)


def _bounded_candidates(
    results: list[dict[str, Any]], top_k: int, text_key: str
) -> list[dict[str, Any]]:
    """Cap the batch by its padded footprint, not only by count.

    Padding is batch-longest, so cost scales with ``len(batch) * longest``: a
    100-item batch holding one long chunk costs as much as 100 long chunks.
    ``top_k * 4`` bounds the count and not that product, which is what actually
    drives peak memory on the interactive query path.
    """
    budget = settings.reranker_max_batch_chars
    chosen: list[dict[str, Any]] = []
    longest = 0
    for item in results[: top_k * 4]:
        length = min(len(item.get(text_key) or ""), _MAX_RERANKER_INPUT_LEN)
        candidate_longest = max(longest, length)
        if chosen and (len(chosen) + 1) * candidate_longest > budget:
            break
        chosen.append(item)
        longest = candidate_longest
    return chosen


async def rerank(
    query: str,
    results: list[dict[str, Any]],
    top_k: int = 10,
    text_key: str = "text",
) -> list[dict[str, Any]]:
    """Cross-encoder re-ranking. Runs to completion; it has no internal deadline.

    **The deadline lives at the caller**, and it is real:
    ``_apply_reranker_if_needed`` (``app/search/retrieval.py``) wraps this in
    ``asyncio.wait_for(..., timeout=5.0)``, and on expiry the answer is returned
    in RRF order and every result is flagged by ``_mark_degraded``. That is the
    guard on the interactive path.

    A ``time_budget_ms=500.0`` parameter used to live here and is **removed**.
    It enforced nothing: elapsed time was measured *after* ``session.run()`` had
    already returned, so all it could do was log a warning about work that was
    finished. No caller ever passed it, and at the shipped default it fired on
    essentially every query - measured 2026-09-01 over
    ``tests/eval/corpus_large``, 750-940 ms against a 500 ms budget on **100%**
    of queries, with the model warm. A warning that always fires is not a
    signal, and a parameter named for a budget it does not keep is worse than
    no parameter.

    It could not be made to enforce in place, either, which is why this is a
    deletion rather than a fix. ``session.run()`` is a blocking call into ONNX
    Runtime on a ``ThreadPoolExecutor`` thread, and a started
    ``concurrent.futures`` worker cannot be cancelled - the caller's own comment
    already records that its ``wait_for`` abandons the *await* while the
    inference runs on to completion. An internal deadline could therefore only
    ever be checked after the fact, which is exactly what the removed code did.
    Splitting the batch to check between sub-batches would give a *partial*
    rerank, and that is worse than useless downstream: ``_apply_relevance_cutoff``
    (``app/search/context_builder.py``) branches on whether ``rerank_score`` is
    present on all results or only some, and the some case is explicitly the
    "mixed scales - not assessable as one ranking" path that drops nothing.

    Cost is bounded *before* the work starts instead, by ``_bounded_candidates``:
    ``top_k * 4`` and ``settings.reranker_max_batch_chars``. Capping the batch is
    what actually controls the bill; timing it afterwards never did.

    Per-call latency is still recorded, at debug level, in the log line below.
    """
    if not results:
        return results
    if top_k <= 0:
        return []

    import time

    t0 = time.perf_counter()

    candidates = _bounded_candidates(results, top_k, text_key)

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
        scores = await loop.run_in_executor(_onnx_executor, _run_rerank)
    except RerankerNotInstalledError:
        # Capability state. Propagated, not swallowed: returning RRF order while
        # reporting mode "full_rag" is what made un-reranked results
        # indistinguishable from reranked ones.
        raise
    except Exception as e:
        logger.error("ONNX Reranking failed: %s", e)
        raise RerankerFailedError(str(e)) from e

    for item, score in zip(candidates, scores, strict=False):
        item["rerank_score"] = round(float(score), 6)

    # Fuse the cross-encoder's ordering with the incoming one rather than letting
    # it replace it. `candidates` arrives in the caller's RRF order, so this is
    # the same reciprocal-rank fusion the retrieval path already runs, over two
    # lists instead of three. See settings.reranker_rrf_fusion_weight for the
    # measurement that motivates it.
    #
    # Done here, before the top_k truncation, so every returned item still
    # carries a rerank_score. Fusing at the caller would let an item with no
    # score reach the answer window, and _apply_relevance_cutoff
    # (app/search/context_builder.py) routes partially-scored lists to its
    # "mixed scales - not assessable as one ranking" branch, which drops nothing.
    ranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
    weight = settings.reranker_rrf_fusion_weight
    if weight <= 0:
        top = ranked[:top_k]
    else:
        k_rrf = settings.reranker_fusion_k
        ce_pos = {id(item): pos for pos, item in enumerate(ranked)}
        fused = sorted(
            enumerate(candidates),
            key=lambda pair: (
                -(
                    weight / (k_rrf + pair[0] + 1)
                    + (1.0 - weight) / (k_rrf + ce_pos[id(pair[1])] + 1)
                )
            ),
        )
        top = [item for _, item in fused][:top_k]

    elapsed_ms = (time.perf_counter() - t0) * 1000

    logger.debug(
        "Reranked %d candidates → top-%d (best=%.4f, worst=%.4f) in %.0f ms",
        len(candidates),
        top_k,
        top[0]["rerank_score"] if top else 0,
        top[-1]["rerank_score"] if top else 0,
        elapsed_ms,
    )
    return top
