"""Content-addressed geometry cache."""

import hashlib


class GeometryCache:
    """Keeps evaluated geometry keyed by content digest.

    Timestamps were not enough: two writes inside the same second are
    indistinguishable at second resolution, which served stale geometry.
    """

    def __init__(self, max_entries: int = 64):
        self.max_entries = max_entries
        self._store: dict = {}
        self._order: list = []

    @staticmethod
    def digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def get(self, key: str):
        if key in self._store:
            self._order.remove(key)
            self._order.append(key)
            return self._store[key]
        return None

    def put(self, key: str, value) -> None:
        if key not in self._store and len(self._order) >= self.max_entries:
            evicted = self._order.pop(0)
            del self._store[evicted]
        self._store[key] = value
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)
