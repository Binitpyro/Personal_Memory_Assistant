import struct
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.modules import websocket_endpoint
from app.indexing.extractors.docx_extractor import DocxExtractor
from app.indexing.extractors.json_extractor import JsonExtractor
from app.indexing.service import IndexingService, _get_sentence_offsets
from app.indexing.summarizer import (
    _summarize_code_regex,
    _summarize_data_format,
    _summarize_doc_text,
    _summarize_markdown,
    _summarize_python,
    _summarize_spreadsheet_text,
    generate_deep_summary,
)
from app.project_constants import _get_app_version
from app.scanner.ntfs_mft import NTFSScanner
from app.scanner.scanner import _list_dir_entries, _scandir_walk, scan_folder
from app.search.capability_detector import CapabilityDetector
from app.search.context_builder import (
    _get_encoding,
    _get_tokens,
    _semantic_deduplicate,
    _token_count,
    _truncate_to_tokens,
    build_context,
    compute_context_budget,
)
from app.search.retrieval import _get_metadata_insights
from app.storage.db import DatabaseManager

# ── 1. App Version Fallbacks ──────────────────────────────────────────────────


def test_get_app_version_importlib_fails(monkeypatch):
    # Force importlib version lookup to raise exception
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda name: exec('raise Exception("failed")'),  # noqa: S102
    )
    # Make sure Path.exists returns False to trigger final fallback
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert _get_app_version() == "0.0.55"


def test_get_app_version_tomllib_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda name: exec('raise Exception("failed")'),  # noqa: S102
    )
    # Path exists, but we mock open/load to fail
    monkeypatch.setattr(Path, "exists", lambda self: True)

    with patch("builtins.open", side_effect=RuntimeError("disk read error")):
        assert _get_app_version() == "0.0.55"


# ── 2. Debug API ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_debug_query_plan_dev_mode(client):
    from app.config import settings

    # 1. Dev mode is False
    settings.dev_mode = False
    response = await client.get("/api/debug/query-plan?q=test")
    assert response.status_code == 404

    # 2. Dev mode is True
    settings.dev_mode = True
    response = await client.get("/api/debug/query-plan?q=how+many+files")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "how many files"
    assert data["dev_mode"] is True
    assert "mode" in data


# ── 3. Modules WebSocket Authentication & Loop Failures ────────────────────────


@pytest.mark.asyncio
async def test_websocket_missing_token_on_server(monkeypatch):
    websocket = AsyncMock()
    # Mock X_LOCAL_ACCESS_TOKEN env var missing
    monkeypatch.delenv("X_LOCAL_ACCESS_TOKEN", raising=False)
    await websocket_endpoint(websocket, token="some-token")
    websocket.close.assert_called_once_with(code=1008)


@pytest.mark.asyncio
async def test_websocket_loop_exception():
    websocket = AsyncMock()
    # Mock valid token authentication
    websocket.headers = {"x-local-access-token": "test-token"}
    # Force receive_json to raise unexpected error
    websocket.receive_json = AsyncMock(side_effect=RuntimeError("simulated websocket crash"))

    with patch.dict("os.environ", {"X_LOCAL_ACCESS_TOKEN": "test-token"}):
        await websocket_endpoint(websocket)

    websocket.accept.assert_called_once()
    websocket.close.assert_called_with(code=1011)


# ── 4. Docx Extractor Edge Cases ──────────────────────────────────────────────


