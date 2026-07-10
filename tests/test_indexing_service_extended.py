import asyncio
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
