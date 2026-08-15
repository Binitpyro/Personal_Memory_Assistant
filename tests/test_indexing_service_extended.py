import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.indexing import service as idx
from app.indexing.folder_profiler import (
    build_folder_profile as _build_folder_profile,
)
from app.indexing.folder_profiler import (
    detect_project_type as _detect_project_type,
)
from app.indexing.folder_profiler import (
    resolve_folder_overlaps as _resolve_folder_overlaps,
)


class FakeEmb:
    async def embed_texts(self, texts, batch_size=None, progress_callback=None):
        if progress_callback:
            progress_callback(1, 1)
        import numpy as np

        return np.array([[float(i + 1)] for i, _ in enumerate(texts)], dtype=np.float32)


class FakeLanceDB:
    def __init__(self):
        self.deleted_ids = []
        self.docs_batches = []
        self.summaries = []

    async def delete_documents(self, ids):
        self.deleted_ids.append(ids)

    async def add_documents(self, ids, embs, metas):
        self.docs_batches.append((ids, embs, metas))

    async def add_summary(self, doc_id, embedding, metadata):
        self.summaries.append((doc_id, embedding, metadata))

    async def add_summaries_batch(self, items):
        self.summaries.extend(items)

    def get_max_id(self, table_name="pma_chunks"):
        return 0


class FakeDB:
    def __init__(self):
        self.files = {}
        self.next_file_id = 1
        self.file_chunks = {}
        self.profile_rows = []
        self._in_external_transaction = False

    async def begin_transaction(self):
        self._in_external_transaction = True

    async def commit_transaction(self):
        self._in_external_transaction = False

    async def rollback_transaction(self):
        self._in_external_transaction = False

    async def get_files_change_map(self, paths):
        return {p: ("same", "") for p in paths if p.endswith("same.txt")}

    async def get_file_by_path(self, path):
        if path in self.files:
            return {"id": self.files[path]}
        return None

    async def get_file_chunks(self, file_id):
        return [{"id": cid} for cid in self.file_chunks.get(file_id, [])]

    async def delete_file_chunks(self, file_id, *, auto_commit=True):
        self.file_chunks[file_id] = []

    async def insert_file(self, file_data, *, auto_commit=True):
        path = file_data["path"]
        if path in self.files:
            return self.files[path]
        fid = self.next_file_id
        self.next_file_id += 1
        self.files[path] = fid
        self.file_chunks.setdefault(fid, [])
        return fid

    async def batch_insert_files(self, files_data, auto_commit=True):
        ids = []
        for fd in files_data:
            ids.append(await self.insert_file(fd, auto_commit=auto_commit))
        return ids

    async def insert_chunks_bulk(self, rows, auto_commit=True):
        if not rows:
            return []
        file_id = rows[0]["file_id"]
        current = self.file_chunks.setdefault(file_id, [])
        start_id = len(current) + 1
        new_ids = list(range(start_id, start_id + len(rows)))
        current.extend(new_ids)
        return new_ids

    async def insert_chunk_embeddings_bulk(self, rows, auto_commit=True):
        return None

    async def insert_kg_nodes_bulk(self, data, auto_commit=True):
        return None

    async def insert_kg_edges_bulk(self, data, auto_commit=True):
        return None

    async def commit(self):
        self._in_external_transaction = False
        return None

    async def upsert_folder_profile(self, profile, *, auto_commit=True):
        self.profile_rows.append(profile)

    async def get_existing_file_ids(self, paths):
        return {p: fid for p, fid in self.files.items() if p in paths}

    async def execute_write(self, q, p=None):
        return None

    async def wal_checkpoint(self):
        return None


def _make_service():
    return idx.IndexingService(FakeDB(), FakeEmb(), FakeLanceDB())


async def test_index_folders_always_releases_the_progress_flag(monkeypatch, tmp_path: Path):
    """An exception mid-run must not leave progress.status == "running".

    The OCR drain loop refuses to claim work while it is anything else
    (app/ocr/manager.py) and index_ocr_pages raises on it outright, so a leaked
    "running" flag starves OCR indefinitely - index_folders' caller is an
    unhandled background task, and the watcher that would eventually clear it
    is off by default.
    """
    service = _make_service()

    def _boom(*_a, **_kw):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(service, "_scan_all_folders", _boom)

    idx.progress.status = "idle"
    with pytest.raises(RuntimeError, match="scan exploded"):
        await service.index_folders([str(tmp_path)])

    assert idx.progress.status == "idle"
    assert not idx.indexing_lock.locked()


