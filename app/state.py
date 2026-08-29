"""
app/state.py — Shared mutable application state.

P3-2: Extracted from app/main.py to break the circular import that arises when
      app/api/system.py and other routers import from app.main to access
      _bg_tasks, _file_tree_cache, and _insights_cache.

Usage:
    from app.state import bg_tasks, file_tree_cache, insights_cache

Rationale:
- app/main.py imports routers from app/api/*.py
- app/api/system.py was importing _bg_tasks/_file_tree_cache/_insights_cache
  back from app/main.py  → circular dependency
- Placing shared state in a leaf module (no app imports) breaks the cycle.
"""

import asyncio
from typing import Any

# Background task set — keeps references so GC doesn't collect running coroutines
bg_tasks: set[asyncio.Task] = set()

# File tree cache — invalidated on index/clear operations
file_tree_cache: dict[str, Any] = {"data": None, "ts": 0.0}

# Insights cache — invalidated on index/clear operations
insights_cache: dict[str, Any] = {"data": None, "ts": 0.0}

# TTL for both caches (seconds)
CACHE_TTL: int = 10

# Split-brain sync status — idle | syncing | done | error
split_brain_sync_status: str = "idle"

# Readiness of the optional subsystems, written once each during lifespan.
#
# All three start their lives inside a `try` whose `except` only logs, which is
# the right policy — a broken OCR install must never stop the server coming up —
# but it left the user with no way to see the result. The reported symptom was
# "my PDFs never get text", noticed days later.
#
# States: "up" | "down" | "disabled" | "unknown". "unknown" is not a synonym for
# "down": a subsystem whose startup has not been attempted yet has not failed.
# "disabled" is a configuration choice, not a fault, and must not read as one.
subsystems: dict[str, dict[str, str]] = {
    "ocr": {"state": "unknown", "detail": ""},
    "watcher": {"state": "unknown", "detail": ""},
    "reranker": {"state": "unknown", "detail": ""},
}


# Embedding-model signature, compared at boot against what the index was built
# with. A mismatch means every stored vector is from a different vector space
# and semantic search is quietly returning nonsense.
#
# Surfaced the same way as split_brain_sync_status: recorded here, emitted in
# the health payload, rendered by the frontend. It used to exist only as a log
# banner, which nobody sees.
#
# `reembed` tracks the user-initiated repair: idle | running | done | error.
embedding_signature: dict[str, str | bool] = {
    "stored": "",
    "current": "",
    "mismatch": False,
    "reembed": "idle",
}


def set_embedding_signature(stored: str, current: str) -> None:
    """Record the boot-time signature comparison."""
    embedding_signature["stored"] = stored
    embedding_signature["current"] = current
    embedding_signature["mismatch"] = bool(stored) and stored != current


def set_subsystem(name: str, state: str, detail: str = "") -> None:
    """Record a subsystem's startup outcome. Unknown names are ignored."""
    if name in subsystems:
        subsystems[name] = {"state": state, "detail": detail[:200]}


# ── Service Resolution (Centralized in lifespan) ──────────────────────
indexing_service_cls: Any = None
progress_obj: Any = None
full_rag_func: Any = None
insights_service_cls: Any = None
