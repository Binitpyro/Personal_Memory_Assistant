from unittest.mock import AsyncMock, MagicMock

import pytest
import gc
from httpx import ASGITransport, AsyncClient

import os
os.environ["X_LOCAL_ACCESS_TOKEN"] = "test-token"

from app.api.deps import get_db, get_emb, get_lancedb, get_llm
from app.config import settings
from app.main import app
from app.storage.db import DatabaseManager

# Override settings to ensure we don't accidentally write to real disk
settings.db_path = ":memory:"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def mock_db():
    db = DatabaseManager(":memory:")
    await db.connect()
    # Initialize full schema for testing
    await db.init_db()
    yield db
    await db.close()


@pytest.fixture
def mock_emb():
    mock = MagicMock()
    mock.embed_query = AsyncMock(return_value=[0.1] * 384)

    async def _embed_texts(texts, *args, **kwargs):
        return [[0.1] * 384 for _ in texts]

    mock.embed_texts = AsyncMock(side_effect=_embed_texts)
    return mock


@pytest.fixture
def mock_lancedb():
    mock = MagicMock()
    mock.semantic_search = AsyncMock(
        return_value={"ids": [[]], "distances": [[]], "metadatas": [[]]}
    )
    mock.search_summaries = AsyncMock(
        return_value={"ids": [[]], "distances": [[]], "metadatas": [[]]}
    )
    mock.add_documents = AsyncMock()
    mock.add_summaries_batch = AsyncMock()
    return mock


@pytest.fixture
def mock_llm():
    mock = MagicMock()
    mock.generate_response = AsyncMock(return_value="Mocked response")
    return mock


@pytest.fixture
async def client(mock_db, mock_emb, mock_lancedb, mock_llm):
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_emb] = lambda: mock_emb
    app.dependency_overrides[get_lancedb] = lambda: mock_lancedb
    app.dependency_overrides[get_llm] = lambda: mock_llm

    import os
    token = os.environ.get("X_LOCAL_ACCESS_TOKEN", "test_token")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-Local-Access-Token": token}) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
async def cleanup_db():
    """M-10: Test Leak - Prevent SQL locks on Windows by explicitly closing
    database connections between tests."""
    yield
    from app.api.deps import _db_manager

    if _db_manager and _db_manager.conn:
        await _db_manager.close()
    gc.collect()
