import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.api.deps import ensure_indexing
from app.api.limiter import limiter


@contextlib.contextmanager
def _limiter_enabled():
    """conftest disables slowapi suite-wide; re-enable it for one test only.

    Both the flag and the shared MemoryStorage are restored, otherwise a
    consumed bucket leaks into whatever test runs next.
    """
    limiter.reset()
    limiter.enabled = True
    try:
        yield
    finally:
        limiter.enabled = False
        limiter.reset()


@pytest.mark.asyncio
async def test_indexing_endpoints_lifecycle(
    client: AsyncClient, mock_db, mock_emb, mock_lancedb, tmp_path: Path
):
    # Setup folders
    test_dir = tmp_path / "target_dir"
    test_dir.mkdir()

    # 1. Start indexing
    response = await client.post("/api/index/start", json={"folders": [str(test_dir)]})
    assert response.status_code == 200
    assert response.json()["message"] == "Indexing started"

    # Give it a tiny bit of time to run background task
    await asyncio.sleep(0.1)

    # 2. Get status
    status_response = await client.get("/api/index/status")
    assert status_response.status_code == 200
    status_data = status_response.json()
    assert "status" in status_data
    assert "files_indexed" in status_data

    # 3. Cancel indexing (when not running)
    _, progress = ensure_indexing()
    progress.status = "idle"
    cancel_fail = await client.post("/api/index/cancel")
    assert cancel_fail.status_code == 400

    # Cancel indexing (when running)
    progress.status = "running"
    cancel_ok = await client.post("/api/index/cancel")
    assert cancel_ok.status_code == 200
    assert progress.is_cancelled is True
    assert progress.status == "cancelling"

    # Reset progress status
    progress.status = "idle"
    progress.is_cancelled = False

    # 4. Progress Stream (make status idle so it terminates immediately without hanging ASGI stream)
    progress.total_files = 10
    progress.processed_files = 3
    progress.status = "idle"

    response_stream = await client.get("/api/index/progress-stream")
    assert response_stream.status_code == 200
    assert "event: progress" in response_stream.text
    assert '"processed_files": 3' in response_stream.text

    # 5. Cleanup stale files
    # Insert mock file to SQLite first
    await mock_db.insert_file(
        {
            "path": str(test_dir / "stale.py"),
            "size": 100,
            "modified_at": "2026-03-03T12:00:00",
            "type": ".py",
            "folder_tag": "test_tag",
            "summary": "Stale file summary",
        }
    )
    cleanup_response = await client.post("/api/index/cleanup")
    assert cleanup_response.status_code == 200
    assert "cleaned_paths" in cleanup_response.json()

    # 6. Export index
    export_response = await client.get("/api/index/export")
    assert export_response.status_code == 200
    export_data = export_response.json()
    assert "file_count" in export_data
    assert "files" in export_data

    # 7. Remove folder index
    remove_response = await client.post(
        "/api/index/folder/remove", json={"folders": [str(test_dir)]}
    )
    assert remove_response.status_code == 200
    assert "chunks_removed" in remove_response.json()

    # 8. Clear index
    mock_lancedb.clear_all = AsyncMock()
    clear_response = await client.post("/api/index/clear")
    assert clear_response.status_code == 200
    assert mock_lancedb.clear_all.called


@pytest.mark.asyncio
async def test_indexing_blocked_and_invalid_folders(client: AsyncClient):
    # System path (should be blocked)
    blocked_path = "C:\\Windows\\System32"
    response = await client.post("/api/index/start", json={"folders": [blocked_path]})
    assert response.status_code == 400
    assert "No valid folder" in response.json()["error"]

    # Non-existent path
    non_existent = "d:/non_existent_folder_xyz_123"
    response2 = await client.post("/api/index/start", json={"folders": [non_existent]})
    assert response2.status_code == 400
    assert "No valid folder" in response2.json()["error"]


@pytest.mark.asyncio
async def test_destructive_index_endpoints_are_rate_limited(
    client: AsyncClient, mock_db, mock_lancedb
):
    """/clear wipes the index and used to accept unlimited calls."""
    # conftest's mock_lancedb only stubs the search/add methods as awaitable.
    mock_lancedb.clear_all = AsyncMock()

    with _limiter_enabled():
        codes = [(await client.post("/api/index/clear")).status_code for _ in range(4)]

    assert codes[:3] == [200, 200, 200], codes
    assert codes[3] == 429, codes


@pytest.mark.asyncio
async def test_index_status_is_not_rate_limited(client: AsyncClient):
    """LibraryPage polls /status every 10s; a limit here breaks idle browsing."""
    with _limiter_enabled():
        codes = [(await client.get("/api/index/status")).status_code for _ in range(12)]

    assert set(codes) == {200}, codes
