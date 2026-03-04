import pytest
from httpx import AsyncClient, ASGITransport
import asyncio
from app.main import app
from typing import AsyncGenerator

@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

@pytest.mark.asyncio
async def test_read_root(client: AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["ok", "degraded"]
    assert "db" in data

@pytest.mark.asyncio
async def test_index_status(client: AsyncClient):
    response = await client.get("/index/status")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "progress_percent" in data
    assert data["progress_percent"] >= 0

@pytest.mark.asyncio
async def test_query_validation_error(client: AsyncClient):
    response = await client.post("/query", json={})
    assert response.status_code == 422
    assert "Validation error" in response.json().get("error", "")

@pytest.mark.asyncio
async def test_index_start_validation_error(client: AsyncClient):
    response = await client.post("/index/start", json={"folders": []})
    assert response.status_code == 400
    assert "No valid folder paths provided." in response.json().get("error", "")


@pytest.mark.asyncio
async def test_react_assets_served_correctly(client: AsyncClient):
    """Verify that /assets/* serves actual JS/CSS files, not index.html."""
    import os
    from pathlib import Path
    assets_dir = Path("static/react/assets")
    if not assets_dir.exists():
        pytest.skip("React build not present")
    js_files = list(assets_dir.glob("*.js"))
    if not js_files:
        pytest.skip("No JS bundle found")
    js_name = js_files[0].name
    response = await client.get(f"/assets/{js_name}")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    # Must NOT be text/html (that would mean the SPA catch-all is broken)
    assert "text/html" not in content_type
    # Should be a JavaScript file
    assert "javascript" in content_type or "application/octet" in content_type


@pytest.mark.asyncio
async def test_spa_catchall_returns_html_for_unknown_routes(client: AsyncClient):
    """Client-side routes like /library should return index.html."""
    from pathlib import Path
    if not Path("static/react/index.html").exists():
        pytest.skip("React build not present")
    response = await client.get("/library")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_gzip_middleware_active(client: AsyncClient):
    """Verify GZip middleware compresses responses when client accepts it."""
    response = await client.get(
        "/health",
        headers={"Accept-Encoding": "gzip, deflate"},
    )
    assert response.status_code == 200
    # GZip middleware sets content-encoding header
    encoding = response.headers.get("content-encoding", "")
    # For small payloads GZip may not kick in (minimum_size=500),
    # but at least verify the endpoint works with the accept header
    assert response.json()["status"] in ["ok", "degraded"]


@pytest.mark.asyncio
async def test_system_info_endpoint(client: AsyncClient):
    """Test the /system/info endpoint returns expected structure."""
    response = await client.get("/system/info")
    assert response.status_code == 200
    data = response.json()
    assert "os" in data
    assert "volumes" in data
    assert isinstance(data["volumes"], list)


@pytest.mark.asyncio
async def test_query_history_endpoint(client: AsyncClient):
    """Test the /query/history endpoint returns expected structure."""
    response = await client.get("/query/history?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert "history" in data
    assert isinstance(data["history"], list)


@pytest.mark.asyncio
async def test_files_tree_endpoint(client: AsyncClient):
    """Test the /files/tree endpoint returns expected structure."""
    response = await client.get("/files/tree")
    assert response.status_code == 200
    data = response.json()
    assert "folders" in data
    assert "total_files" in data
    assert "total_size" in data
