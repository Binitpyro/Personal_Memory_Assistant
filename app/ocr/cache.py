"""DAO for `ocr_cache`, keyed on file content rather than identity.

Because the key is the content hash, this cache survives a full index reset
and a file being moved or renamed. `clear_all()` deliberately does not touch
it - re-indexing the same bytes should cost nothing.

The cache stores *every* recognized line, including ones below the confidence
floor. Only the indexer filters. That means raising `ocr_conf_floor` later
re-filters from cache instead of re-running OCR over the whole corpus.
"""

from __future__ import annotations

import contextlib
import logging

from app.config import settings
from app.ocr.settings import expected_engine_identity, preproc_hash
from app.ocr.types import OcrPage

logger = logging.getLogger(__name__)

#: Hashing sentinels the indexing pipeline writes into `files.sha256` when the
#: hash pass fails or is interrupted. They identify no content at all, so a
#: cache row keyed on one would be served to unrelated files.
INVALID_CONTENT_KEYS = frozenset({"", "ERROR", "CANCELLED"})

#: Running-total key in `system_state`. SUM(bytes) over a 500 MB table on
#: every page write is a needless scan; we reconcile periodically instead.
_BYTES_KEY = "ocr_cache_bytes"

_SQLITE_MAX_VARS = 900


def is_valid_content_key(content_key: str | None) -> bool:
    return bool(content_key) and content_key not in INVALID_CONTENT_KEYS


async def get_pages(
    db, content_key: str, pages: list[int], *, engine_id: str | None = None
) -> dict[int, OcrPage]:
    """Return cached pages for this content, keyed by page number.

    Misses are simply absent from the result. Touches `last_used_at` on hits so
    LRU eviction reflects real usage.

    `engine_id` identifies the engine whose output is acceptable. It defaults to
    whatever the installed tier is expected to produce, so a page recognized by
    a different model or execution provider misses rather than being served as
    if this engine had produced it.
    """
    if not is_valid_content_key(content_key) or not pages:
        return {}

    model_version = engine_id or expected_engine_identity()
    preproc = preproc_hash()
    found: dict[int, OcrPage] = {}

    page_list = sorted(set(int(p) for p in pages))
    for i in range(0, len(page_list), _SQLITE_MAX_VARS):
        batch = page_list[i : i + _SQLITE_MAX_VARS]
        placeholders = ",".join("?" * len(batch))
        rows = await db.execute_query(
            f"SELECT page_num, text FROM ocr_cache "  # noqa: S608
            f"WHERE content_key = ? AND model_version = ? AND preproc_hash = ? "
            f"AND page_num IN ({placeholders})",
            (content_key, model_version, preproc, *batch),
        )
        for page_num, raw in rows:
            found[int(page_num)] = OcrPage.from_cache_json(int(page_num), raw)

    if found:
        hit_pages = sorted(found)
        for i in range(0, len(hit_pages), _SQLITE_MAX_VARS):
            batch = hit_pages[i : i + _SQLITE_MAX_VARS]
            placeholders = ",".join("?" * len(batch))
            await db.execute_write(
                f"UPDATE ocr_cache SET last_used_at = datetime('now') "  # noqa: S608
                f"WHERE content_key = ? AND model_version = ? AND preproc_hash = ? "
                f"AND page_num IN ({placeholders})",
                (content_key, model_version, preproc, *batch),
            )

    return found


