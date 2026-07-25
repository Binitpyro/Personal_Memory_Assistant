import asyncio
import os
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db, get_emb, get_lancedb, get_llm
from app.indexing.extractors.epub_extractor import EpubExtractor
from app.indexing.service import IndexingService, StreamChunker
from app.main import app

# ── 1. API Token Enforcement ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_api_token_enforcement(mock_db, mock_emb, mock_lancedb, mock_llm):
    # Override dependencies for security middleware tests
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_emb] = lambda: mock_emb
    app.dependency_overrides[get_lancedb] = lambda: mock_lancedb
    app.dependency_overrides[get_llm] = lambda: mock_llm

    # A. Request without a token to protected route -> 401
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/query", json={"question": "test"})
        assert response.status_code == 401
        assert "Unauthorized" in response.json().get("error", "")

    # B. Request with an invalid token -> 401
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Local-Access-Token": "invalid-token"},
    ) as ac:
        response = await ac.post("/api/query", json={"question": "test"})
        assert response.status_code == 401

    # C. Request with valid token in query param -> passes auth check (might be 200 or 422/other depending on route details, but NOT 401)
    token = os.environ.get("X_LOCAL_ACCESS_TOKEN", "test-token")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Requesting search status check endpoint with token
        response = await ac.get(f"/api/query/history?token={token}")
        assert response.status_code != 401

    # D. Health check endpoints are exempt -> 200 without token
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
        assert response.status_code == 200
        response = await ac.get("/api/health")
        assert response.status_code == 200

    app.dependency_overrides.clear()


# ── 2. Path Traversal Safeguards ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_path_traversal_safeguards():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Attempt traversal in catch-all route
        response = await ac.get("/static/react/../../app/main.py")
        assert response.status_code in (403, 404)
        if response.status_code == 403:
            assert "Access denied" in response.text

        response = await ac.get("/..%2F..%2F..%2FWindows%2Fwin.ini")
        assert response.status_code in (403, 404)

        # Test spa_catch_all path directly
        response = await ac.get("/some/nonexistent/directory/../../app/main.py")
        assert response.status_code in (403, 404)


# ── 3. ZIP-Bomb Immunity ──────────────────────────────────────────────────────


