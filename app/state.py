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

# ── Service Resolution (Centralized in lifespan) ──────────────────────
indexing_service_cls: Any = None
progress_obj: Any = None
full_rag_func: Any = None
insights_service_cls: Any = None