async def put_pages(
    db, content_key: str, pages: list[OcrPage], *, engine_id: str | None = None
) -> int:
    """Cache recognized pages. Returns bytes written.

    Refuses invalid content keys outright - a row stored under "ERROR" would
    later be served to any other file whose hash also failed.

    `engine_id` must identify the engine that actually produced `pages` - the
    caller reads it off the worker's `ready` message rather than assuming it.
    Labelling output with the engine we *expected* to run would let a silently
    degraded tier write its text under another tier's key, which is precisely
    the aliasing the key exists to prevent.
    """
    if not is_valid_content_key(content_key) or not pages:
        return 0

    model_version = engine_id or expected_engine_identity()
    preproc = preproc_hash()
    written = 0
    net_delta = 0

    for page in pages:
        # A page that failed outright has no text worth remembering, and
        # caching it would suppress the retry.
        if page.error and not page.lines:
            continue
        payload = page.to_cache_json()
        size = len(payload.encode("utf-8"))

        # Compute exact byte delta if row already exists under this primary key
        prev_rows = await db.execute_query(
            "SELECT bytes FROM ocr_cache "
            "WHERE content_key = ? AND page_num = ? AND model_version = ? AND preproc_hash = ?",
            (content_key, int(page.page_num), model_version, preproc),
        )
        old_size = int(prev_rows[0][0]) if prev_rows and prev_rows[0][0] is not None else 0

        await db.execute_write(
            "INSERT OR REPLACE INTO ocr_cache "
            "(content_key, page_num, model_version, preproc_hash, text, mean_conf, "
            " bytes, created_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (
                content_key,
                int(page.page_num),
                model_version,
                preproc,
                payload,
                float(page.mean_conf),
                size,
            ),
        )
        written += size
        net_delta += (size - old_size)

    if net_delta:
        await _bump_bytes(db, net_delta)
    return written


async def _bump_bytes(db, delta: int) -> None:
    try:
        current = await db.get_system_state(_BYTES_KEY)
        total = int(current or 0) + delta
        await db.set_system_state(_BYTES_KEY, str(max(0, total)))
    except Exception as exc:
        logger.debug("Could not update OCR cache byte counter: %s", exc)


async def total_bytes(db, *, reconcile: bool = False) -> int:
    """Approximate cache size, or the exact SUM when `reconcile` is set."""
    if not reconcile:
        with contextlib.suppress(Exception):
            cached = await db.get_system_state(_BYTES_KEY)
            if cached is not None:
                return int(cached)

    rows = await db.execute_query("SELECT COALESCE(SUM(bytes), 0) FROM ocr_cache")
    total = int(rows[0][0]) if rows else 0
    with contextlib.suppress(Exception):
        await db.set_system_state(_BYTES_KEY, str(total))
    return total


async def evict_lru(db, *, max_bytes: int | None = None, target_ratio: float = 0.90) -> int:
    """Drop least-recently-used pages until the cache fits. Returns bytes freed.

    Evicts down to `target_ratio` of the cap rather than exactly to it, so a
    steady-state workload doesn't re-trigger eviction on every single write.
    """
    if max_bytes is None:
        max_bytes = settings.ocr_cache_max_mb * 1024 * 1024
    if max_bytes <= 0:
        return 0

    total = await total_bytes(db, reconcile=True)
    if total <= max_bytes:
        return 0

    target = int(max_bytes * target_ratio)
    freed = 0
    # Bounded loop: a corrupt count or a concurrent writer must not spin here.
    for _ in range(1000):
        if total <= target:
            break
        rows = await db.execute_query(
            "SELECT rowid, bytes, last_used_at FROM ocr_cache ORDER BY last_used_at ASC LIMIT 200"
        )
        if not rows:
            break
        row_pairs = [(int(r[0]), str(r[2] or "")) for r in rows]
        batch_bytes = sum(int(r[1] or 0) for r in rows)
        placeholders = " OR ".join(["(rowid = ? AND last_used_at = ?)"] * len(row_pairs))
        flat_params = [val for pair in row_pairs for val in pair]
        await db.execute_write(
            f"DELETE FROM ocr_cache WHERE {placeholders}",  # noqa: S608
            tuple(flat_params),
        )
        freed += batch_bytes
        total -= batch_bytes

    await db.set_system_state(_BYTES_KEY, str(max(0, total)))
    if freed:
        logger.info("OCR cache eviction freed %.1f MB", freed / (1024 * 1024))
    return freed


async def clear_cache(db) -> int:
    """Wipe every cached page. Only ever user-initiated."""
    rows = await db.execute_query("SELECT COUNT(*) FROM ocr_cache")
    count = int(rows[0][0]) if rows else 0
    await db.execute_write("DELETE FROM ocr_cache")
    await db.set_system_state(_BYTES_KEY, "0")
    return count