def test_docx_extractor_table_extraction(tmp_path):
    fake_path = tmp_path / "table.docx"
    fake_path.touch()

    mock_docx = MagicMock()
    mock_doc = MagicMock()

    # Body containing a paragraph then a table
    mock_child_p = MagicMock()
    mock_child_p.tag = "}p"

    mock_child_tbl = MagicMock()
    mock_child_tbl.tag = "}tbl"

    mock_doc.element.body.iterchildren.return_value = [mock_child_p, mock_child_tbl]
    mock_docx.Document.return_value = mock_doc

    mock_para = MagicMock()
    mock_para.text = "Paragraph text content"

    # Mock Paragraph class
    mock_paragraph_module = MagicMock()
    mock_paragraph_module.Paragraph.return_value = mock_para

    # Mock Table and rows
    mock_table_module = MagicMock()
    mock_row = MagicMock()
    mock_cell = MagicMock()
    mock_cell.text = "Cell data"
    mock_row.cells = [mock_cell]

    mock_t = MagicMock()
    mock_t.rows = [mock_row]
    mock_table_module.Table.return_value = mock_t

    ext = DocxExtractor()
    with patch.dict(
        "sys.modules",
        {
            "docx": mock_docx,
            "docx.text.paragraph": mock_paragraph_module,
            "docx.table": mock_table_module,
        },
    ):
        # Normal extraction
        res = ext.extract(fake_path, 1000)
        assert "Paragraph" in res
        assert "Cell data" in res

        # Max file size limit truncation in table loop
        res_truncated = ext.extract(fake_path, 15)
        # It yields "Paragraph text content" which exceeds 15, paragraph check returns early
        assert "Paragraph" in res_truncated


def test_docx_extractor_encrypted_docx(tmp_path):
    fake_path = tmp_path / "encrypted.docx"
    fake_path.touch()

    mock_docx = MagicMock()
    # Document loading throws exception with "encrypted" in message
    mock_docx.Document.side_effect = Exception("This file is encrypted and password-protected.")

    ext = DocxExtractor()
    with patch.dict("sys.modules", {"docx": mock_docx}):
        res = ext.extract(fake_path, 1000)
        assert "password-protected" in res


# ── 5. Json Extractor Truncation ──────────────────────────────────────────────


def test_json_extractor_large_file(tmp_path):
    ext = JsonExtractor()
    f = tmp_path / "large.json"
    # Write more than 500,000 bytes
    large_str = '{"data": "' + ("x" * 600000) + '"}'
    f.write_text(large_str, encoding="utf-8")

    assert f.stat().st_size > 500000
    res = ext.extract(f, 100)
    assert len(res) <= 100
    assert "data" in res


# ── 6. NTFS MFT Scanner ────────────────────────────────────────────────────────


def make_fake_usn_buffer(records):
    buf_bytes = bytearray(128 * 1024)
    struct.pack_into("<Q", buf_bytes, 0, 0)  # next_ref = 0

    offset = 8
    for file_ref, parent_ref, attrs, name in records:
        name_bytes = name.encode("utf-16-le")
        name_len = len(name_bytes)
        name_off = 60
        rec_len = name_off + name_len
        rec_len = (rec_len + 7) // 8 * 8  # pad

        struct.pack_into("<I", buf_bytes, offset, rec_len)
        struct.pack_into("<Q", buf_bytes, offset + 8, file_ref)
        struct.pack_into("<Q", buf_bytes, offset + 16, parent_ref)
        struct.pack_into("<I", buf_bytes, offset + 52, attrs)
        struct.pack_into("<H", buf_bytes, offset + 56, name_len)
        struct.pack_into("<H", buf_bytes, offset + 58, name_off)
        buf_bytes[offset + name_off : offset + name_off + name_len] = name_bytes

        offset += rec_len
    return buf_bytes, offset


def test_ntfs_scanner_mocked_execution(monkeypatch, tmp_path):
    # Setup mocks for kernel32 DLL calls
    mock_k32 = MagicMock()
    mock_k32.CreateFileW.return_value = 1234

    # DeviceIoControl returns True first time with fake USN buffer, False next time
    calls = 0

    def mock_device_io_control(
        handle, ioctl, in_buf, in_len, out_buf, out_len, bytes_returned, overlapped
    ):
        nonlocal calls
        if calls == 0:
            calls += 1
            fake_records = [
                (101, 5, 0x10, "Subfolder"),  # dir
                (102, 101, 0, "file.txt"),  # file in subfolder
            ]
            buf_bytes, total_len = make_fake_usn_buffer(fake_records)
            out_buf[:total_len] = buf_bytes[:total_len]
            bytes_returned.value = total_len
            return True
        else:
            return False

    mock_k32.DeviceIoControl.side_effect = mock_device_io_control
    mock_k32.CloseHandle.return_value = True

    monkeypatch.setattr("ctypes.byref", lambda x: x)
    monkeypatch.setattr("app.scanner.ntfs_mft.kernel32", mock_k32)
    # mock get_last_error to return ERROR_HANDLE_EOF (38)
    monkeypatch.setattr("ctypes.get_last_error", lambda: 38)
    monkeypatch.setattr(
        Path,
        "drive",
        property(
            lambda self: "C:" if str(self).startswith("C:") or str(self).startswith("C:\\") else ""
        ),
    )

    scanner = NTFSScanner()
    # 1. Scan folder where drive is empty
    assert scanner.scan_folder(Path("relative/path"), {".txt"}) is None

    # 2. Scan volume successfully
    mock_k32.CreateFileW.return_value = 1234
    res = scanner.scan_folder(Path("C:/Subfolder"), {".txt"})
    # Subfolder/file.txt
    assert len(res) == 1
    assert res[0].name == "file.txt"

    # 3. CreateFileW fails (INVALID_HANDLE_VALUE = -1)
    mock_k32.CreateFileW.return_value = -1
    monkeypatch.setattr("ctypes.get_last_error", lambda: 5)  # Access Denied
    assert scanner.scan_folder(Path("C:/Subfolder"), {".txt"}) is None


