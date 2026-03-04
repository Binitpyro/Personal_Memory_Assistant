import json
from pathlib import Path

import pytest

from app.insights.service import InsightsService
from app.main import (
    IndexRequest,
    QueryRequest,
    UnrealImportRequest,
    cleanup_stale,
    export_index,
    get_files_tree,
    get_system_info,
    query_history,
)
from app.search.context_builder import _format_file_stats, build_context


class FakeDB:
    def __init__(self):
        self.raise_on_files = False
        self.raise_on_history = False
        self.raise_on_cleanup = False
        self.raise_on_export = False

    async def get_all_files(self):
        if self.raise_on_files:
            raise RuntimeError("db error")
        return [
            {
                "folder_tag": "ProjectA",
                "path": "C:/proj/a.py",
                "size": 100,
                "type": ".py",
                "usage_count": 2,
            },
            {
                "folder_tag": "",
                "path": "C:/proj/readme.md",
                "size": 50,
                "type": ".md",
                "usage_count": 0,
            },
        ]

    async def get_query_history(self, limit=20):
        if self.raise_on_history:
            raise RuntimeError("history error")
        return [{"question": "q", "answer": "a", "limit": limit}]

    async def cleanup_stale_files(self):
        if self.raise_on_cleanup:
            raise RuntimeError("cleanup error")
        return ["C:/old/file.txt"]

    async def get_counts(self):
        if self.raise_on_export:
            raise RuntimeError("count error")
        return (2, 10)


@pytest.mark.asyncio
async def test_get_files_tree_groups_and_totals():
    db = FakeDB()
    result = await get_files_tree(db=db)
    assert result["total_files"] == 2
    assert result["total_size"] == 150
    assert "ProjectA" in result["folders"]
    assert "Unknown" in result["folders"]


@pytest.mark.asyncio
async def test_get_files_tree_db_failure_returns_empty():
    from app.main import _file_tree_cache
    _file_tree_cache["data"] = None  # Clear cache from prior test
    db = FakeDB()
    db.raise_on_files = True
    result = await get_files_tree(db=db)
    assert result == {"folders": {}, "total_files": 0, "total_size": 0}


@pytest.mark.asyncio
async def test_query_history_success_and_error():
    db = FakeDB()
    ok = await query_history(limit=5, db=db)
    assert len(ok["history"]) == 1
    assert ok["history"][0]["limit"] == 5

    db.raise_on_history = True
    err = await query_history(limit=5, db=db)
    assert err == {"history": []}


@pytest.mark.asyncio
async def test_cleanup_stale_success_and_error():
    db = FakeDB()
    ok = await cleanup_stale(db=db)
    assert ok["cleaned_paths"] == ["C:/old/file.txt"]

    db.raise_on_cleanup = True
    err = await cleanup_stale(db=db)
    assert err.status_code == 500
    payload = json.loads(err.body)
    assert payload["error"] == "Cleanup failed."


@pytest.mark.asyncio
async def test_export_index_success_and_error():
    db = FakeDB()
    ok = await export_index(db=db)
    assert ok["file_count"] == 2
    assert ok["chunk_count"] == 10
    assert len(ok["files"]) == 2

    db.raise_on_export = True
    err = await export_index(db=db)
    assert err.status_code == 500
    payload = json.loads(err.body)
    assert payload["error"] == "Export failed."


@pytest.mark.asyncio
async def test_get_system_info_non_windows(monkeypatch):
    monkeypatch.setattr("app.main.plat.system", lambda: "Linux")
    info = await get_system_info()
    assert info["os"] == "Linux"
    assert info["scan_method"] == "scandir"
    assert info["volumes"] == []


def test_request_validation_helpers(tmp_path: Path):
    good = str(tmp_path)
    bad = str(tmp_path / "does-not-exist")

    req = IndexRequest(folders=[f' "{good}" ', bad, "   "])
    assert req.validated_folders == [str(tmp_path.resolve())]

    q = QueryRequest(question="   hello world   ")
    assert q.validated_question == "hello world"

    fake_json = tmp_path / "meta.json"
    fake_json.write_text("{}", encoding="utf-8")
    ur = UnrealImportRequest(json_path=f" '{fake_json}' ")
    assert ur.validated_json_path == str(fake_json.resolve())


def test_context_builder_includes_stats_profiles_and_snippets():
    stats = {
        "total_files": 3,
        "total_size_mb": 1.5,
        "by_type": [{"ext": ".py", "count": 2, "size_mb": 1.0}],
        "by_folder": [{"folder": "Proj", "count": 3}],
    }
    stats_text = _format_file_stats(stats)
    assert "Total indexed files: 3" in stats_text
    assert "Proj: 3 files" in stats_text

    context = build_context(
        [{"file_path": "a.py", "text": "print('x')"}],
        max_tokens=500,
        file_stats=stats,
        folder_profiles_text="PROFILE",
    )
    assert "PROFILE" in context
    assert "Snippet 1 [a.py]" in context


def test_context_builder_empty_and_truncation():
    assert build_context([], max_tokens=10) == "No relevant context found."

    long_text = "x" * 5000
    context = build_context(
        [{"file_path": "big.txt", "text": long_text}],
        max_tokens=10,
    )
    assert "Snippet" not in context


class FakeInsightsDB:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def execute_query(self, _sql):
        if self.fail:
            raise RuntimeError("boom")
        self.calls += 1
        if self.calls == 1:
            return [(150, 3)]
        if self.calls == 2:
            return [("a.py", 100)]
        if self.calls == 3:
            return [("b.py", 40, 0)] # Added usage_count
        return [(".py", 2, 140), (".md", 1, 10)]


@pytest.mark.asyncio
async def test_insights_service_success_and_error():
    svc = InsightsService(FakeInsightsDB())
    stats = await svc.get_stats()
    assert stats["total_size_bytes"] == 150
    assert stats["file_count"] == 3
    assert stats["top_files"][0]["path"] == "a.py"
    assert stats["type_breakdown"][".py"]["count"] == 2
    assert stats["error"] is None

    failing = InsightsService(FakeInsightsDB(fail=True))
    err_stats = await failing.get_stats()
    assert err_stats["error"] is not None
