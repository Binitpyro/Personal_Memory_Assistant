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
        """Loads the embedding model (blocking)."""
        if self.model:
            self._ready.set()
            return
        
        try:
            from sentence_transformers import SentenceTransformer
            import torch
            
            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info("Loading embedding model: %s on device: %s", self.model_name, device)
            
            # Optimization: Load with half-precision if on GPU for faster inference
            self.model = SentenceTransformer(self.model_name, device=device)
            
            if device == "cuda":
                self.model.half() # Use FP16 on GPU
                
            logger.info("Model loaded successfully.")
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
        """Generates embeddings for a list of texts asynchronously.

        Optimisation: deduplicates identical texts so the model only encodes
        each unique string once, then maps results back to the original order.
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
        original_map: List[int] = []  # original_map[i] = index in unique_texts

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
        
        unique_embeddings = await loop.run_in_executor(
            None,
            lambda: model.encode(
                unique_texts,
                batch_size=effective_batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True, # Ensure unit length for cosine similarity
            ).tolist(),
        )

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
