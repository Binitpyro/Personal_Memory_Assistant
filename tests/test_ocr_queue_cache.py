"""Queue lifecycle and cache behaviour against a real in-memory schema."""

import pytest

from app.ocr import cache as ocr_cache
from app.ocr import queue as ocr_queue
from app.ocr.types import OcrLine, OcrPage, QueueStatus

PATH_A = r"C:\docs\scan_a.pdf"
PATH_B = r"C:\docs\scan_b.pdf"
KEY = "a" * 64


def page(num, *, text="hello world", conf=0.9, low=False):
    return OcrPage(
        page_num=num,
        lines=(OcrLine(text=text, conf=conf, low=low),),
        mean_conf=conf,
        elapsed_ms=10,
    )


# ── queue lifecycle ──────────────────────────────────────────────────────


async def test_enqueue_claim_done_lifecycle(mock_db):
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0, 2, 5], 6)

    claimed = await ocr_queue.claim_next(mock_db, max_attempts=3)
    assert claimed is not None
    assert claimed.file_path == PATH_A
    assert claimed.pages == (0, 2, 5)
    assert claimed.status == QueueStatus.RUNNING
    assert claimed.attempts == 1

    await ocr_queue.mark_done(mock_db, PATH_A, pages_done=3)
    row = await ocr_queue.get_row(mock_db, PATH_A)
    assert row.status == QueueStatus.DONE
    assert row.pages_done == 3


async def test_claim_returns_none_when_queue_is_empty(mock_db):
    assert await ocr_queue.claim_next(mock_db, max_attempts=3) is None


async def test_claim_does_not_hand_out_the_same_row_twice(mock_db):
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0], 1)
    assert await ocr_queue.claim_next(mock_db, max_attempts=3) is not None
    assert await ocr_queue.claim_next(mock_db, max_attempts=3) is None


async def test_claim_is_oldest_first(mock_db):
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0], 1)
    await mock_db.execute_write(
        "UPDATE ocr_queue SET enqueued_at = '2000-01-01 00:00:00' WHERE file_path = ?", (PATH_A,)
    )
    await ocr_queue.enqueue_document(mock_db, PATH_B, [0], 1)

    assert (await ocr_queue.claim_next(mock_db, max_attempts=3)).file_path == PATH_A


async def test_attempts_accumulate_and_then_exhaust(mock_db):
    """Attempts increment on *claim*, so a crash-looping worker still stops."""
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0], 1)

    for expected in (1, 2, 3):
        claimed = await ocr_queue.claim_next(mock_db, max_attempts=3)
        assert claimed.attempts == expected
        await ocr_queue.mark_failed(mock_db, PATH_A, "boom", terminal=False)

    assert await ocr_queue.claim_next(mock_db, max_attempts=3) is None


async def test_requeue_clears_the_attempt_budget(mock_db):
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0], 1)
    for _ in range(3):
        await ocr_queue.claim_next(mock_db, max_attempts=3)
        await ocr_queue.mark_failed(mock_db, PATH_A, "boom", terminal=True)

    await ocr_queue.requeue(mock_db, PATH_A)
    claimed = await ocr_queue.claim_next(mock_db, max_attempts=3)
    assert claimed is not None
    assert claimed.attempts == 1


async def test_reenqueue_resets_progress_and_attempts(mock_db):
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0, 1], 2)
    await ocr_queue.claim_next(mock_db, max_attempts=3)
    await ocr_queue.mark_failed(mock_db, PATH_A, "boom", terminal=True)

    # A re-index means the old verdict is stale; the file gets a fresh budget.
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0, 1, 2], 3)
    row = await ocr_queue.get_row(mock_db, PATH_A)
    assert row.status == QueueStatus.PENDING
    assert row.attempts == 0
    assert row.pages == (0, 1, 2)
    assert row.last_error == ""


async def test_enqueue_with_no_pages_is_a_noop(mock_db):
    await ocr_queue.enqueue_document(mock_db, PATH_A, [], 0)
    assert await ocr_queue.get_row(mock_db, PATH_A) is None


async def test_reset_running_recovers_orphaned_rows(mock_db):
    """A hard kill leaves rows claimed by a process that no longer exists."""
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0], 1)
    await ocr_queue.claim_next(mock_db, max_attempts=3)

    assert await ocr_queue.reset_running(mock_db) == 1
    assert (await ocr_queue.get_row(mock_db, PATH_A)).status == QueueStatus.PENDING


async def test_counts_reports_pages_pending(mock_db):
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0, 1, 2], 10)
    await ocr_queue.enqueue_document(mock_db, PATH_B, [0], 4)
    await ocr_queue.mark_progress(mock_db, PATH_A, 4)

    counts = await ocr_queue.counts(mock_db)
    assert counts["pending"] == 2
    assert counts["pages_pending"] == (10 - 4) + 4


async def test_list_queue_filters_by_status(mock_db):
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0], 1)
    await ocr_queue.enqueue_document(mock_db, PATH_B, [0], 1)
    await ocr_queue.mark_failed(mock_db, PATH_A, "kaboom", terminal=True)

    failed = await ocr_queue.list_queue(mock_db, status="failed")
    assert [r.file_path for r in failed] == [PATH_A]
    assert failed[0].last_error == "kaboom"