def test_project_detection_and_overlap_resolution(tmp_path: Path):
    project = tmp_path / "proj"
    src = project / "src"
    src.mkdir(parents=True)
    pkg = project / "package.json"
    pkg.write_text("{}", encoding="utf-8")
    f = src / "main.js"
    f.write_text("console.log('x')", encoding="utf-8")
    files = [(pkg, "proj"), (f, "proj")]
    ptype, desc = _detect_project_type(files, project)
    assert ptype in {"React", "Node.js", "node"}
    assert desc
    out = _resolve_folder_overlaps([str(project), str(src)])
    assert out == [project.resolve()]


def test_folder_profile_and_chunk_helpers(tmp_path: Path):
    folder = tmp_path / "game"
    folder.mkdir()
    py = folder / "a.py"
    py.write_text("print('a')", encoding="utf-8")
    md = folder / "README.md"
    md.write_text("hello", encoding="utf-8")
    profile = _build_folder_profile(folder, "game", [(py, "game"), (md, "game")])
    assert profile["folder_tag"] == "game"
    assert profile["file_count"] == 2
    assert "project_type" in profile


@pytest.mark.asyncio
async def test_stream_extract_and_prepare(tmp_path: Path):
    svc = _make_service()
    file_ok = tmp_path / "ok.txt"
    file_ok.write_text("Hello world. " * 30, encoding="utf-8")
    q = asyncio.Queue()
    await svc._stream_extract_and_prepare(file_ok, "tmp", None, q)

    header = await q.get()
    assert header["type"] == "header"
    chunk = await q.get()
    assert chunk["type"] == "chunk"
    footer = await q.get()
    assert footer["type"] == "footer"
    assert "Hello" in chunk["chunk"]["text_preview"]


@pytest.mark.asyncio
async def test_stream_pump_unblocks_on_cancellation(monkeypatch, tmp_path: Path):
    svc = _make_service()
    waiting_file = tmp_path / "waiting.wait"
    waiting_file.write_text("waiting", encoding="utf-8")
    started = threading.Event()

    class BlockingExtractor:
        def can_handle(self, path: Path) -> bool:
            return path.suffix == ".wait"

        def extract_stream(self, path: Path, max_file_size: int):
            started.set()
            while not idx.progress.is_cancelled:
                time.sleep(0.01)
            yield "This fragment is discarded after cancellation."

    monkeypatch.setattr(idx, "EXTRACTORS", [BlockingExtractor()])
    monkeypatch.setattr(svc, "_generate_summary", lambda _text, _path: "")
    idx.progress.reset(0)
    queue = asyncio.Queue()
    task = asyncio.create_task(svc._stream_extract_and_prepare(waiting_file, "tmp", None, queue))

    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
        svc.cancel_indexing()
        await asyncio.wait_for(task, timeout=2)
    finally:
        idx.progress.reset(0)

    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    assert [item["type"] for item in items] == ["header", "footer"]
    assert items[-1]["sha256"] == "CANCELLED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lancedb_mode", "sqlite_embedding_backup", "expects_backup"),
    [
        ("portable", False, False),
        ("split_brain", False, True),
        ("portable", True, True),
    ],
)
async def test_flush_pending_chunks_releases_source_payloads(
    monkeypatch,
    tmp_path: Path,
    lancedb_mode: str,
    sqlite_embedding_backup: bool,
    expects_backup: bool,
):
    import numpy as np

    svc = _make_service()
    embedding_writes = []

    async def record_embedding_write(rows, auto_commit=True):
        embedding_writes.append((rows, auto_commit))

    svc.db.insert_chunk_embeddings_bulk = record_embedding_write
    monkeypatch.setattr(idx.settings, "lancedb_mode", lancedb_mode)
    monkeypatch.setattr(idx.settings, "sqlite_embedding_backup", sqlite_embedding_backup)

    path = tmp_path / "payload.py"
    embedding = np.array([0.25, 0.5], dtype=np.float32)
    chunk = {
        "start_offset": 0,
        "end_offset": 10,
        "text_preview": "large source payload",
        "_embedding": embedding,
        "kg_nodes": [{"id": "node-1", "label": "Node", "start_line": 1, "end_line": 1}],
        "kg_edges": [{"src_id": "node-1", "dst_id": "node-2", "rel_type": "uses"}],
    }
    item = {"path": path, "file_id": 1, "chunk": chunk}
    active_files = {str(path.absolute()): {"data": {"folder_tag": "tmp"}}}

    _ids, l_embs, _metas = await svc._flush_pending_chunks_sqlite([item], active_files)

    assert l_embs[0] is embedding
    assert "_embedding" not in chunk
    assert "kg_nodes" not in chunk
    assert "kg_edges" not in chunk
    assert "text_preview" not in chunk
    if expects_backup:
        assert len(embedding_writes) == 1
        rows, auto_commit = embedding_writes[0]
        assert auto_commit is False
        assert rows == [(1, np.array(embedding, dtype=np.float16).tobytes())]
    else:
        assert embedding_writes == []


