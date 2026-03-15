import pytest
from httpx import AsyncClient, ASGITransport
import aiosqlite
from unittest.mock import AsyncMock, MagicMock
from app.main import app, get_db, get_emb, get_chroma, get_llm
from app.storage.db import DatabaseManager

# Override settings to ensure we don't accidentally write to real disk
from app.config import settings
settings.db_path = ":memory:"

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def mock_db():
    db = DatabaseManager(":memory:")
    await db.connect()
    # Initialize basic schema for testing
    await db.conn.executescript("""
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT UNIQUE, size INTEGER, type TEXT, folder_tag TEXT, usage_count INTEGER DEFAULT 0, summary TEXT DEFAULT '', sha256 TEXT DEFAULT '', created_at TEXT DEFAULT '');
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, file_id INTEGER, start_offset INTEGER, end_offset INTEGER, text_preview BLOB, created_at TEXT DEFAULT '');
        CREATE TABLE folder_profiles (folder_path TEXT, project_type TEXT, file_count INTEGER, total_size_bytes INTEGER);
        CREATE TABLE query_history (id INTEGER PRIMARY KEY, question TEXT, answer TEXT, source_count INTEGER, latency_ms REAL, created_at TEXT DEFAULT '');
    """)
    yield db
    await db.close()

@pytest.fixture
def mock_emb():
    mock = MagicMock()
    mock.embed_query = AsyncMock(return_value=[0.1] * 384)
    mock.embed_texts = AsyncMock(return_value=[[0.1] * 384])
    return mock

@pytest.fixture
def mock_chroma():
    mock = MagicMock()
    mock.query_documents = AsyncMock(return_value=[])
    mock.add_documents = AsyncMock()
    return mock

@pytest.fixture
def mock_llm():
    mock = MagicMock()
    mock.generate_response = AsyncMock(return_value="Mocked response")
    return mock

@pytest.fixture
async def client(mock_db, mock_emb, mock_chroma, mock_llm):
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_emb] = lambda: mock_emb
    app.dependency_overrides[get_chroma] = lambda: mock_chroma
    app.dependency_overrides[get_llm] = lambda: mock_llm
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
        
    app.dependency_overrides.clear()