# ── cache ────────────────────────────────────────────────────────────────


async def test_cache_round_trip_preserves_low_flags(mock_db):
    """Low-confidence lines must survive so the floor can be raised later."""
    original = OcrPage(
        page_num=3,
        lines=(
            OcrLine("clear text", 0.95, False),
            OcrLine("blurry text", 0.11, True),
        ),
        mean_conf=0.53,
    )
    await ocr_cache.put_pages(mock_db, KEY, [original])

    got = await ocr_cache.get_pages(mock_db, KEY, [3])
    restored = got[3]
    assert [ln.low for ln in restored.lines] == [False, True]
    assert restored.full_text == "clear text\nblurry text"
    assert restored.indexable_text == "clear text"


async def test_cache_miss_is_absent_not_empty(mock_db):
    await ocr_cache.put_pages(mock_db, KEY, [page(0)])
    got = await ocr_cache.get_pages(mock_db, KEY, [0, 1, 2])
    assert set(got) == {0}


@pytest.mark.parametrize("bad_key", ["", "ERROR", "CANCELLED"])
async def test_sentinel_content_keys_are_never_cached(mock_db, bad_key):
    """These come from a failed or interrupted hash pass and identify nothing."""
    assert await ocr_cache.put_pages(mock_db, bad_key, [page(0)]) == 0
    assert await ocr_cache.get_pages(mock_db, bad_key, [0]) == {}
    rows = await mock_db.execute_query("SELECT COUNT(*) FROM ocr_cache")
    assert rows[0][0] == 0


async def test_failed_page_with_no_text_is_not_cached(mock_db):
    """Caching a failure would suppress the retry."""
    failed = OcrPage(page_num=0, lines=(), error="OCR_PAGE_TIMEOUT")
    assert await ocr_cache.put_pages(mock_db, KEY, [failed]) == 0


async def test_model_version_change_invalidates_the_cache(mock_db, monkeypatch):
    await ocr_cache.put_pages(mock_db, KEY, [page(0)])
    assert await ocr_cache.get_pages(mock_db, KEY, [0])

    monkeypatch.setattr(ocr_cache, "MODEL_VERSION", "some-other-model")
    assert await ocr_cache.get_pages(mock_db, KEY, [0]) == {}


async def test_preproc_change_invalidates_the_cache(mock_db, monkeypatch):
    """Changing DPI must miss, not return text rendered at the old resolution."""
    await ocr_cache.put_pages(mock_db, KEY, [page(0)])
    monkeypatch.setattr(ocr_cache, "preproc_hash", lambda: "different0000")
    assert await ocr_cache.get_pages(mock_db, KEY, [0]) == {}


async def test_corrupt_cache_row_degrades_gracefully(mock_db):
    await ocr_cache.put_pages(mock_db, KEY, [page(0)])
    await mock_db.execute_write("UPDATE ocr_cache SET text = 'not json'")

    restored = (await ocr_cache.get_pages(mock_db, KEY, [0]))[0]
    assert restored.error == "CACHE_CORRUPT"
    assert restored.indexable_text == ""


async def test_evict_lru_drops_oldest_first_and_lands_under_target(mock_db):
    big = "x" * 2000
    for i in range(20):
        await ocr_cache.put_pages(mock_db, KEY, [page(i, text=big)])

    total_before = await ocr_cache.total_bytes(mock_db, reconcile=True)
    # Age the first ten so LRU has an unambiguous ordering.
    await mock_db.execute_write(
        "UPDATE ocr_cache SET last_used_at = '2000-01-01 00:00:00' WHERE page_num < 10"
    )

    cap = total_before // 2
    freed = await ocr_cache.evict_lru(mock_db, max_bytes=cap)

    assert freed > 0
    assert await ocr_cache.total_bytes(mock_db, reconcile=True) <= cap * 0.90
    survivors = await mock_db.execute_query("SELECT page_num FROM ocr_cache ORDER BY page_num")
    assert all(p[0] >= 10 for p in survivors)


async def test_evict_is_a_noop_under_the_cap(mock_db):
    await ocr_cache.put_pages(mock_db, KEY, [page(0)])
    assert await ocr_cache.evict_lru(mock_db, max_bytes=100 * 1024 * 1024) == 0


async def test_get_pages_refreshes_last_used_at(mock_db):
    await ocr_cache.put_pages(mock_db, KEY, [page(0)])
    await mock_db.execute_write("UPDATE ocr_cache SET last_used_at = '2000-01-01 00:00:00'")

    await ocr_cache.get_pages(mock_db, KEY, [0])

    rows = await mock_db.execute_query("SELECT last_used_at FROM ocr_cache")
    assert not rows[0][0].startswith("2000")


async def test_clear_cache_resets_the_byte_counter(mock_db):
    await ocr_cache.put_pages(mock_db, KEY, [page(0), page(1)])
    assert await ocr_cache.clear_cache(mock_db) == 2
    assert await ocr_cache.total_bytes(mock_db) == 0
