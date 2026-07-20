import hashlib
import time
from typing import Any


class ValidationCache:
    def __init__(self, ttl_seconds: int = 60):
        self.ttl = ttl_seconds
        # dict mapping (provider_id, base_url, key_hash) -> (timestamp, result)
        self._cache: dict[tuple[str, str | None, str], tuple[float, Any]] = {}

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

    def set(self, provider_id: str, base_url: str | None, api_key: str | None, val: Any) -> None:
        key_hash = self._hash_key(api_key)
        key = (provider_id, base_url, key_hash)
        self._cache[key] = (time.time(), val)

    def clear(self) -> None:
        self._cache.clear()


validation_cache = ValidationCache()
