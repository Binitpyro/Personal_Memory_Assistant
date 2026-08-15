import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.api.deps import get_llm
from app.main import app
from app.search import retrieval
from app.search.planner import QueryPlanner


@pytest.fixture(autouse=True)
def clean_cache_each_test():
    retrieval.clear_retrieval_cache()
    yield
    retrieval.clear_retrieval_cache()


@pytest.fixture
def override_llm():
    m = MagicMock()
    m.generate_answer = AsyncMock(return_value="Mocked response content")
    m.get_model_class = MagicMock(return_value="gemini-1.5-pro")

    async def fake_stream(*args, **kwargs):
        yield "Streaming chunk 1"
        yield "Streaming chunk 2"

    m.stream_answer = fake_stream
    return m


@pytest.mark.asyncio
async def test_api_query_standard(
    client: AsyncClient, mock_db, mock_emb, mock_lancedb, override_llm
):
    # Mock lancedb search_cache to avoid MagicMock await error
    mock_lancedb.search_cache = AsyncMock(return_value=None)
    mock_lancedb.add_query_cache = AsyncMock()

    # Insert mock file and chunk to SQLite
    file_id = await mock_db.insert_file(
        {
            "path": "d:/test_project/main.py",
            "size": 2048,
            "modified_at": "2026-03-03T12:00:00",
            "type": ".py",
            "folder_tag": "test_tag",
            "summary": "Main entry point file",
        }
    )
    await mock_db.insert_chunks_bulk(
        [
            {
                "file_id": file_id,
                "start_offset": 0,
                "end_offset": 100,
                "text_preview": "import os\ndef main():\n    print('Hello World')\n# End of file content string long enough to satisfy snippet length requirements",
            }
        ]
    )

    # Setup LanceDB search mocks
    mock_lancedb.semantic_search = AsyncMock(
        return_value={
            "ids": [["1"]],
            "distances": [[0.1]],
            "metadatas": [
                [
                    {
                        "file_path": "d:/test_project/main.py",
                        "text": "import os\ndef main():\n    print('Hello World')\n# End of file content string long enough to satisfy snippet length requirements",
                        "folder_tag": "test_tag",
                    }
                ]
            ],
        }
    )

    app.dependency_overrides[get_llm] = lambda: override_llm
    try:
        response = await client.post(
            "/api/query",
            json={
                "question": "How does the main function work?",
                "file_type": ".py",
                "folder_tag": "test_tag",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "answer" in data
        assert data["answer"] == "Mocked response content"
        assert len(data["sources"]) > 0
    finally:
        app.dependency_overrides.pop(get_llm, None)


@pytest.mark.asyncio
async def test_api_query_stream_standard(
    client: AsyncClient, mock_db, mock_emb, mock_lancedb, override_llm
):
    mock_lancedb.search_cache = AsyncMock(return_value=None)
    mock_lancedb.add_query_cache = AsyncMock()

    file_id = await mock_db.insert_file(
        {
            "path": "d:/test_project/main.py",
            "size": 2048,
            "modified_at": "2026-03-03T12:00:00",
            "type": ".py",
            "folder_tag": "test_tag",
            "summary": "Main entry point file",
        }
    )
    await mock_db.insert_chunks_bulk(
        [
            {
                "file_id": file_id,
                "start_offset": 0,
                "end_offset": 100,
                "text_preview": "import os\ndef main():\n    print('Hello World')\n# End of file content string long enough to satisfy snippet length",
            }
        ]
    )

    mock_lancedb.semantic_search = AsyncMock(
        return_value={
            "ids": [["1"]],
            "distances": [[0.1]],
            "metadatas": [
                [
                    {
                        "file_path": "d:/test_project/main.py",
                        "text": "import os\ndef main():\n    print('Hello World')\n# End of file content string long enough to satisfy snippet length",
                        "folder_tag": "test_tag",
                    }
                ]
            ],
        }
    )

    app.dependency_overrides[get_llm] = lambda: override_llm
    try:
        response = await client.post(
            "/api/query/stream",
            json={
                "question": "How does the main function work in stream?",
                "file_type": ".py",
                "folder_tag": "test_tag",
            },
        )
        assert response.status_code == 200
        body = response.text
        lines = [json.loads(line) for line in body.split("\n") if line.strip()]

        types = [l["type"] for l in lines]  # noqa: E741
        assert "sources" in types
        assert "content" in types
        assert "done" in types
    finally:
        app.dependency_overrides.pop(get_llm, None)


@pytest.mark.asyncio
async def test_stream_keepalive_does_not_truncate_slow_generator(client, monkeypatch):
    """A gap longer than the keepalive must not end the stream.

    ``asyncio.wait_for`` cancels what it waits on, so the previous
    implementation threw CancelledError into ``stream_rag`` at its suspension
    point. The next ``anext()`` then raised StopAsyncIteration, the answer was
    silently cut short at the first slow token, and the history/telemetry
    writes at the end of the generator never ran. On a 3 tok/s local provider
    that gap is the normal first query, not an edge case.
    """
    from app.api import search as search_api

    monkeypatch.setattr(search_api, "_KEEPALIVE_SECONDS", 0.05)

    async def slow_stream(*args, **kwargs):
        yield {"type": "content", "text": "before"}
        await asyncio.sleep(0.2)  # 4x the keepalive
        yield {"type": "content", "text": "after"}

    monkeypatch.setattr(retrieval, "stream_rag", slow_stream)

    response = await client.post("/api/query/stream", json={"question": "a slow question"})
    assert response.status_code == 200

    lines = [json.loads(line) for line in response.text.split("\n") if line.strip()]
    types = [line["type"] for line in lines]

    assert "ping" in types, f"keepalive never fired: {types}"
    assert [line["text"] for line in lines if line["type"] == "content"] == [
        "before",
        "after",
    ], f"stream truncated across the keepalive: {types}"
    assert types[-1] == "done"


@pytest.mark.asyncio
async def test_api_query_fast_path_metadata(
    client: AsyncClient, mock_db, mock_emb, mock_lancedb, override_llm
):
    mock_lancedb.search_cache = AsyncMock(return_value=None)

    app.dependency_overrides[get_llm] = lambda: override_llm
    try:
        response = await client.post("/api/query", json={"question": "how many files do i have?"})
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "fast_path"
        assert "answer" in data
    finally:
        app.dependency_overrides.pop(get_llm, None)


@pytest.mark.asyncio
async def test_api_query_fast_path_project(
    client: AsyncClient, mock_db, mock_emb, mock_lancedb, override_llm
):
    mock_lancedb.search_cache = AsyncMock(return_value=None)

    # Insert folder profile to trigger fast path project profiles
    await mock_db.execute_query(
        "INSERT INTO folder_profiles (folder_tag, folder_path, project_type, profile_text) VALUES (?, ?, ?, ?)",
        ("test_tag", "d:/test_project", "Python", "Synthesized project profile text for testing"),
    )

    app.dependency_overrides[get_llm] = lambda: override_llm
    try:
        response = await client.post("/api/query", json={"question": "show project summary"})
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "fast_path"
        assert "test_tag" in data["answer"]
    finally:
        app.dependency_overrides.pop(get_llm, None)


@pytest.mark.asyncio
async def test_query_challenge_mode(
    client: AsyncClient, mock_db, mock_emb, mock_lancedb, override_llm
):
    mock_lancedb.search_cache = AsyncMock(return_value=None)
    mock_lancedb.semantic_search = AsyncMock(
        return_value={
            "ids": [["1"]],
            "distances": [[0.1]],
            "metadatas": [
                [
                    {
                        "file_path": "d:/test_project/main.py",
                        "text": "import os\ndef main():\n    print('Hello World')\n# End of file content string long enough to satisfy snippet length",
                        "folder_tag": "test_tag",
                    }
                ]
            ],
        }
    )

    app.dependency_overrides[get_llm] = lambda: override_llm
    try:
        response = await client.post(
            "/api/query", json={"question": "Explain implementation details", "mode": "challenge"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "answer" in data
    finally:
        app.dependency_overrides.pop(get_llm, None)


@pytest.mark.asyncio
async def test_query_history_and_clear(client: AsyncClient, mock_db):
    # Insert dummy query history entries
    await mock_db.save_query("What is Python?", "Python is a language.", 1, 120.5)

    response = await client.get("/api/query/history?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 1
    assert data["history"][0]["question"] == "What is Python?"

    # Clear history
    clear_response = await client.post("/api/query/history/clear")
    assert clear_response.status_code == 200
    assert clear_response.json()["message"] == "Query history cleared"

    # Verify history is empty
    history_after = await client.get("/api/query/history?limit=10")
    assert len(history_after.json()["history"]) == 0


@pytest.mark.asyncio
async def test_retrieval_semantic_cache_hit(mock_db, mock_emb, mock_lancedb, override_llm):
    mock_lancedb.search_cache = AsyncMock(
        return_value={
            "response_text": "Cached response content",
            "query_text": "cached query",
            "timestamp": 12345.67,
        }
    )
    planner = QueryPlanner()
    res = await retrieval.full_rag(
        query="cached query",
        db=mock_db,
        embedding_service=mock_emb,
        lancedb_client=mock_lancedb,
        llm_client=override_llm,
        planner=planner,
    )
    assert res["answer"] == "Cached response content"


@pytest.mark.asyncio
async def test_retrieval_llm_failure_handling(mock_db, mock_emb, mock_lancedb, override_llm):
    mock_lancedb.search_cache = AsyncMock(return_value=None)
    mock_lancedb.add_query_cache = AsyncMock()
    override_llm.generate_answer = AsyncMock(side_effect=Exception("LLM Connection Failure"))
    planner = QueryPlanner()

    # Insert mock file and chunk to SQLite so the document is found
    file_id = await mock_db.insert_file(
        {
            "path": "d:/test_project/main.py",
            "size": 2048,
            "modified_at": "2026-03-03T12:00:00",
            "type": ".py",
            "folder_tag": "test_tag",
            "summary": "Main entry point file",
        }
    )
    await mock_db.insert_chunks_bulk(
        [
            {
                "file_id": file_id,
                "start_offset": 0,
                "end_offset": 100,
                "text_preview": "import os\ndef main():\n    print('Hello World')\n# End of file content string long enough to satisfy snippet length requirements",
            }
        ]
    )

    mock_lancedb.semantic_search = AsyncMock(
        return_value={
            "ids": [["1"]],
            "distances": [[0.1]],
            "metadatas": [
                [
                    {
                        "file_path": "d:/test_project/main.py",
                        "text": "import os\ndef main():\n    print('Hello World')\n# End of file content string long enough to satisfy snippet length requirements",
                        "folder_tag": "test_tag",
                    }
                ]
            ],
        }
    )

    res = await retrieval.full_rag(
        query="Explain implementation details",
        db=mock_db,
        embedding_service=mock_emb,
        lancedb_client=mock_lancedb,
        llm_client=override_llm,
        planner=planner,
    )
    assert "_is_error" in res
    assert res["_is_error"] is True
    assert "error while generating the answer" in res["answer"]
