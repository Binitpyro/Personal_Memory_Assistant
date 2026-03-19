import logging
import asyncio
import threading
from collections import OrderedDict
from typing import List, Optional, TYPE_CHECKING, Dict
from functools import lru_cache
from app.config import settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

class EmbeddingService:
    def __init__(self, model_name: str = ""):
        self.model_name = model_name or settings.embedding_model
        self.model: Optional["SentenceTransformer"] = None
        self._loading = False
        self._ready = threading.Event()
        
        # LRU cache for query embeddings to avoid redundant computation
        self._query_cache: OrderedDict[str, List[float]] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._max_cache_size = 2000  # Increased from 1000

    def load_model(self) -> None:
        """
        Load the embedding model synchronously and mark the service as ready.
        
        Attempts to instantiate a SentenceTransformer model for the configured model_name, selecting GPU when available or preferring an ONNX Runtime backend on CPU if the required packages are present; falls back to the PyTorch backend if ONNX is unavailable or initialization fails. On successful load, the model is assigned to `self.model` (and converted to FP16 on CUDA). On any failure the exception is logged but not re-raised. This method always clears the internal loading flag and sets the readiness event so waiters are unblocked.
        """
        if self.model:
            self._ready.set()
            return
        
        try:
            from sentence_transformers import SentenceTransformer
            import torch
            
            device = "cuda" if torch.cuda.is_available() else "cpu"

            # Phase 2.1: Prefer ONNX backend on CPU for ~2-3x faster inference
            backend = "torch"  # default
            if device == "cpu":
                try:
                    import onnxruntime
                    import optimum.onnxruntime
                    backend = "onnx"
                    logger.info("ONNX Runtime and Optimum verified — using ONNX backend for faster CPU inference.")
                except ImportError as e:
                    logger.info("ONNX backend unavailable (missing %s) — falling back to PyTorch.", str(e))
                except Exception as e:
                    logger.info("ONNX initialization failed: %s — falling back to PyTorch.", str(e))

            logger.info("Loading embedding model: %s on device: %s (backend: %s)", self.model_name, device, backend)
            
            if backend == "onnx":
                self.model = SentenceTransformer(
                    self.model_name, 
                    device=device, 
                    backend=backend,
                    model_kwargs={"file_name": "onnx/model_O4.onnx"}
                )
            else:
                self.model = SentenceTransformer(self.model_name, device=device)
            
            if device == "cuda":
                self.model.half() # Use FP16 on GPU
                
            logger.info("Model loaded successfully (backend=%s).", backend)
        except Exception as e:
            logger.exception("Failed to load embedding model: %s", e)
        finally:
            self._loading = False  # Reset so future retries are possible
            self._ready.set()  # Unblock waiters on both success and failure

    def load_model_background(self) -> None:
        """Starts model loading in a background thread (non-blocking)."""
        if self.model or self._loading:
            return
        self._loading = True
        thread = threading.Thread(target=self.load_model, daemon=True, name="emb-loader")
        thread.start()

    def wait_until_ready(self, timeout: float = 120) -> bool:
        """Block until the model is loaded. Returns True if ready."""
        return self._ready.wait(timeout=timeout)

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    async def embed_texts(self, texts: List[str], batch_size: Optional[int] = None) -> List[List[float]]:
        """
        Create vector embeddings for the provided texts.
        
        Deduplicates identical input strings so each unique text is encoded once, encodes unique texts in batches (using `batch_size` or the configured default), reports per-batch progress to the indexing service, and returns embeddings mapped to the original input order.
        
        Parameters:
            texts (List[str]): Strings to embed.
            batch_size (Optional[int]): Maximum number of texts per encoding batch; when omitted the configured default is used.
        
        Returns:
            List[List[float]]: A list of embedding vectors corresponding to each input string in the same order as `texts`.
        
        Raises:
            RuntimeError: If the embedding model failed to load and embeddings cannot be generated.
        """
        if not self.model:
            if self._loading:
                await asyncio.get_running_loop().run_in_executor(
                    None, self.wait_until_ready
                )
            else:
                await asyncio.get_running_loop().run_in_executor(
                    None, self.load_model
                )
        if self.model is None:
            raise RuntimeError("Embedding model failed to load. Cannot generate embeddings.")

        # ── Deduplicate texts ──────────────────────────────────────────
        unique_texts: List[str] = []
        text_to_idx: Dict[str, int] = {}
        original_map: List[int] = []  # maps original index to deduplicated text index

        for text in texts:
            if text not in text_to_idx:
                text_to_idx[text] = len(unique_texts)
                unique_texts.append(text)
            original_map.append(text_to_idx[text])

        # Optimization: Dynamic batching based on text count and settings
        effective_batch_size = batch_size or settings.embedding_batch_size
        if len(unique_texts) < effective_batch_size:
            effective_batch_size = max(1, len(unique_texts))

        loop = asyncio.get_running_loop()
        model = self.model  # capture for closure
        
        # Internal progress reporting
        from app.indexing.service import progress
        
        def encode_with_progress():
            """
            Encode deduplicated texts in batches while reporting progress for each batch.
            
            Encodes `unique_texts` in batches of `effective_batch_size`, updates progress via `progress.set_current_file(...)` for each batch, and returns the concatenated embeddings in the same order as `unique_texts`.
            
            Returns:
                list[list[float]]: A list of embedding vectors (one per input text) where each embedding is a list of floats.
            """
            num_batches = (len(unique_texts) + effective_batch_size - 1) // effective_batch_size
            results = []
            for i in range(0, len(unique_texts), effective_batch_size):
                batch_num = i // effective_batch_size + 1
                progress.set_current_file(f"Phase 2/3: Embedding batch {batch_num}/{num_batches}…")
                batch = unique_texts[i : i + effective_batch_size]
                batch_embeddings = model.encode(
                    batch,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                )
                results.append(batch_embeddings)
            import numpy as np
            return np.vstack(results).tolist()

        unique_embeddings = await loop.run_in_executor(None, encode_with_progress)

        # Map back to original order
        embeddings = [unique_embeddings[original_map[i]] for i in range(len(texts))]
        if len(unique_texts) < len(texts):
            logger.debug(
                "Embedding dedup: %d texts → %d unique (saved %d encodes)",
                len(texts), len(unique_texts), len(texts) - len(unique_texts),
            )
        return embeddings

    async def embed_query(self, query: str) -> List[float]:
        """Generates embedding for a single query string with LRU caching."""
        # Optimization: Check LRU cache first
        with self._cache_lock:
            if query in self._query_cache:
                self._query_cache.move_to_end(query)  # Mark as recently used
                return self._query_cache[query]

        embeddings = await self.embed_texts([query])
        result = embeddings[0]

        # Optimization: Update cache with proper LRU eviction
        with self._cache_lock:
            if len(self._query_cache) >= self._max_cache_size:
                self._query_cache.popitem(last=False)  # Evict least-recently-used
            self._query_cache[query] = result
            
        return result