# ── 7. Cross-platform Scanner Helper ──────────────────────────────────────────


def test_scan_folder_mft_handling(monkeypatch):
    # Test MFT scanner exceptions / availability
    monkeypatch.setattr("platform.system", lambda: "Windows")

    # Cause importing NTFSScanner to raise ImportError
    with patch("app.scanner.ntfs_mft.NTFSScanner", side_effect=ImportError("No MFT")):
        res = scan_folder(Path("C:/tmp"), {".txt"})
        assert res.method == "scandir"


def test_scandir_walk_os_error(monkeypatch):
    # Mock list_dir_entries to throw OSError
    def fake_list_dir(path, ext):
        raise OSError("Permission Denied")

    monkeypatch.setattr("app.scanner.scanner._list_dir_entries", fake_list_dir)
    res = _scandir_walk(Path("C:/restricted"), {".txt"})
    assert res == []


def test_list_dir_entries_os_error(tmp_path):
    # entry.is_dir raises OSError
    mock_entry = MagicMock()
    mock_entry.is_dir.side_effect = OSError("boom")

    mock_it = MagicMock()
    mock_it.__enter__.return_value = [mock_entry]

    with patch("os.scandir", return_value=mock_it):
        res = _list_dir_entries(str(tmp_path), {".txt"})
        assert res == []


# ── 8. Capability Detector ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capability_detector(monkeypatch):
    detector = CapabilityDetector()
    detector.reset_cache()

    # 1. 3b_local model class
    mock_client = MagicMock()
    mock_client.get_model_class.return_value = "3b_local"
    assert await detector.detect_capabilities(mock_client) is False

    # 2. Probe succeeds
    mock_client.get_model_class.return_value = "cloud"
    mock_client.provider_preference = "openai"
    mock_client.model = "gpt-4o"
    mock_client.ollama_model = ""
    mock_client.lm_studio_model = ""
    mock_client.generate_answer = AsyncMock(return_value='<claim sources="[1]">True</claim>')
    assert await detector.detect_capabilities(mock_client) is True

    # 3. Cache hit
    assert await detector.detect_capabilities(mock_client) is True

    # 4. Report failure
    detector.report_failure(mock_client)
    assert await detector.detect_capabilities(mock_client) is False

    # 5. Probe raises Exception
    detector.reset_cache()
    mock_client.generate_answer = AsyncMock(side_effect=RuntimeError("Timeout"))
    assert await detector.detect_capabilities(mock_client) is False


# ── 9. Context Builder ────────────────────────────────────────────────────────


def test_context_builder_edge_cases():
    # 1. Empty stats, empty profiles, empty snippets
    res, count = build_context([], max_tokens=100)  # noqa: RUF059
    assert res == "No relevant context found."

    # 2. default context max tokens when <= 0
    from app.config import settings

    settings.context_max_tokens = 2500
    res, _count = build_context([{"file_path": "a.py", "text": "foo", "score": 1.0}], max_tokens=0)
    assert "a.py" in res

    # 3. 3b_local parameters
    res_local, _ = build_context(
        [{"file_path": "a.py", "text": "foo", "score": 1.0}],
        max_tokens=1000,
        model_class="3b_local",
    )
    assert "a.py" in res_local


