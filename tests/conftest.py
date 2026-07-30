import asyncio
import ctypes
import gc
import os
import re
import sys
from unittest.mock import AsyncMock, MagicMock

if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()
if not hasattr(ctypes, "get_last_error"):
    ctypes.get_last_error = MagicMock(return_value=0)

# Ensure nltk is mocked to prevent network calls and lookup issues
try:
    import nltk
except ImportError:
    nltk = MagicMock()
    sys.modules["nltk"] = nltk

nltk.download = MagicMock(return_value=True)
if not hasattr(nltk, "data"):
    nltk.data = MagicMock()
nltk.data.find = MagicMock(return_value="mocked/path")

if not hasattr(nltk, "tokenize"):
    nltk.tokenize = MagicMock()


def dummy_sent_tokenize(text, language="english"):
    if not text:
        return []
    sents = re.split(r"(?<=[.!?])\s+", text)
    return [s for s in sents if s]


nltk.tokenize.sent_tokenize = MagicMock(side_effect=dummy_sent_tokenize)

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

os.environ["X_LOCAL_ACCESS_TOKEN"] = "test-token"  # noqa: S105

from app.api.deps import get_db, get_emb, get_lancedb, get_llm  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.storage.db import DatabaseManager  # noqa: E402

# Override settings to ensure we don't accidentally write to real disk
settings.db_path = ":memory:"

# Disable rate limiter during tests
from app.api.limiter import limiter  # noqa: E402

limiter.enabled = False


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
        import numpy as np

        return np.array([[0.1] * 384 for _ in texts], dtype=np.float32)

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
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Local-Access-Token": token},
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True, scope="session")
def ensure_dummy_react_index():
    from pathlib import Path

    index_file = Path(__file__).parent.parent / "static" / "react" / "index.html"
    if not index_file.exists():
        index_file.parent.mkdir(parents=True, exist_ok=True)
        index_file.write_text("<!DOCTYPE html><html><body>PMA Test</body></html>")


@pytest.fixture(autouse=True)
async def cleanup_db():
    """M-10: Test Leak - Prevent SQL locks on Windows by explicitly closing
    database connections between tests."""
    yield
    from app.api.deps import _db_manager

    if _db_manager and _db_manager.conn:
        await _db_manager.close()
    gc.collect()


@pytest.fixture(autouse=True)
def mock_local_reachability_default(monkeypatch, request):
    """Default fixture to mock local reachability in tests unless test module explicitly exercises real socket logic."""
    if request.module and "test_provider_manifest" in request.module.__name__:
        return
    from app.providers import manifest
    manifest.clear_reachability_cache()
    monkeypatch.setattr(manifest, "is_local_endpoint_reachable", lambda url, timeout=0.2: True)

