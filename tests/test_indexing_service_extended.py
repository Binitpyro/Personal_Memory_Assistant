from pathlib import Path
from types import SimpleNamespace

import pytest

from app.indexing import service as idx


class FakeEmb:
    async def embed_texts(self, texts, batch_size=None):
        return [[float(i + 1)] for i, _ in enumerate(texts)]


class FakeChroma:
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


class FakeDB:
    def __init__(self):
        self.files = {}
        self.next_file_id = 1
        self.file_chunks = {}
        self.profile_rows = []

    async def get_files_change_map(self, paths):
        return {p: ("same", "") for p in paths if p.endswith("same.txt")}

    async def get_file_by_path(self, path):
        if path in self.files:
            return {"id": self.files[path]}
        return None

    async def get_file_chunks(self, file_id):
        return [{"id": cid} for cid in self.file_chunks.get(file_id, [])]

    async def delete_file_chunks(self, file_id):
        self.file_chunks[file_id] = []

    async def insert_file(self, file_data):
        path = file_data["path"]
        if path in self.files:
            return self.files[path]
        fid = self.next_file_id
        self.next_file_id += 1
        self.files[path] = fid
        self.file_chunks.setdefault(fid, [])
        return fid

    async def insert_chunks_bulk(self, rows):
        if not rows:
            return []
        file_id = rows[0]["file_id"]
        current = self.file_chunks.setdefault(file_id, [])
        start_id = len(current) + 1
        new_ids = list(range(start_id, start_id + len(rows)))
        current.extend(new_ids)
        return new_ids

    async def commit(self):
        return None

    async def upsert_folder_profile(self, profile, *, auto_commit=True):
        self.profile_rows.append(profile)

    async def get_existing_file_ids(self, paths):
        return {p: fid for p, fid in self.files.items() if p in paths}


def _make_service():
    return idx.IndexingService(FakeDB(), FakeEmb(), FakeChroma())


def test_project_detection_and_overlap_resolution(tmp_path: Path):
    project = tmp_path / "proj"
    src = project / "src"
    src.mkdir(parents=True)
    pkg = project / "package.json"
    pkg.write_text("{}", encoding="utf-8")
    f = src / "main.js"
    f.write_text("console.log('x')", encoding="utf-8")

    files = [(pkg, "proj"), (f, "proj")]
    ptype, desc = idx._detect_project_type(files, project)
    assert ptype in {"React", "Node.js"}
    assert desc

    out = idx._resolve_folder_overlaps([str(project), str(src)])
    assert out == [project.resolve()]


def test_folder_profile_and_chunk_helpers(tmp_path: Path):
    folder = tmp_path / "game"
    folder.mkdir()
    py = folder / "a.py"
    py.write_text("print('a')", encoding="utf-8")
    md = folder / "README.md"
    md.write_text("hello", encoding="utf-8")

    profile = idx._build_folder_profile(folder, "game", [(py, "game"), (md, "game")])
    assert profile["folder_tag"] == "game"
    assert profile["file_count"] == 2
    assert "project_type" in profile

    chunks = [{"start_offset": 0, "end_offset": 10, "text_preview": "x", "_embedding": [1.0]}]
    rows = idx.IndexingService._build_chunk_rows(chunks, file_id=7)
    assert rows[0]["file_id"] == 7
    assert "_embedding" not in rows[0]


@pytest.mark.asyncio
async def test_collect_assign_embeddings_and_store_paths(tmp_path: Path):
    svc = _make_service()

    prepared = [
        {
            "path": tmp_path / "a.txt",
            "folder_tag": "T",
            "file_data": {"path": str(tmp_path / "a.txt"), "size": 1, "modified_at": "m", "type": ".txt"},
            "chunks": [{"text_preview": "c1"}, {"text_preview": "c2"}],
            "summary": "sum",
        }
    ]
    texts, text_map = svc._build_embedding_payload(prepared)
    assert texts == ["c1", "c2", "sum"]

    svc._assign_embeddings(prepared, text_map, [[1.0], [2.0], [3.0]])
    assert prepared[0]["chunks"][0]["_embedding"] == [1.0]
    assert prepared[0]["_summary_embedding"] == [3.0]

    await svc._store_prepared_items(prepared)
    assert svc.chroma_client.docs_batches
    assert svc.chroma_client.summaries


@pytest.mark.asyncio
async def test_extract_prepare_and_detect_changes(tmp_path: Path):
    svc = _make_service()
    svc.max_file_size = 10_000

    file_ok = tmp_path / "ok.txt"
    file_ok.write_text("Hello world. " * 30, encoding="utf-8")
    file_same = tmp_path / "same.txt"
    file_same.write_text("same", encoding="utf-8")
    file_missing = tmp_path / "gone.txt"

    def fake_extract_text(path: Path):
        return "alpha beta gamma " * 10

    svc._extract_text = fake_extract_text
    prepared = svc._extract_and_prepare(file_ok, "tmp")
    assert prepared is not None
    assert prepared["chunks"]

    detected, skipped, new_count, changed = await svc._detect_changes(
        [(file_ok, "tmp"), (file_same, "tmp"), (file_missing, "tmp")]
    )
    assert any(fp == file_ok for fp, _ in detected)
    assert skipped >= 1
    assert new_count + changed >= 1


@pytest.mark.asyncio
async def test_scan_index_file_and_profiles(monkeypatch, tmp_path: Path):
    svc = _make_service()

    folder = tmp_path / "proj"
    folder.mkdir()
    file1 = folder / "one.txt"
    file1.write_text("A short document. More text.", encoding="utf-8")

    fake_scan_result = SimpleNamespace(method="scandir", duration_ms=1.5, files=[file1, file1])
    monkeypatch.setattr(idx, "fast_scan", lambda _path, _exts: fake_scan_result)

    all_files, method, duration = svc._scan_all_folders([folder])
    assert len(all_files) == 1
    assert method == "scandir"
    assert duration == 1.5

    await svc.index_file(file1, "proj")
    assert svc.chroma_client.docs_batches

    await svc._generate_folder_profiles(all_files, [folder])
    assert svc.db.profile_rows
    assert svc.chroma_client.summaries


def test_extract_json_csv_stub_and_chunking(tmp_path: Path):
    svc = _make_service()
    svc.chunk_size = 60
    svc.chunk_overlap = 10

    strict_json = tmp_path / "a.json"
    strict_json.write_text('{"x": 1}', encoding="utf-8")
    assert '"x": 1' in svc._extract_json(strict_json)

    trailing_json = tmp_path / "b.json"
    trailing_json.write_text('{"x": 1,}', encoding="utf-8")
    assert '"x": 1' in svc._extract_json(trailing_json)

    jsonl = tmp_path / "c.json"
    jsonl.write_text('{"a":1}\n{"b":2}', encoding="utf-8")
    out = svc._extract_json(jsonl)
    assert '"a": 1' in out and '"b": 2' in out

    csvp = tmp_path / "d.csv"
    csvp.write_text("h1,h2\n1,2", encoding="utf-8")
    assert "h1, h2" in svc._extract_csv(csvp)

    umap = tmp_path / "Map.umap"
    stub = svc._extract_unreal_asset_stub(umap)
    assert "Unreal Engine binary map" in stub

    text = "# Title\n\nSentence one. Sentence two.\n## Sub\n\nMore content."
    md_chunks = svc._create_chunks(text, file_path=str(tmp_path / "doc.md"))
    assert md_chunks
    assert all("[MD: doc.md]" in c["text_preview"] for c in md_chunks)

    plain_chunks = svc._split_text("A. B. C. D." * 20, "", 0)
    assert plain_chunks