@pytest.mark.asyncio
async def test_scan_index_file_and_profiles(monkeypatch, tmp_path: Path):
    svc = _make_service()
    folder = tmp_path / "proj"
    folder.mkdir()
    file1 = folder / "one.txt"
    file1.write_text("A short document.", encoding="utf-8")
    fake_scan_result = SimpleNamespace(method="scandir", duration_ms=1.5, files=[file1, file1])
    monkeypatch.setattr(idx, "fast_scan", lambda _path, _exts: fake_scan_result)
    monkeypatch.setattr(idx, "RUST_CORE_AVAILABLE", False)

    all_files, _method, _duration = svc._scan_all_folders([folder])
    assert len(all_files) == 1
    await svc._batch_index_pipeline([(file1, "proj")])
    assert svc.lancedb_client.docs_batches


def test_extract_monolithic_and_chunking(tmp_path: Path):
    svc = _make_service()
    strict_json = tmp_path / "a.json"
    strict_json.write_text('{"x": 1}', encoding="utf-8")
    text = svc._extract_text_monolithic(strict_json)
    assert '{"x": 1}' in text or '"x": 1' in text

    md_chunks = svc._create_chunks("# Title\nBody", file_path="doc.md")
    assert md_chunks
    assert "[MD: doc.md]" in md_chunks[0]["text_preview"]

    plain_chunks = svc._create_chunks("A. B. C. D." * 10, file_path="test.txt")
    assert plain_chunks
    assert "A. B." in plain_chunks[0]["text_preview"]


@pytest.mark.asyncio
async def test_cancellation_awareness(tmp_path: Path):
    """Test that the batch indexing pipeline respects task cancellation and halts OS threads."""
    svc = _make_service()

    folder = tmp_path / "cancel_test"
    folder.mkdir()
    f = folder / "file_0.txt"
    # A large file so extraction takes time
    f.write_text("Hello world. " * 100000, encoding="utf-8")

    task = asyncio.create_task(svc._batch_index_pipeline([(f, "cancel_test")]))

    # Wait until it enters ingest mode
    await asyncio.sleep(0.05)

    # Cancel the task using the correct API
    svc.cancel_indexing()

    await task

    # Assert that the pipeline's progress object caught the cancellation
    from app.indexing.service import progress

    assert progress.is_cancelled


@pytest.mark.asyncio
async def test_update_path_deletes_old_chunks(tmp_path: Path):
    """Test that modifying a file correctly drops old chunks from the real FTS index."""
    from app.storage.db import DatabaseManager

    db_path = tmp_path / "test.db"
    db = DatabaseManager(str(db_path))
    await db.init_db()

    # Insert a file
    f_data = {
        "path": "doc.txt",
        "size": 10,
        "modified_at": "2023-01-01",
        "type": ".txt",
        "folder_tag": "test",
        "summary": "",
        "sha256": "",
    }
    file_id = await db.insert_file(f_data)

    # Enter ingest mode, bulk insert chunks, exit ingest mode
    await db.enter_ingest_mode()
    await db.insert_chunks_bulk(
        [
            {
                "file_id": file_id,
                "start_offset": 0,
                "end_offset": 5,
                "text_preview": "old content that we want to search",
                "sentence_offsets": "[]",
                "segmenter_version": "v1",
            }
        ]
    )
    await db.exit_ingest_mode()

    # Verify FTS index has it
    conn = db._get_conn()
    async with conn.execute("SELECT count(*) FROM chunk_fts WHERE chunk_fts MATCH 'content'") as c:
        assert (await c.fetchone())[0] == 1

    # Now simulate the update path: enter ingest, delete chunks, exit ingest
    await db.enter_ingest_mode()
    await db.delete_file_chunks(file_id)
    await db.exit_ingest_mode()

    # Verify FTS index dropped it
    async with conn.execute("SELECT count(*) FROM chunk_fts WHERE chunk_fts MATCH 'content'") as c:
        assert (await c.fetchone())[0] == 0

    await db.close()
