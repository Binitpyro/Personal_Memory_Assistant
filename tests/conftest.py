from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

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
    # Initialize basic schema for testing
    await db.conn.executescript("""
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE,
            size INTEGER,
            type TEXT,
            folder_tag TEXT,
            usage_count INTEGER DEFAULT 0,
            summary TEXT DEFAULT '',
            sha256 TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            modified_at TEXT DEFAULT ''
        );
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            file_id INTEGER,
            start_offset INTEGER,
            end_offset INTEGER,
            text_preview BLOB,
            created_at TEXT DEFAULT ''
        );
        CREATE TABLE folder_profiles (
            folder_path TEXT,
            project_type TEXT,
            file_count INTEGER,
            total_size_bytes INTEGER
        );
        CREATE TABLE query_history (
            id INTEGER PRIMARY KEY,
            question TEXT,
            answer TEXT,
            source_count INTEGER,
            latency_ms REAL,
            created_at TEXT DEFAULT ''
        );
        CREATE TABLE unreal_project_facts (
            folder_path TEXT PRIMARY KEY,
            folder_tag TEXT,
            project_name TEXT,
            engine_version TEXT,
            total_assets INTEGER,
            map_count INTEGER,
            character_blueprints INTEGER,
            pawn_blueprints INTEGER,
            skeletal_meshes INTEGER,
            material_count INTEGER,
            niagara_systems INTEGER,
            environment_assets INTEGER,
            metadata_source TEXT,
            profile_text TEXT
        );
    """)
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
