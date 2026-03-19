import pytest
from httpx import AsyncClient
import asyncio

@pytest.mark.asyncio
async def test_read_root(client: AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    # Testing the root alias used by some tests
    response = await client.get("/health")
    assert response.status_code == 200
    # Also test the standard API path
    response_api = await client.get("/api/health")
    assert response_api.status_code == 200
    data = response_api.json()
    assert data["status"] in ["ok", "degraded"]

@pytest.mark.asyncio
async def test_index_status(client: AsyncClient):
    response = await client.get("/api/index/status")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "progress_percent" in data

@pytest.mark.asyncio
async def test_query_validation_error(client: AsyncClient):
    response = await client.post("/api/query", json={})
    assert response.status_code == 422
    assert "Validation error" in response.json().get("error", "")

@pytest.mark.asyncio
async def test_index_start_validation_error(client: AsyncClient):
    response = await client.post("/api/index/start", json={"folders": []})
    assert response.status_code == 400
    assert "No valid folder paths provided." in response.json().get("error", "")

@pytest.mark.asyncio
async def test_react_assets_served_correctly(client: AsyncClient):
    """Verify that assets serve files, not index.html."""
    from pathlib import Path
    # Just test that some asset path doesn't return 200 index.html if it doesn't exist
    response = await client.get("/static/pma.css")
    # Even if file doesn't exist in test env, it should NOT be text/html from catch-all if it's a file path
    if response.status_code == 200:
        assert "text/css" in response.headers.get("content-type", "")

@pytest.mark.asyncio
async def test_spa_catchall_returns_html_for_unknown_routes(client: AsyncClient):
    """Client-side routes like /library should return index.html."""
    response = await client.get("/library", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

@pytest.mark.asyncio
async def test_system_info_endpoint(client: AsyncClient):
    response = await client.get("/api/system/info")
    assert response.status_code == 200
    data = response.json()
    assert "os" in data

@pytest.mark.asyncio
async def test_query_history_endpoint(client: AsyncClient):
    response = await client.get("/api/query/history?limit=5")
    assert response.status_code == 200
    assert "history" in response.json()

@pytest.mark.asyncio
async def test_files_tree_endpoint(client: AsyncClient):
    response = await client.get("/api/files/tree")
    assert response.status_code == 200
    assert "folders" in response.json()
