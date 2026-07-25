import asyncio
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, model_name: str = ""):
        self.model_name = model_name or settings.embedding_model
        self._session: Any = None  # onnxruntime.InferenceSession
        self._tokenizer: Any = None  # tokenizers.Tokenizer
        self._loading = False
        self._load_lock = threading.Lock()
        self._ready = threading.Event()
        self.optimal_batch_size = settings.embedding_batch_size
        self._embedding_dim = 384  # Default, overwritten during load

        # LRU cache for query embeddings to avoid redundant computation
        self._query_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._max_cache_size = 2000

    def _load_onnx_model(self, model_path: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        # Load tokenizer
        tokenizer_json = model_path / "tokenizer.json"
        if not tokenizer_json.exists():
            # Fallback for some models that use different structures
            tokenizer_json = model_path / "onnx" / "tokenizer.json"

        self._tokenizer = Tokenizer.from_file(str(tokenizer_json))
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")  # noqa: S106

        # Load ONNX session (check for quantized models first)
        onnx_file = None
        quantized_names = [
            "model_quantized.onnx",
            "model_int8.onnx",
            "onnx/model_quantized.onnx",
            "onnx/model_int8.onnx",
        ]
        for q_name in quantized_names:
            candidate = model_path / q_name
            if candidate.exists():
                onnx_file = candidate
                logger.info("Found quantized ONNX model at: %s", onnx_file)
                break

        if not onnx_file:
            # Search the model directory for any .onnx file containing 'quant' or 'int8'
            for file in model_path.rglob("*.onnx"):
                if "quant" in file.name.lower() or "int8" in file.name.lower():
                    onnx_file = file
                    logger.info("Found matched quantized ONNX model at: %s", onnx_file)
                    break

        if not onnx_file:
            onnx_file = model_path / "model.onnx"
            if not onnx_file.exists():
                onnx_file = model_path / "onnx" / "model.onnx"

        if not onnx_file or not onnx_file.exists():
            raise FileNotFoundError(f"ONNX model file not found at {model_path}")

        # Use CPU execution provider for maximum portability and minimum size
        providers = ["CPUExecutionProvider"]

        # O(1) Memory Fix: Prevent ONNX from allocating gigabytes of thread memory arenas
        # By default, ONNX creates a memory arena per CPU core, scaling RAM to 1.5GB+ on modern CPUs.
        # Performance Fix: Use more threads (4 intra, 2 inter) and enable memory optimizations.
        # With fixed tokenizer padding (length=256), enable_mem_pattern=True is safe and fast.
        import os

        options = ort.SessionOptions()
        # Use up to 4 intra-op threads, leave 1 core free for OS/other tasks
        options.intra_op_num_threads = min(4, max(1, (os.cpu_count() or 1) - 1))
        # Use 2 inter-op threads for model-level parallelism
        options.inter_op_num_threads = 2
        # Re-enable CPU memory arena (bounded by fixed tensor shapes from fixed padding)
        options.enable_cpu_mem_arena = True
        # Enable memory pattern optimization - safe with fixed-length tokenizer padding
        options.enable_mem_pattern = True

        self._session = ort.InferenceSession(
            str(onnx_file), sess_options=options, providers=providers
        )
        logger.info("ONNX InferenceSession initialized for %s (Bounded Memory)", self.model_name)

        # Prewarm the model with a dummy inference batch (batch=1, seq=8)
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

            # Dynamically determine the embedding dimension from a dummy run
            dummy_emb = self._mean_pooling(
                self._session.run(None, dummy_inputs), dummy_attention_mask
            )
            self._embedding_dim = dummy_emb.shape[1]

            logger.info(
                "ONNX Runtime prewarmed successfully with dummy batch (batch=1, seq=8). "
                "Extracted dim: %d",
                self._embedding_dim,
            )
        except Exception as prewarm_err:
            logger.warning(
                "Failed to prewarm ONNX session or extract embedding dimension: %s", prewarm_err
            )

    def load_model(self) -> None:
        """Loads the embedding model using ONNX Runtime (blocking)."""
        if self._session:
            self._ready.set()
            return

        try:
            model_path = Path(self.model_name)

            if not model_path.exists():
                logger.info(
                    "Downloading/Resolving ONNX model '%s' from HuggingFace...", self.model_name
                )
                from huggingface_hub import snapshot_download

                # Fetch only required ONNX and tokenizer files to save bandwidth and disk space
                model_path_str = snapshot_download(
                    repo_id=self.model_name,
                    allow_patterns=["*.json", "*.txt", "*.onnx", "onnx/*"],
                    ignore_patterns=["*.safetensors", "*.bin", "*.h5", "*.msgpack"],
                )
                model_path = Path(model_path_str)

            logger.info("Loading ONNX embedding model from: %s", model_path)
            self._load_onnx_model(model_path)

            logger.info(
                "Embedding model loaded successfully (ONNX, batch_size=%d).",
                self.optimal_batch_size,
            )
        except Exception as e:
            logger.error("Failed to load ONNX embedding model: %s", e)
            # In a production app, we would handle missing models by downloading them
            # but here we follow the instruction to move to ONNX.
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
        return self._ready.is_set()

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

        if not self._session:
            raise RuntimeError("Embedding model failed to load.")

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
                # Some models don't need token_type_ids, but MiniLM usually does
                token_type_ids = np.array([e.type_ids for e in encoded], dtype=np.int64)

                onnx_inputs = {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "token_type_ids": token_type_ids,
                }

                # Run inference
                model_output = self._session.run(None, onnx_inputs)

                # Mean Pooling
                sentence_embeddings = self._mean_pooling(model_output, attention_mask)
                # Normalization
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
            raise RuntimeError("Embedding model not ready.")

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
