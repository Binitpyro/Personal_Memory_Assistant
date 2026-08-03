import asyncio
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingUnavailableError(RuntimeError):
    """Raised when embedding model failed integrity check or loading failed."""

    pass


def _get_models_lock_data() -> dict[str, Any]:
    import sys

    lock_file = None
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / "models.lock.json"
        if candidate.exists():
            lock_file = candidate
    if not lock_file:
        lock_file = Path(__file__).parent.parent.parent / "models.lock.json"

    if lock_file.exists():
        try:
            with open(lock_file, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error("Failed to parse models.lock.json at %s: %s", lock_file, e)
            if not settings.embedding_allow_unpinned:
                raise ValueError(f"models.lock.json invalid or missing and unpinned loading disallowed: {e}") from e
            return {}

        models = data.get("models", {})
        if not models and not settings.embedding_allow_unpinned:
            raise ValueError(f"models.lock.json at {lock_file} contains no models.")
        return models
    else:
        if not settings.embedding_allow_unpinned:
            raise ValueError(f"models.lock.json missing at {lock_file} and embedding_allow_unpinned=False.")

    return {}


class EmbeddingService:
    def __init__(self, model_name: str = ""):
        self.model_name = model_name or settings.embedding_model
        self._session: Any = None  # onnxruntime.InferenceSession
        self._tokenizer: Any = None  # tokenizers.Tokenizer
        self._loading = False
        self._load_error: str | None = None
        self._load_lock = threading.Lock()
        self._ready = threading.Event()
        self.optimal_batch_size = settings.embedding_batch_size
        self._embedding_dim = 384  # Default, overwritten during load
        self.model_signature: str | None = None

        # LRU cache for query embeddings to avoid redundant computation
        self._query_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._max_cache_size = 2000

    def _verify_onnx_checksum(self, onnx_file: Path, expected_sha256: str) -> bool:
        import hashlib
        import hmac

        if not onnx_file.exists() or onnx_file.stat().st_size == 0:
            logger.error("ONNX model missing or empty: %s", onnx_file)
            return False

        hasher = hashlib.sha256()
        try:
            with open(onnx_file, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
            if not hmac.compare_digest(digest, expected_sha256):
                logger.error(
                    "ONNX integrity FAILED for %s: expected %s..., got %s...",
                    onnx_file.name,
                    expected_sha256[:16],
                    digest[:16],
                )
                return False
            logger.info("ONNX integrity verified: %s (%s...)", onnx_file.name, digest[:16])
            return True
        except Exception as e:
            logger.error("Failed to calculate SHA256 for %s: %s", onnx_file, e)
            return False

    def _load_onnx_model(
        self,
        model_path: Path,
        expected_files: dict[str, Any],
        repo_id: str = "",
        revision: str | None = None,
    ):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        # Load tokenizer
        tokenizer_json = model_path / "tokenizer.json"
        if not tokenizer_json.exists():
            tokenizer_json = model_path / "onnx" / "tokenizer.json"

        if not tokenizer_json.exists():
            raise FileNotFoundError(f"tokenizer.json missing at {model_path}")

        if expected_files:
            tok_key = next((k for k in expected_files if k == "tokenizer.json" or k.endswith("/tokenizer.json")), None)
            if tok_key:
                expected_tok_sha = expected_files[tok_key].get("sha256")
                if not expected_tok_sha:
                    raise ValueError("tokenizer.json entry in models.lock.json missing sha256 digest.")
                if not self._verify_onnx_checksum(tokenizer_json, expected_tok_sha):
                    raise ValueError(f"tokenizer.json at {tokenizer_json} failed integrity check.")

        self._tokenizer = Tokenizer.from_file(str(tokenizer_json))
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")  # nosec B106 # noqa: S106

        # Candidate relative paths in priority order (quantized first to conserve memory)
        candidate_paths = [
            "onnx/model_quantized.onnx",
            "onnx/model.onnx",
            "model_quantized.onnx",
            "model.onnx",
        ]

        onnx_file = None
        if expected_files:
            for rel in candidate_paths:
                if rel in expected_files:
                    cand = model_path / rel
                    if cand.exists():
                        onnx_file = cand
                        break

        if not onnx_file:
            for rel in candidate_paths:
                cand = model_path / rel
                if cand.exists():
                    onnx_file = cand
                    break

        if not onnx_file:
            raise FileNotFoundError(f"ONNX model file not found at {model_path}")

        # Check integrity against lockfile (Fail Closed)
        if expected_files:
            try:
                rel_key = str(onnx_file.relative_to(model_path)).replace("\\", "/")
            except Exception:
                rel_key = onnx_file.name

            if rel_key not in expected_files:
                if not settings.embedding_allow_unpinned:
                    raise ValueError(
                        f"Resolved ONNX model file '{rel_key}' is not pinned in models.lock.json expected_files: {list(expected_files.keys())}"
                    )
            else:
                expected_sha = expected_files[rel_key].get("sha256")
                if not expected_sha:
                    raise ValueError(
                        f"ONNX model file '{rel_key}' is pinned in models.lock.json but missing sha256 digest."
                    )
                if not self._verify_onnx_checksum(onnx_file, expected_sha):
                    raise ValueError(f"ONNX model at {onnx_file} failed integrity check.")
        else:
            try:
                rel_key = str(onnx_file.relative_to(model_path)).replace("\\", "/")
            except Exception:
                rel_key = onnx_file.name

        # Use CPU execution provider
        providers = ["CPUExecutionProvider"]

        options = ort.SessionOptions()
        # P0-4: leave intra_op_num_threads at ORT's default (0), which
        # resolves to physical core count *with* thread affinitization.
        # min(4, cpu_count-1) discarded that affinity and hard-capped
        # throughput on anything with more than 5 cores.
        options.inter_op_num_threads = 2
        # P0-4: measured on a variable-length synthetic corpus (mixed
        # 15-400 word texts, batch_size=64) that deliberately stresses
        # BatchLongest padding - the arena never shrinks, so wide shape
        # variance compounds its growth: enable_cpu_mem_arena=True peaked
        # at 3848 MB vs 172 MB with it off (22x), for a 9% throughput cost
        # (22.12 -> 20.06 texts/sec) - comfortably worth it.
        options.enable_cpu_mem_arena = False
        # enable_mem_pattern=True stays on: with the arena already off,
        # turning this off too gave no further memory benefit (173 MB,
        # same as above within noise) but roughly halved throughput
        # (20.06 -> 10.19 texts/sec) - not worth it on its own.
        options.enable_mem_pattern = True

        self._session = ort.InferenceSession(
            str(onnx_file), sess_options=options, providers=providers
        )
        logger.info("ONNX InferenceSession initialized for %s (Bounded Memory)", self.model_name)

        # Prewarm the model
        try:
            dummy_input_ids = np.zeros((1, 8), dtype=np.int64)
            dummy_attention_mask = np.ones((1, 8), dtype=np.int64)
            dummy_token_type_ids = np.zeros((1, 8), dtype=np.int64)

            input_names = [i.name for i in self._session.get_inputs()]
            dummy_inputs = {}
            if "input_ids" in input_names:
                dummy_inputs["input_ids"] = dummy_input_ids
            if "attention_mask" in input_names:
                dummy_inputs["attention_mask"] = dummy_attention_mask
            if "token_type_ids" in input_names:
                dummy_inputs["token_type_ids"] = dummy_token_type_ids

            self._session.run(None, dummy_inputs)

            dummy_emb = self._mean_pooling(
                self._session.run(None, dummy_inputs), dummy_attention_mask
            )
            self._embedding_dim = dummy_emb.shape[1]
            logger.info("ONNX Runtime prewarmed successfully. Extracted dim: %d", self._embedding_dim)
        except Exception as prewarm_err:
            logger.warning("Failed to prewarm ONNX session: %s", prewarm_err)

        rev_str = revision or "local"
        r_id = repo_id or self.model_name
        self.model_signature = f"{r_id}@{rev_str}:{rel_key}"

    def load_model(self) -> None:
        """Loads the embedding model using ONNX Runtime (blocking)."""
        if self._session:
            self._ready.set()
            return

        self._load_error = None
        try:
            candidate = Path(self.model_name).expanduser()
            is_local_dir = (
                candidate.is_absolute()
                or self.model_name.startswith((".", "~"))
                or candidate.is_dir()
            )

            expected_files: dict[str, Any] = {}
            repo_id = self.model_name
            revision = None

            if is_local_dir:
                model_path = candidate.resolve()
                if not model_path.is_dir():
                    raise FileNotFoundError(
                        f"PMA_EMBEDDING_MODEL points to a missing directory: {model_path}"
                    )
            else:
                lock_data = _get_models_lock_data()
                entry = lock_data.get(self.model_name)
                if entry is None:
                    if not settings.embedding_allow_unpinned:
                        raise ValueError(
                            f"Model '{self.model_name}' is not in models.lock.json. "
                            f"Run scripts/pin_models.py or set PMA_EMBEDDING_ALLOW_UNPINNED=true."
                        )
                    logger.warning("Loading UNPINNED, UNVERIFIED model '%s'", self.model_name)
                    repo_id = self.model_name
                    revision = None
                else:
                    repo_id = entry.get("repo_id", self.model_name)
                    revision = entry.get("revision")
                    expected_files = entry.get("files", {})

                from huggingface_hub import snapshot_download
                from huggingface_hub.errors import (
                    LocalEntryNotFoundError,
                    RepositoryNotFoundError,
                    RevisionNotFoundError,
                )

                try:
                    model_path_str = snapshot_download(  # nosec B615
                        repo_id=repo_id,
                        revision=revision,
                        local_files_only=True,
                        allow_patterns=["*.json", "*.txt", "*.onnx", "onnx/*"],
                        ignore_patterns=["*.safetensors", "*.bin", "*.h5", "*.msgpack"],
                    )
                    model_path = Path(model_path_str)
                    logger.info("Loaded ONNX model from local HF cache: %s", model_path)
                except LocalEntryNotFoundError:
                    if not settings.embedding_allow_download:
                        raise RuntimeError(
                            "Embedding model not present in local cache and downloads are disabled. "
                            "Set PMA_EMBEDDING_ALLOW_DOWNLOAD=true to enable downloads."
                        ) from None
                    logger.info(
                        "Downloading ONNX model '%s' (repo %s, rev %s)...",
                        self.model_name,
                        repo_id,
                        revision or "latest",
                    )
                    try:
                        model_path_str = snapshot_download(  # nosec B615
                            repo_id=repo_id,
                            revision=revision,
                            local_files_only=False,
                            allow_patterns=["*.json", "*.txt", "*.onnx", "onnx/*"],
                            ignore_patterns=["*.safetensors", "*.bin", "*.h5", "*.msgpack"],
                        )
                    except (RevisionNotFoundError, RepositoryNotFoundError) as e:
                        raise RuntimeError(
                            f"models.lock.json points at {repo_id}@{revision}, which does not resolve on HuggingFace: {e}"
                        ) from e
                    model_path = Path(model_path_str)

            logger.info("Loading ONNX embedding model from: %s", model_path)
            self._load_onnx_model(
                model_path,
                expected_files,
                repo_id=repo_id if not is_local_dir else self.model_name,
                revision=revision if not is_local_dir else None,
            )
            logger.info("Embedding model loaded successfully (ONNX, batch_size=%d).", self.optimal_batch_size)
        except Exception as e:
            self._session = None
            self._load_error = str(e)
            logger.error("Failed to load ONNX embedding model: %s", e)
        finally:
            self._loading = False
            self._ready.set()

    def load_model_background(self) -> None:
        """Starts model loading in a background thread."""
        with self._load_lock:
            if self._session or self._loading:
                return
            self._loading = True
        thread = threading.Thread(target=self.load_model, daemon=True, name="emb-loader")
        thread.start()

    def wait_until_ready(self, timeout: float = 120) -> bool:
        return self._ready.wait(timeout=timeout)

    @property
    def is_ready(self) -> bool:
        """Returns True only if model loading completed successfully and session is active."""
        return self._ready.is_set() and self._session is not None and self._load_error is None

    @property
    def has_failed(self) -> bool:
        """Returns True if model loading completed with an error."""
        return self._ready.is_set() and (self._load_error is not None or self._session is None)

    @property
    def load_error(self) -> str | None:
        """Returns the load error message if loading failed."""
        return self._load_error

    def _mean_pooling(self, model_output, attention_mask):
        token_embeddings = model_output[0]
        input_mask_expanded = np.expand_dims(attention_mask, -1).astype(float)
        return np.sum(token_embeddings * input_mask_expanded, 1) / np.maximum(
            input_mask_expanded.sum(1), 1e-9
        )

    def _normalize(self, v):
        norm = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.maximum(norm, 1e-9)

    async def embed_texts(
        self,
        texts: list[str],
        batch_size: int | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> np.ndarray:
        if not self._session:
            if self._loading:
                await asyncio.get_running_loop().run_in_executor(None, self.wait_until_ready)
            else:
                await asyncio.get_running_loop().run_in_executor(None, self.load_model)

        if self._load_error:
            raise EmbeddingUnavailableError(f"Embedding model unavailable: {self._load_error}")

        if not self._session:
            raise EmbeddingUnavailableError("Embedding model failed to load.")

        # Deduplicate
        unique_texts: list[str] = []
        text_to_idx: dict[str, int] = {}
        original_map: list[int] = []
        for text in texts:
            if text not in text_to_idx:
                text_to_idx[text] = len(unique_texts)
                unique_texts.append(text)
            original_map.append(text_to_idx[text])

        effective_batch_size = batch_size or self.optimal_batch_size
        loop = asyncio.get_running_loop()

        def _run_inference():
            num_batches = (len(unique_texts) + effective_batch_size - 1) // effective_batch_size
            if not unique_texts:
                return np.zeros((0, self._embedding_dim), dtype=np.float32)

            out_array = np.zeros((len(unique_texts), self._embedding_dim), dtype=np.float32)

            for i in range(0, len(unique_texts), effective_batch_size):
                if progress_callback:
                    progress_callback(i // effective_batch_size + 1, num_batches)

                batch = unique_texts[i : i + effective_batch_size]
                encoded = self._tokenizer.encode_batch(batch)

                input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
                attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
                token_type_ids = np.array([e.type_ids for e in encoded], dtype=np.int64)

                onnx_inputs = {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "token_type_ids": token_type_ids,
                }

                model_output = self._session.run(None, onnx_inputs)
                sentence_embeddings = self._mean_pooling(model_output, attention_mask)
                sentence_embeddings = self._normalize(sentence_embeddings)

                end = i + len(batch)
                out_array[i:end] = sentence_embeddings

            return out_array

        unique_embeddings = await loop.run_in_executor(None, _run_inference)
        if len(unique_embeddings) == 0:
            return unique_embeddings  # type: ignore[no-any-return]
        return unique_embeddings[original_map]  # type: ignore[no-any-return]

    def embed_texts_sync(self, texts: list[str], batch_size: int | None = None) -> np.ndarray:
        if not self._session and not self.wait_until_ready(timeout=60):
            if self._load_error:
                raise EmbeddingUnavailableError(f"Embedding model unavailable: {self._load_error}")
            raise EmbeddingUnavailableError("Embedding model not ready.")

        if self._load_error:
            raise EmbeddingUnavailableError(f"Embedding model unavailable: {self._load_error}")

        if not texts:
            return np.zeros((0, self._embedding_dim), dtype=np.float32)

        unique_texts = list(set(texts))
        text_to_idx = {t: i for i, t in enumerate(unique_texts)}
        effective_batch_size = batch_size or self.optimal_batch_size

        out_array = np.zeros((len(unique_texts), self._embedding_dim), dtype=np.float32)
        for i in range(0, len(unique_texts), effective_batch_size):
            batch = unique_texts[i : i + effective_batch_size]
            encoded = self._tokenizer.encode_batch(batch)
            input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
            attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
            token_type_ids = np.array([e.type_ids for e in encoded], dtype=np.int64)

            out = self._session.run(
                None,
                {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "token_type_ids": token_type_ids,
                },
            )
            pooled = self._mean_pooling(out, attention_mask)
            end = i + len(batch)
            out_array[i:end] = self._normalize(pooled)

        if len(out_array) == 0:
            return out_array
        return out_array[[text_to_idx[t] for t in texts]]

    async def embed_query(self, query: str) -> list[float]:
        with self._cache_lock:
            if query in self._query_cache:
                self._query_cache.move_to_end(query)
                return self._query_cache[query]

        embeddings = await self.embed_texts([query])
        raw_emb = embeddings[0]
        result = raw_emb.tolist() if hasattr(raw_emb, "tolist") else list(raw_emb)

        with self._cache_lock:
            if len(self._query_cache) >= self._max_cache_size:
                self._query_cache.popitem(last=False)
            self._query_cache[query] = result
        return result
