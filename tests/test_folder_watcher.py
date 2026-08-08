"""Coverage for the poll-based folder watcher.

The watcher's job is deciding *when to ask*, not what changed - change detection
stays in IndexingService. So these test the decisions: does it skip when it
should, does it survive a bad cycle, does it stop cleanly.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.indexing.watcher import FolderWatcher


class _FakeDB:
    def __init__(self, rows=None, raises=False):
        self.rows = rows or []
        self.raises = raises

    async def execute_query(self, sql, params=()):
        if self.raises:
            raise RuntimeError("db unavailable")
        return self.rows


def _watcher(rows=None, raises=False, service=None):
    service = service or MagicMock(index_folders=AsyncMock())
    return FolderWatcher(_FakeDB(rows, raises), lambda: service), service


@pytest.fixture(autouse=True)
def _enable_watcher(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "watcher_enabled", True)
    monkeypatch.setattr(settings, "watcher_interval_seconds", 10)


# ── which folders it watches ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_watches_only_folders_the_user_already_indexed(tmp_path):
    """It must never index somewhere the user did not ask for. Roots come from
    folder_profiles, which only an explicit index run populates."""
    indexed = tmp_path / "notes"
    indexed.mkdir()

    watcher, service = _watcher(rows=[(str(indexed),)])
    folders = await watcher.run_once()

    assert folders == [str(indexed)]
    service.index_folders.assert_awaited_once_with([str(indexed)])


@pytest.mark.asyncio
async def test_skips_roots_that_no_longer_exist(tmp_path):
    """An unmounted external drive or deleted folder must be skipped, not
    handed to the indexer - scanning nothing could read as 'all files gone'."""
    live = tmp_path / "live"
    live.mkdir()
    gone = tmp_path / "unmounted"

    watcher, service = _watcher(rows=[(str(live),), (str(gone),)])
    folders = await watcher.run_once()

    assert folders == [str(live)]
    service.index_folders.assert_awaited_once_with([str(live)])


@pytest.mark.asyncio
async def test_no_indexed_folders_means_no_work():
    watcher, service = _watcher(rows=[])
    assert await watcher.run_once() == []
    service.index_folders.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreadable_database_does_not_raise():
    watcher, service = _watcher(raises=True)
    assert await watcher.run_once() == []
    service.index_folders.assert_not_awaited()


# ── interaction with manual indexing ────────────────────────────────────────


@pytest.mark.asyncio
async def test_skips_while_a_manual_index_is_running(tmp_path):
    """Two concurrent index runs over the same folders would contend on the
    same tables. Skipping loses nothing: the next cycle re-derives from disk."""
    from app.indexing.service import indexing_lock

    folder = tmp_path / "notes"
    folder.mkdir()
    watcher, service = _watcher(rows=[(str(folder),)])

    async with indexing_lock:
        folders = await watcher.run_once()

    assert folders == []
    service.index_folders.assert_not_awaited()

    # Once the lock is free the next cycle proceeds.
    assert await watcher.run_once() == [str(folder)]


# ── loop robustness ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failing_cycle_does_not_kill_the_loop(tmp_path, monkeypatch):
    """A watcher that dies on one bad cycle is worse than none: it fails
    silently and never recovers."""
    from app.indexing import watcher as watcher_mod

    # Drop the interval floor so the loop runs its iterations immediately
    # instead of the test waiting out real poll intervals.
    monkeypatch.setattr(watcher_mod, "_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(watcher_mod.settings, "watcher_interval_seconds", 0)

    folder = tmp_path / "notes"
    folder.mkdir()
    watcher, _ = _watcher(rows=[(str(folder),)])

    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient scan failure")
        return ["ok"]

    monkeypatch.setattr(watcher, "run_once", flaky)
    # Drive the loop body directly rather than waiting out real intervals.
    monkeypatch.setattr(watcher, "_stopping", _FakeEvent(fire_after=3))

    await watcher._run()

    assert calls["n"] >= 2, "loop stopped after the failing cycle"


class _FakeEvent:
    """asyncio.Event stand-in that reports 'set' after N checks, so the loop
    runs a bounded number of iterations without real sleeping."""

    def __init__(self, fire_after: int):
        self.fire_after = fire_after
        self.checks = 0

    def is_set(self) -> bool:
        self.checks += 1
        return self.checks > self.fire_after

    def set(self) -> None:
        self.checks = self.fire_after + 1

    def clear(self) -> None:
        self.checks = 0

    async def wait(self) -> bool:
        # Never resolves, so wait_for in the loop always times out and the loop
        # proceeds to the work step immediately.
        await asyncio.sleep(3600)
        return True


# ── start / stop ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_watcher_does_not_start(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "watcher_enabled", False)
    watcher, _ = _watcher()

    assert watcher.start() is False
    assert watcher._task is None


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_is_clean(tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    watcher, _ = _watcher(rows=[(str(folder),)])

    assert watcher.start() is True
    first = watcher._task
    assert watcher.start() is True
    assert watcher._task is first, "start() spawned a second loop"

    await watcher.stop()
    assert watcher._task is None

    # Stopping again must not raise.
    await watcher.stop()


@pytest.mark.asyncio
async def test_stop_before_start_is_safe():
    watcher, _ = _watcher()
    await watcher.stop()
