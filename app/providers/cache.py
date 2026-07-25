import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HEAP_PATH = Path("data/model_heap.json")


class ValidationCache:
    def __init__(self, ttl_seconds: int = 60):
        self.ttl = ttl_seconds
        # dict mapping (provider_id, base_url, key_hash) -> (timestamp, result)
        self._cache: dict[tuple[str, str | None, str], tuple[float, Any]] = {}
        # Retry failure counters per provider_id
        self._retry_counts: dict[str, int] = {}
        # Persistent model heap: provider_id -> list of ModelInfo dicts
        self._persistent_heap: dict[str, list[dict[str, Any]]] = self._load_heap()

    def _load_heap(self) -> dict[str, list[dict[str, Any]]]:
        if not HEAP_PATH.exists():
            return {}
        try:
            with open(HEAP_PATH, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.debug("Failed to load model heap: %s", e)
            return {}

    def _save_heap(self) -> None:
        try:
            HEAP_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(HEAP_PATH, "w", encoding="utf-8") as f:
                json.dump(self._persistent_heap, f, indent=2)
        except Exception as e:
            logger.debug("Failed to save model heap: %s", e)

    def _hash_key(self, api_key: str | None) -> str:
        if not api_key:
            return ""
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    def get(self, provider_id: str, base_url: str | None, api_key: str | None) -> Any | None:
        key_hash = self._hash_key(api_key)
        key = (provider_id, base_url, key_hash)
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < self.ttl:
                return val
            else:
                del self._cache[key]
        return None

    def get_offline_fallback(self, provider_id: str) -> Any | None:
        if provider_id in self._persistent_heap and self._persistent_heap[provider_id]:
            return {
                "ok": False,
                "latency_ms": 0,
                "models": self._persistent_heap[provider_id],
                "error": "Provider offline. Using cached model heap.",
                "error_code": "cached_offline",
                "cached_offline": True,
                "server_time": None
            }
        return None

    def set(self, provider_id: str, base_url: str | None, api_key: str | None, val: Any) -> None:
        key_hash = self._hash_key(api_key)
        key = (provider_id, base_url, key_hash)
        self._cache[key] = (time.time(), val)

        # If validation succeeded and returned models, save to persistent heap
        if isinstance(val, dict) and val.get("ok") and val.get("models"):
            self._persistent_heap[provider_id] = val["models"]
            self._retry_counts[provider_id] = 0
            self._save_heap()
        elif isinstance(val, dict) and not val.get("ok"):
            self._retry_counts[provider_id] = self._retry_counts.get(provider_id, 0) + 1

    def get_retry_count(self, provider_id: str) -> int:
        return self._retry_counts.get(provider_id, 0)

    def get_persistent_models(self, provider_id: str) -> list[dict[str, Any]]:
        return self._persistent_heap.get(provider_id, [])

    def clear(self) -> None:
        self._cache.clear()


validation_cache = ValidationCache()