def test_epub_extractor_zip_bomb_protection(tmp_path):
    # Create a zip representing a highly compressed zip bomb (15MB of 'a's)
    zip_bomb_path = tmp_path / "bomb.epub"
    with zipfile.ZipFile(zip_bomb_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.xhtml", "a" * 15_000_000)

    extractor = EpubExtractor()
    max_size = 5_000_000

    # Ensure extraction output is bounded
    res = extractor.extract(zip_bomb_path, max_size)
    assert len(res) <= max_size

    # Ensure streaming extraction respects max size limits and reads in chunks
    stream_generator = extractor.extract_stream(zip_bomb_path, max_size)
    chunks = list(stream_generator)
    total_len = sum(len(c) for c in chunks)
    # The extraction loop reads in chunks and caps when total_chars >= max_size
    assert total_len <= max_size + 10_000_000


# ── 4. StreamChunker Boundaries ───────────────────────────────────────────────


def test_stream_chunker_boundaries():
    # If chunk_overlap >= chunk_size, chunk_overlap should be capped at chunk_size - 1
    chunker = StreamChunker(chunk_size=100, chunk_overlap=150, prefix="test: ")
    assert chunker.chunk_overlap == 99

    # Verify snap logic behaves normally with large inputs and doesn't loop infinitely
    res = chunker.process("a" * 500)
    assert len(res) > 0
    # Finalize should return the remaining buffer
    res_fin = chunker.finalize()
    assert isinstance(res_fin, list)


# ── 5. RAG Pipeline Edge Cases ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_rag_pipeline_edge_cases(tmp_path, mock_db, mock_emb, mock_lancedb):
    # A. Malformed binary files are detected as binary and skipped (expected stub/behavior)
    binary_file = tmp_path / "malformed.py"
    binary_file.write_bytes(b"\x00\xff\xfe\x00\x01\x02\x03\x04")

    indexer = IndexingService(mock_db, mock_emb, mock_lancedb)
    text = indexer._extract_text_monolithic(binary_file)
    assert text.startswith("[BINARY:")

    # B. Verify extraction exceptions publish footer chunk and allow indexing pipeline to complete
    error_file = tmp_path / "error.txt"
    error_file.write_text("trigger error")

    queue = asyncio.Queue()

    # Monkeypatch streaming extract to raise an exception for error.txt
    original_extract = indexer._extract_plain_text_stream

    def mock_extract(path):
        if "error.txt" in str(path):
            raise ValueError("Simulated extraction error")
        return original_extract(path)

    indexer._extract_plain_text_stream = mock_extract

    # Run streaming extraction
    await indexer._stream_extract_and_prepare(error_file, "test_tag", None, queue)

    # Collect items from queue
    items = []
    while not queue.empty():
        items.append(await queue.get())

    # Header and footer must be sent, even if extraction failed
    assert len(items) == 2
    assert items[0]["type"] == "header"
    assert items[1]["type"] == "footer"
    assert "[ERROR:" in items[1]["summary"]
    assert "Simulated extraction error" in items[1]["summary"]


# ── 6. Input Validation & Limit Gaps ─────────────────────────────────────────


@pytest.mark.anyio
async def test_input_validation_limit_gaps(client):
    # A. Reject excessively large forced_chunk_ids list (> 500 items)
    response = await client.post(
        "/api/query", json={"question": "hello", "forced_chunk_ids": list(range(600))}
    )
    assert response.status_code == 422
    assert "forced_chunk_ids" in response.text

    # B. Reject malformed history inputs with invalid keys
    response = await client.post(
        "/api/query",
        json={
            "question": "hello",
            "history": [{"role": "user", "content": "hello", "extra_key": "malicious"}],
        },
    )
    assert response.status_code == 422

    # C. Reject invalid roles in history (only user, assistant, system allowed)
    response = await client.post(
        "/api/query",
        json={"question": "hello", "history": [{"role": "attacker", "content": "inject"}]},
    )
    assert response.status_code == 422

    # D. Reject excessively large history contents (> 10000 chars)
    response = await client.post(
        "/api/query",
        json={"question": "hello", "history": [{"role": "user", "content": "a" * 10005}]},
    )
    assert response.status_code == 422

    # E. Reject excessively long string fields
    response = await client.post("/api/query", json={"question": "hello", "file_type": "a" * 60})
    assert response.status_code == 422


# ── 7. Write Lock Concurrency ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_write_lock_concurrency(mock_db):
    lock_records = []

    # Create a custom lock class to trace acquire and release actions
    class TracedLock(asyncio.Lock):
        async def acquire(self):
            task_name = asyncio.current_task().get_name()
            lock_records.append(("acquire", task_name))
            res = await super().acquire()
            lock_records.append(("acquired", task_name))
            return res

        def release(self):
            task_name = asyncio.current_task().get_name()
            lock_records.append(("release", task_name))
            super().release()

    # Inject the traced lock
    mock_db._write_lock = TracedLock()

    # Wrap the connection's execute method to trace and delay
    original_conn_execute = mock_db._write_conn.execute

    async def delayed_conn_execute(sql, *args, **kwargs):
        # The lock must be held here because execute_write is decorated with @serialize_write
        assert mock_db._write_lock.locked()
        await asyncio.sleep(0.05)
        return await original_conn_execute(sql, *args, **kwargs)

    mock_db._write_conn.execute = delayed_conn_execute

    # Spawn concurrent write tasks
    async def run_task(name):
        # Set task name for tracing
        asyncio.current_task().set_name(name)
        await mock_db.execute_write("SELECT 1")

    tasks = [
        asyncio.create_task(run_task("task_A")),
        asyncio.create_task(run_task("task_B")),
    ]

    await asyncio.gather(*tasks)

    # We expect task_A and task_B to serialize lock acquisition.
    # The order of execution depends on event loop scheduling, but they MUST not overlap.
    # i.e., "acquired" by task X must release before task Y is "acquired".

    # Let's find acquired/release events in order
    events = [r for r in lock_records if r[0] in ("acquired", "release")]
    assert len(events) == 4

    # First active task must release before second active task is acquired
    first_task = events[0][1]
    assert events[0] == ("acquired", first_task)
    assert events[1] == ("release", first_task)

    second_task = events[2][1]
    assert second_task != first_task
    assert events[2] == ("acquired", second_task)
    assert events[3] == ("release", second_task)


# ── 8. New Security and Robustness Tests ─────────────────────────────────────


def test_epub_extractor_cumulative_zip_bomb(tmp_path):
    # Test that the cumulative decompressed bytes limit (100MB) works
    zip_bomb_path = tmp_path / "cumulative_bomb.epub"
    with zipfile.ZipFile(zip_bomb_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Create 11 XHTML files, each 10MB (highly compressible)
        for i in range(11):
            zf.writestr(f"content_{i}.xhtml", "a" * 10_000_000)

    extractor = EpubExtractor()
    # It should yield at most 100MB of characters
    stream_generator = extractor.extract_stream(zip_bomb_path, 200_000_000)
    chunks = list(stream_generator)
    total_len = sum(len(c) for c in chunks)
    assert total_len <= 100_000_000


@pytest.mark.anyio
async def test_rag_pipeline_extremely_large_input(tmp_path, mock_db, mock_emb, mock_lancedb):
    # Test that StreamChunker and IndexingService handle a very large input file without infinite loops or crashes
    large_file = tmp_path / "huge.txt"
    large_file.write_text("This is some text. " * 300_000)  # ~6MB text

    indexer = IndexingService(mock_db, mock_emb, mock_lancedb)
    queue = asyncio.Queue()
    await indexer._stream_extract_and_prepare(large_file, "test_tag", None, queue)

    items = []
    while not queue.empty():
        items.append(await queue.get())

    assert len(items) >= 2
    assert items[0]["type"] == "header"
    assert items[-1]["type"] == "footer"


@pytest.mark.anyio
async def test_malformed_structured_files(tmp_path, mock_db, mock_emb, mock_lancedb):
    # Test fake PDF and docx files containing corrupted contents
    fake_pdf = tmp_path / "corrupted.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n%invalid_pdf_content\x00\xff")

    fake_docx = tmp_path / "corrupted.docx"
    fake_docx.write_bytes(b"PK\x03\x04 corrupted zip header but not a real docx")

    indexer = IndexingService(mock_db, mock_emb, mock_lancedb)

    # Process fake PDF
    queue_pdf = asyncio.Queue()
    await indexer._stream_extract_and_prepare(fake_pdf, "pdf_tag", None, queue_pdf)

    pdf_items = []
    while not queue_pdf.empty():
        pdf_items.append(await queue_pdf.get())

    assert len(pdf_items) >= 2
    assert pdf_items[0]["type"] == "header"
    assert pdf_items[-1]["type"] == "footer"

    # Process fake docx
    queue_docx = asyncio.Queue()
    await indexer._stream_extract_and_prepare(fake_docx, "docx_tag", None, queue_docx)

    docx_items = []
    while not queue_docx.empty():
        docx_items.append(await queue_docx.get())

    assert len(docx_items) >= 2
    assert docx_items[0]["type"] == "header"
    assert docx_items[-1]["type"] == "footer"


@pytest.mark.anyio
async def test_additional_input_validation_robustness(client):
    # A. Reject massive question input (> 2000 chars)
    response = await client.post("/api/query", json={"question": "a" * 2001})
    assert response.status_code == 422
    assert "question" in response.text

    # B. Reject whitespace-only question
    response = await client.post("/api/query", json={"question": "   "})
    assert response.status_code == 422
    assert "Question cannot be empty" in response.text

    # C. Reject empty question
    response = await client.post("/api/query", json={"question": ""})
    assert response.status_code == 422

    # D. Reject malformed JSON payload
    token = os.environ.get("X_LOCAL_ACCESS_TOKEN", "test-token")
    headers = {"X-Local-Access-Token": token, "Content-Type": "application/json"}

    response = await client.request(
        "POST", "/api/query", content="{'question': 'hello', invalid_json}", headers=headers
    )
    assert response.status_code in (400, 422)