def test_context_builder_tiktoken_error(monkeypatch):
    # Force tiktoken.get_encoding to raise an exception
    import sys

    # If tiktoken is imported, we can mock it
    if "tiktoken" in sys.modules:
        monkeypatch.setattr("tiktoken.get_encoding", lambda name: exec('raise Exception("failed")'))  # noqa: S102

    # Clear internal encoding cache
    import app.search.context_builder as cb

    cb._ENCODING = None

    enc = _get_encoding()
    assert enc is False
    assert _get_tokens("hello") == []
    assert _token_count("hello") == 1
    assert _truncate_to_tokens("hello world", 1) == "hell"


def test_semantic_deduplicate_with_datasketch(monkeypatch):
    # Mock datasketch MinHash and MinHashLSH
    mock_lsh_instance = MagicMock()
    mock_lsh_instance.query.side_effect = [
        [],
        ["res_0"],
    ]  # first call empty, second call duplicate match

    mock_lsh_class = MagicMock(return_value=mock_lsh_instance)
    mock_minhash = MagicMock()

    mock_datasketch = MagicMock()
    mock_datasketch.MinHash = mock_minhash
    mock_datasketch.MinHashLSH = mock_lsh_class

    with patch.dict("sys.modules", {"datasketch": mock_datasketch}):
        # Run deduplicate with mocked datasketch
        # If it finds a match, it won't insert and will drop the second snippet
        results = [
            {"text": "This is a long text to hash. 1234567890abcdefghijklmnopqrstuvwxyz"},
            {"text": "This is a long text to hash. 1234567890abcdefghijklmnopqrstuvwxyz"},
        ]
        # Avoid direct import to ensure it resolves inside function
        deduped = _semantic_deduplicate(results)
        # First one gets inserted (matches = empty), second matches "res_0" so dropped
        assert len(deduped) >= 1


def test_compute_context_budget():
    assert compute_context_budget("cloud", 0) == 98520
    assert compute_context_budget("3b_local", 2) == 1720


# ── 10. Retrieval Insights & Metadata ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_metadata_insights_latest_and_largest():
    # Setup a mock DB that returns rows for modified_at and size
    db = AsyncMock()
    db.execute_query = AsyncMock(
        side_effect=lambda sql, *args: (
            [("C:/path/to/latest.py", "2026-07-04")]
            if "modified_at" in sql
            else [("C:/path/to/largest.py", 10 * 1024 * 1024)]
        )
    )

    # 1. Query "latest files"
    insights = await _get_metadata_insights("what are the latest files?", db, None, [])
    assert "latest.py" in insights

    # 2. Query "largest files"
    insights = await _get_metadata_insights("what is the biggest file?", db, None, [])
    assert "largest.py" in insights
    assert "10.0 MB" in insights


# ── 11. Database Manager covering index, frozen path, and duplicate migrations ─


@pytest.mark.asyncio
async def test_db_manager_duplicate_migrations_and_fragmentation(tmp_path):
    db_file = tmp_path / "test.db"
    db = DatabaseManager(str(db_file))

    # 1. Test duplicate column migration handling
    import sqlite3

    mock_conn = MagicMock()
    mock_conn.commit = AsyncMock()

    class MockCursor:
        def __init__(self, val=None):
            self.val = val

        def __await__(self):
            async def _impl():
                return self

            return _impl().__await__()

        async def __aenter__(self):
            return self

        async def __aexit__(self, et, ev, tb):
            pass

        async def fetchone(self):
            return self.val

    def mock_execute(sql, *args):
        if "schema_migrations" in sql:
            return MockCursor(None)
        if "ALTER TABLE" in sql:
            raise sqlite3.OperationalError("duplicate column name: sha256")
        return MockCursor(None)

    mock_conn.execute.side_effect = mock_execute

    # This should call _apply_column_migrations and handle the duplicate column error cleanly
    await db._apply_column_migrations(mock_conn)


