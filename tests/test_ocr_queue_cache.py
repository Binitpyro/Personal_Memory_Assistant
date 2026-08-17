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


async def test_release_claim_refunds_the_attempt(mock_db):
    """A claim that could not be acted on must not cost the document anything.

    mark_failed(terminal=False) re-arms the row but leaves the claim-time
    attempt spent, which is right when the document failed. When the *claim*
    was unusable - the indexer went busy mid-write - charging it would retire
    an innocent document after three unlucky collisions.
    """
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0], 1)

    for _ in range(5):
        claimed = await ocr_queue.claim_next(mock_db, max_attempts=3)
        assert claimed is not None, "release_claim must not consume the budget"
        assert claimed.attempts == 1
        await ocr_queue.release_claim(mock_db, PATH_A)
        row = await ocr_queue.get_row(mock_db, PATH_A)
        assert row.status == QueueStatus.PENDING
        assert row.attempts == 0


async def test_release_claim_does_not_underflow_attempts(mock_db):
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0], 1)
    await ocr_queue.release_claim(mock_db, PATH_A)
    row = await ocr_queue.get_row(mock_db, PATH_A)
    assert row.attempts == 0


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
    """Owed pages come from the queued set, not the document page count.

    This previously asserted (10 - 4) + 4, i.e. page_count as the denominator,
    and to make that arithmetic work it recorded 4 pages done against a
    document that had only 3 pages queued. Both halves were artefacts of the
    same bug: page_count is the whole PDF, pages_json is what OCR was asked for.
    """
    await ocr_queue.enqueue_document(mock_db, PATH_A, [0, 1, 2], 10)
    await ocr_queue.enqueue_document(mock_db, PATH_B, [0], 4)
    await ocr_queue.mark_progress(mock_db, PATH_A, 2)

    counts = await ocr_queue.counts(mock_db)
    assert counts["pending"] == 2
    assert counts["pages_pending"] == (3 - 2) + 1


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


async def test_model_version_change_invalidates_the_cache(mock_db):
    await ocr_cache.put_pages(mock_db, KEY, [page(0)])
    assert await ocr_cache.get_pages(mock_db, KEY, [0])

    assert await ocr_cache.get_pages(mock_db, KEY, [0], engine_id="some-other-model") == {}


async def test_pages_are_read_back_only_under_the_engine_that_wrote_them(mock_db):
    """Two engines must never read each other's text.

    The identity is part of the primary key, but that only protects anything if
    the value actually varies - it used to be a module constant, so a second
    model would have silently aliased onto the first model's rows.
    """
    await ocr_cache.put_pages(mock_db, KEY, [page(0)], engine_id="ppocrv4-server@dml")

    assert await ocr_cache.get_pages(mock_db, KEY, [0], engine_id="ppocrv4-mobile") == {}
    assert await ocr_cache.get_pages(mock_db, KEY, [0], engine_id="ppocrv4-server@dml")


def test_cpu_engine_identity_is_unqualified():
    """Existing cached pages must stay readable after this change.

    Every page cached before the execution provider entered the identity was
    written under a bare model version by a CPU run, so the CPU case has to keep
    producing exactly that string or the whole corpus silently re-OCRs.
    """
    from app.ocr.settings import MODEL_VERSION, engine_identity

    assert engine_identity(MODEL_VERSION, "CPUExecutionProvider") == MODEL_VERSION
    assert engine_identity(MODEL_VERSION, None) == MODEL_VERSION
    assert engine_identity(MODEL_VERSION, "") == MODEL_VERSION
    assert engine_identity("ppocrv4-server", "DmlExecutionProvider") == "ppocrv4-server@dml"


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


async def test_pages_pending_counts_queued_pages_not_document_pages(mock_db):
    """A mixed native/scanned PDF must not report its native pages as pending.

    `page_count` is the whole document (service.py passes meta.page_count
    alongside meta.ocr_pages); only `pages_json` holds what OCR was asked for.
    Deriving the backlog from page_count left a 10-page PDF with 3 scanned
    pages reporting 7 pages pending forever, so the Library backlog banner
    never cleared.
    """
    await ocr_queue.enqueue_document(mock_db, r"C:\docs\mixed.pdf", [4, 5, 6], 10)

    counts = await ocr_queue.counts(mock_db)
    assert counts["pages_pending"] == 3, "only the queued pages are owed"

    await ocr_queue.claim_next(mock_db, max_attempts=3)
    await ocr_queue.mark_progress(mock_db, r"C:\docs\mixed.pdf", 3)

    counts = await ocr_queue.counts(mock_db)
    assert counts["pages_pending"] == 0, "all queued pages done means nothing pending"


async def test_pages_pending_survives_corrupt_pages_json(mock_db):
    """json_array_length raises on invalid JSON; the count must not blow up."""
    await ocr_queue.enqueue_document(mock_db, r"C:\docs\bad.pdf", [0, 1], 2)
    await mock_db.execute_write(
        "UPDATE ocr_queue SET pages_json = ? WHERE file_path = ?",
        ("{not json", r"C:\docs\bad.pdf"),
    )

    counts = await ocr_queue.counts(mock_db)
    assert counts["pages_pending"] == 0
