"""DAO for `ocr_queue`.

Plain async functions rather than a class - there is no state to hold, and the
table has exactly one consumer (the manager's drain loop). Lives here rather
than in `db.py` because `db.py` is already large and nothing else touches
these two tables.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.ocr.types import OcrQueueRow, QueueStatus

logger = logging.getLogger(__name__)

# A module constant, never request input. It is interpolated into f-string SQL
# below purely to avoid repeating the column list five times; every value is
# still bound as a parameter. Hence the S608 suppressions on those queries.
_COLUMNS = (
    "file_path, pages_json, page_count, pages_done, tier, status, "
    "force_ocr, attempts, last_error, enqueued_at, updated_at"
)


def _row_to_queue_row(row: Any) -> OcrQueueRow:
    try:
        pages = tuple(int(p) for p in json.loads(row[1] or "[]"))
    except (ValueError, TypeError):
        pages = ()
    try:
        status = QueueStatus(row[5])
    except ValueError:
        status = QueueStatus.PENDING
    return OcrQueueRow(
        file_path=row[0],
        pages=pages,
        page_count=int(row[2] or 0),
        pages_done=int(row[3] or 0),
        tier=row[4] or "cpu",
        status=status,
        force_ocr=bool(row[6]),
        attempts=int(row[7] or 0),
        last_error=row[8] or "",
        enqueued_at=row[9] or "",
        updated_at=row[10] or "",
    )


async def enqueue_document(
    db,
    file_path: str,
    pages: list[int],
    page_count: int,
    *,
    force: bool = False,
    tier: str = "cpu",
) -> None:
    """Add or refresh a file's OCR work item.

    Upserts on `file_path`, resetting progress and attempts: a re-index means
    the previous verdict is stale, and a file that failed three times deserves
    a fresh budget once its content changes.
    """
    if not pages:
        return
    await db.execute_write(
        """
        INSERT INTO ocr_queue
            (file_path, pages_json, page_count, pages_done, tier, status,
             force_ocr, attempts, last_error, enqueued_at, updated_at)
        VALUES (?, ?, ?, 0, ?, 'pending', ?, 0, '', datetime('now'), datetime('now'))
        ON CONFLICT(file_path) DO UPDATE SET
            pages_json = excluded.pages_json,
            page_count = excluded.page_count,
            pages_done = 0,
            tier       = excluded.tier,
            status     = 'pending',
            force_ocr  = excluded.force_ocr,
            attempts   = 0,
            last_error = '',
            updated_at = datetime('now')
        """,
        (
            file_path,
            json.dumps(sorted(set(int(p) for p in pages))),
            int(page_count),
            tier,
            1 if force else 0,
        ),
    )


async def claim_next(db, *, max_attempts: int) -> OcrQueueRow | None:
    """Atomically take the oldest pending row and mark it running.

    Increments `attempts` on claim, not on failure: a worker that hard-crashes
    never gets to report anything, and without this a crash-loop would retry
    the same document forever.
    """
    rows = await db.execute_write_returning(
        f"""
        UPDATE ocr_queue
           SET status = 'running',
               attempts = attempts + 1,
               updated_at = datetime('now')
         WHERE file_path = (
               SELECT file_path FROM ocr_queue
                WHERE status = 'pending' AND attempts < ?
                ORDER BY enqueued_at
                LIMIT 1
         )
        RETURNING {_COLUMNS}
        """,  # nosec B608 # noqa: S608
        (int(max_attempts),),
    )
    if not rows:
        return None
    return _row_to_queue_row(rows[0])


async def mark_progress(db, file_path: str, pages_done: int) -> None:
    await db.execute_write(
        "UPDATE ocr_queue SET pages_done = ?, updated_at = datetime('now') WHERE file_path = ?",
        (int(pages_done), file_path),
    )


async def mark_done(db, file_path: str, *, pages_done: int | None = None) -> None:
    if pages_done is None:
        await db.execute_write(
            "UPDATE ocr_queue SET status = 'done', last_error = '', "
            "updated_at = datetime('now') WHERE file_path = ?",
            (file_path,),
        )
    else:
        await db.execute_write(
            "UPDATE ocr_queue SET status = 'done', pages_done = ?, last_error = '', "
            "updated_at = datetime('now') WHERE file_path = ?",
            (int(pages_done), file_path),
        )


async def mark_failed(db, file_path: str, error: str, *, terminal: bool) -> None:
    """Record a failure.

    `terminal=False` returns the row to `pending` so the drain loop retries it;
    the attempt counter (bumped at claim time) is what eventually stops that.
    """
    await db.execute_write(
        "UPDATE ocr_queue SET status = ?, last_error = ?, updated_at = datetime('now') "
        "WHERE file_path = ?",
        ("failed" if terminal else "pending", (error or "")[:500], file_path),
    )


async def release_claim(db, file_path: str) -> None:
    """Hand a claimed row back untouched, refunding the attempt.

    Distinct from `mark_failed(terminal=False)`: that re-arms the row but leaves
    the claim-time attempt spent, which is correct when the *document* failed.
    This is for when the claim itself could not be acted on - the indexer went
    busy, or the worker died before this row was dispatched - where charging the
    document for someone else's timing would eventually retire it permanently.
    """
    await db.execute_write(
        "UPDATE ocr_queue SET status = 'pending', "
        "attempts = MAX(0, attempts - 1), updated_at = datetime('now') "
        "WHERE file_path = ?",
        (file_path,),
    )


async def mark_skipped(db, file_path: str, reason: str) -> None:
    await db.execute_write(
        "UPDATE ocr_queue SET status = 'skipped', last_error = ?, "
        "updated_at = datetime('now') WHERE file_path = ?",
        ((reason or "")[:500], file_path),
    )


async def requeue(db, file_path: str) -> None:
    """User-driven retry: clear the attempt budget and re-arm the row."""
    await db.execute_write(
        "UPDATE ocr_queue SET status = 'pending', attempts = 0, last_error = '', "
        "pages_done = 0, updated_at = datetime('now') WHERE file_path = ?",
        (file_path,),
    )


async def reset_running(db) -> int:
    """Return orphaned `running` rows to `pending` at startup.

    A hard kill (power loss, task manager) leaves rows claimed by a process
    that no longer exists. Without this they are stuck forever.
    """
    rows = await db.execute_query("SELECT COUNT(*) FROM ocr_queue WHERE status = 'running'")
    count = int(rows[0][0]) if rows else 0
    if count:
        await db.execute_write(
            "UPDATE ocr_queue SET status = 'pending', updated_at = datetime('now') "
            "WHERE status = 'running'"
        )
        logger.info("Recovered %d OCR queue row(s) stuck in 'running'.", count)
    return count


async def get_row(db, file_path: str) -> OcrQueueRow | None:
    rows = await db.execute_query(
        f"SELECT {_COLUMNS} FROM ocr_queue WHERE file_path = ?",  # nosec B608 # noqa: S608
        (file_path,),
    )
    return _row_to_queue_row(rows[0]) if rows else None


async def list_queue(
    db, *, limit: int = 50, offset: int = 0, status: str | None = None
) -> list[OcrQueueRow]:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    if status:
        rows = await db.execute_query(
            f"SELECT {_COLUMNS} FROM ocr_queue WHERE status = ? "  # nosec B608 # noqa: S608
            "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (status, limit, offset),
        )
    else:
        rows = await db.execute_query(
            f"SELECT {_COLUMNS} FROM ocr_queue "  # nosec B608 # noqa: S608
            "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    return [_row_to_queue_row(r) for r in rows]


async def counts(db) -> dict[str, int]:
    """Row counts per status, plus pages still owed across pending/running."""
    result = {s.value: 0 for s in QueueStatus}
    rows = await db.execute_query("SELECT status, COUNT(*) FROM ocr_queue GROUP BY status")
    for status, count in rows:
        result[str(status)] = int(count)

    pending_pages = await db.execute_query(
        "SELECT COALESCE(SUM(MAX(page_count - pages_done, 0)), 0) FROM ocr_queue "
        "WHERE status IN ('pending', 'running')"
    )
    result["pages_pending"] = int(pending_pages[0][0]) if pending_pages else 0
    return result


async def clear_queue(db) -> int:
    rows = await db.execute_query("SELECT COUNT(*) FROM ocr_queue")
    count = int(rows[0][0]) if rows else 0
    await db.execute_write("DELETE FROM ocr_queue")
    return count