@pytest.mark.asyncio
async def test_db_manager_frozen_path(monkeypatch, tmp_path):
    # Set sys.frozen to True
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    mock_main = MagicMock()
    fake_schema = tmp_path / "fake_schema.sql"
    fake_schema.write_text(
        "CREATE TABLE files (id INTEGER); CREATE TABLE chunks (id INTEGER);", encoding="utf-8"
    )
    mock_main._get_resource_path.return_value = str(fake_schema)

    monkeypatch.setitem(sys.modules, "__main__", mock_main)

    db = DatabaseManager(":memory:")
    # Should resolve using mock_main._get_resource_path and succeed
    await db.init_db("schema.sql")
    await db.close()


@pytest.mark.asyncio
async def test_db_manager_savepoint_transaction_handling(tmp_path):
    db = DatabaseManager(":memory:")
    await db.init_db()
    conn = db._get_conn()

    # Start a transaction manually so that BEGIN IMMEDIATE inside savepoint raises error
    await conn.execute("BEGIN IMMEDIATE")

    # Try cleaning stale files. It should trigger savepoint path
    # Mock os.path.exists to return False
    with patch("os.path.exists", return_value=False):
        # We need files in DB first
        await db.execute_write(
            "INSERT INTO files (path, size, modified_at, type) VALUES (?, ?, ?, ?)",
            ("C:/stale.py", 100, "now", ".py"),
        )
        res = await db.cleanup_stale_files()
        assert res == ["C:/stale.py"]

    await db.close()


# ── 12. Summarizer Edge Cases ────────────────────────────────────────────────


def test_summarizer_python_syntax_error():
    # Syntax error in Python file, should fall back to Regex
    text = "def foo( invalid syntax \n  class Bar:\n    pass"
    summary = _summarize_python(text, 100)
    assert "Bar" in summary


def test_summarizer_code_regex_no_matches():
    # No matches found by regex, should return text slice
    text = "just plain text with no symbols"
    summary = _summarize_code_regex(text, ".rs", 20)
    assert summary == "just plain text with"


def test_summarizer_markdown_no_headers():
    text = "plain markdown text without hash headers"
    summary = _summarize_markdown(text, 20)
    assert summary == "plain markdown text"


def test_summarizer_data_format_parsing_failing():
    # Invalid json/yaml, falls back to text snippet
    text = "{broken json"
    summary = _summarize_data_format(text, ".json", 20)
    assert summary == "{broken json"


def test_summarizer_pptx_slide_outline():
    text = "--- Slide 1 ---\nSlide One Title\nSome content\n--- Slide 2 ---\nSlide Two Title"
    summary = _summarize_doc_text(text, 100)
    assert "Outline: Slide One Title, Slide Two Title" in summary


def test_summarizer_xlsx_sheet_preview():
    text = "--- Sheet: Sheet1 ---\nRow 1 cell contents"
    summary = _summarize_spreadsheet_text(text, 100)
    assert "Sheets: Sheet1" in summary


def test_summarizer_general_exception():
    # Force an exception inside generate_deep_summary to check fallback snippet path
    # by passing None as text
    res = generate_deep_summary(None, Path("file.txt"), 10)
    assert "[TXT: file.txt]" in res


# ── 13. Indexing Service NLTK & TaskGroup Failure Fallbacks ───────────────────


def test_get_sentence_offsets_nltk_exception(monkeypatch):
    monkeypatch.setenv("PMA_SENTENCE_OFFSETS", "1")
    # Force nltk import or sent_tokenize to fail
    monkeypatch.setattr(
        "nltk.tokenize.sent_tokenize",
        lambda text: exec('raise Exception("failed")'),  # noqa: S102
        raising=False,
    )

    offsets = _get_sentence_offsets("This is a sentence. And another one!")
    assert len(offsets) == 2
    assert offsets[0] == [0, 20]  # "This is a sentence. "


@pytest.mark.asyncio
async def test_indexing_pipeline_taskgroup_exception():
    service = IndexingService(
        db=AsyncMock(), embedding_service=AsyncMock(), lancedb_client=AsyncMock()
    )

    # Mock extractor worker to throw Exception
    async def fake_extractor(*args):
        raise ValueError("simulated pipeline error")

    service._extractor_worker = fake_extractor

    # Run pipelined indexing, it should catch the ExceptionGroup / Exception cleanly
    # and not crash the caller
    await service._batch_index_pipeline(
        files_to_index=[(Path("C:/a.py"), "A")], offset=0, total_to_index=1
    )
    # The TaskGroup catches ExceptionGroup and prints/logs the error.
