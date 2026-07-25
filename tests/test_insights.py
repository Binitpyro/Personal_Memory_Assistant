import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_insights_endpoints(client, mock_db):
    # Setup insights mocks
    mock_service = MagicMock()
    mock_service.get_dashboard_insights = AsyncMock(return_value={"total_size": 1234})
    mock_service.get_insights_for_extension = AsyncMock(
        return_value={"extension": ".py", "count": 10}
    )

    with patch("app.api.insights.ensure_insights", return_value=lambda db: mock_service):
        # Clear cache first
        from app.state import insights_cache

        insights_cache["data"] = None

        # 1. /insights - non-cached
        response = await client.get("/api/insights")
        assert response.status_code == 200
        assert response.json() == {"total_size": 1234}

        # 2. /insights - cached
        response_cached = await client.get("/api/insights")
        assert response_cached.status_code == 200
        assert response_cached.json() == {"total_size": 1234}

        # 3. /insights/by-type
        response_type = await client.get("/api/insights/by-type?extension=py")
        assert response_type.status_code == 200
        assert response_type.json() == {"extension": ".py", "count": 10}

        # 4. /insights exception path
        mock_service.get_dashboard_insights.side_effect = Exception("Service error")
        insights_cache["data"] = None  # Invalidate cache
        response_err = await client.get("/api/insights")
        assert response_err.status_code == 500
        assert "Service error" in response_err.json()["error"]

        # 5. /insights/by-type exception path
        mock_service.get_insights_for_extension.side_effect = Exception("Extension error")
        response_type_err = await client.get("/api/insights/by-type?extension=py")
        assert response_type_err.status_code == 500
        assert "Extension error" in response_type_err.json()["error"]


@pytest.mark.asyncio
async def test_files_tree_endpoint(client, mock_db):
    # Clear cache
    from app.state import file_tree_cache

    file_tree_cache["data"] = None

    # Populate mock_db
    await mock_db.execute_write(
        "INSERT INTO files (id, path, type, size, folder_tag, usage_count, modified_at) VALUES (1, 'dir/a.py', '.py', 100, 'folder_a', 2, 'now')"
    )
    await mock_db.execute_write(
        "INSERT INTO files (id, path, type, size, folder_tag, usage_count, modified_at) VALUES (2, 'dir/b.txt', '.txt', 200, NULL, NULL, 'now')"
    )

    # 1. Non-cached fetch
    response = await client.get("/api/files/tree")
    assert response.status_code == 200
    data = response.json()
    assert data["total_files"] == 2
    assert data["total_size"] == 300
    assert "folder_a" in data["folders"]
    assert "Unknown" in data["folders"]  # None tag maps to Unknown

    # 2. Cached fetch
    response_cached = await client.get("/api/files/tree")
    assert response_cached.status_code == 200
    assert response_cached.json() == data

    # 3. Exception path
    file_tree_cache["data"] = None
    with patch.object(mock_db, "get_all_files", side_effect=Exception("DB broke")):
        response_err = await client.get("/api/files/tree")
        assert response_err.status_code == 500
        assert "DB broke" in response_err.json()["error"]


@pytest.mark.asyncio
async def test_visualizer_binary_stream_endpoint(client, mock_db):
    # Populate mock_db
    await mock_db.execute_write(
        "INSERT INTO files (id, path, type, size, folder_tag, usage_count, modified_at) VALUES (1, 'a.py', '.py', 1024, 'tag1', 0, 'now')"
    )
    await mock_db.execute_write(
        "INSERT INTO files (id, path, type, size, folder_tag, usage_count, modified_at) VALUES (2, 'b.txt', '.txt', 2048, 'tag2', 0, 'now')"
    )

    # 1. /api/visualizer/stream
    response = await client.get("/api/visualizer/stream")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"

    content = response.read()
    assert len(content) in (64, 96)

    # Unpack first record: pos(fff) radius(f) parent_index(I) flags(I) type_hash(I) pad(I)
    _x, _y, _z, radius, parent_index, flags, type_hash, _pad = struct.unpack(
        "<ffffIIII", content[:32]
    )
    if len(content) == 96:
        # Rust layout: first node is root folder
        assert radius >= 2.0
        assert parent_index == 0xFFFFFFFF
        assert flags == 1
    else:
        # Python fallback layout: first node is a file
        assert radius == 2.0
        assert parent_index == 0
        assert flags == 0
        assert type_hash != 0

    # 2. /api/visualizer/stream?extension=py
    response_filtered = await client.get("/api/visualizer/stream?extension=py")
    assert response_filtered.status_code == 200
    content_filtered = response_filtered.read()
    assert len(content_filtered) in (
        32,
        64,
    )  # 32 bytes for Python fallback, 64 bytes for Rust layout (1 file + 1 root)

    # 3. Exception path
    with patch(
        "app.insights.visualizer._stream_visualizer_binary_impl",
        side_effect=Exception("Stream generation failed"),
    ):
        response_err = await client.get("/api/visualizer/stream")
        assert response_err.status_code == 500
        assert "Failed to stream visualizer data" in response_err.json()["error"]
