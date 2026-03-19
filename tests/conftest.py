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
    """
    Configure AnyIO to use the asyncio backend for the test session.
    
    Returns:
        str: The AnyIO backend name `"asyncio"`.
    """
    return "asyncio"

@pytest.fixture
async def mock_db():
    """
    Provide a DatabaseManager connected to an in-memory SQLite database with a minimal schema for tests.
    
    Yields:
        DatabaseManager: A connected DatabaseManager using ":memory:" with tables `files`, `chunks`, `folder_profiles`, and `query_history` created. The connection is closed when the fixture is torn down.
    """
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
    """
    Create a mock embedding provider for tests.
    
    The returned MagicMock exposes async methods `embed_query` and `embed_texts`:
    - `embed_query` returns a list of 384 floats each equal to 0.1.
    - `embed_texts` returns a list containing one embedding (a list of 384 floats each equal to 0.1).
    
    Returns:
        MagicMock: A mock embedding object with the described async methods.
    """
    mock = MagicMock()
    mock.embed_query = AsyncMock(return_value=[0.1] * 384)
    mock.embed_texts = AsyncMock(return_value=[[0.1] * 384])
    return mock

@pytest.fixture
def mock_chroma():
    """
    Create a mocked Chroma-like client with asynchronous `query_documents` and `add_documents` methods.
    
    Returns:
        MagicMock: A mock where `query_documents` is an AsyncMock that returns an empty list and `add_documents` is an AsyncMock.
    """
    mock = MagicMock()
    mock.query_documents = AsyncMock(return_value=[])
    mock.add_documents = AsyncMock()
    return mock

@pytest.fixture
def mock_llm():
    """
    Constructs a MagicMock LLM configured for tests.
    
    The returned mock exposes an async `generate_response` method that always returns the string "Mocked response".
    
    Returns:
        MagicMock: A mock LLM with `generate_response` set to an `AsyncMock` returning "Mocked response".
    """
    mock = MagicMock()
    mock.generate_response = AsyncMock(return_value="Mocked response")
    return mock

@pytest.fixture
async def client(mock_db, mock_emb, mock_chroma, mock_llm):
    """
    Provide an httpx AsyncClient configured to call the FastAPI ASGI app with test dependency overrides applied.
    
    Overrides the application's dependency providers (database, embedding, chroma, LLM) with the supplied mocks, yields an AsyncClient bound to the app for use in tests, and clears the overrides after use.
    
    Parameters:
        mock_db: The test DatabaseManager (in-memory) to use in place of the real database dependency.
        mock_emb: Mock embedding provider returned by the `get_emb` dependency.
        mock_chroma: Mock Chroma-like vector store returned by the `get_chroma` dependency.
        mock_llm: Mock LLM returned by the `get_llm` dependency.
    
    Returns:
        An `httpx.AsyncClient` instance configured with ASGITransport bound to the application and base_url "http://test".
    """
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_emb] = lambda: mock_emb
    app.dependency_overrides[get_chroma] = lambda: mock_chroma
    app.dependency_overrides[get_llm] = lambda: mock_llm
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
        
    app.dependency_overrides.clear()
