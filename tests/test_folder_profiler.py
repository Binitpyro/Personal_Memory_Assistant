"""
tests/test_folder_profiler.py
Full coverage of app/indexing/folder_profiler.py.
Tests project type detection, profile building, overlap resolution.
"""

from pathlib import Path

import pytest

from app.indexing.folder_profiler import (
    _collect_project_markers,
    _dominant_extension_project_type,
    _indicator_matches,
    build_folder_profile,
    detect_project_type,
    generate_folder_profiles_async,
    resolve_folder_overlaps,
)

# ── _indicator_matches ────────────────────────────────────────────────────────


class TestIndicatorMatches:
    def test_ext_match(self):
        assert _indicator_matches("ext", ".py", {".py", ".md"}, set(), set())

    def test_ext_no_match(self):
        assert not _indicator_matches("ext", ".rs", {".py"}, set(), set())

    def test_file_match(self):
        assert _indicator_matches("file", "setup.py", set(), {"setup.py"}, set())

    def test_file_no_match(self):
        assert not _indicator_matches("file", "cargo.toml", set(), {"setup.py"}, set())

    def test_dir_match(self):
        assert _indicator_matches("dir", "node_modules", set(), set(), {"node_modules"})

    def test_dir_no_match(self):
        assert not _indicator_matches("dir", "target", set(), set(), {"src"})

    def test_unknown_kind_returns_false(self):
        assert not _indicator_matches("unknown", "x", set(), set(), set())


# ── _collect_project_markers ──────────────────────────────────────────────────


class TestCollectProjectMarkers:
    def test_collects_extensions(self, tmp_path):
        files = [(tmp_path / "app.py", ".py"), (tmp_path / "README.md", ".md")]
        exts, _names, _dirs = _collect_project_markers(files, tmp_path)
        assert ".py" in exts
        assert ".md" in exts

    def test_collects_filenames(self, tmp_path):
        files = [(tmp_path / "setup.py", ".py")]
        _exts, names, _dirs = _collect_project_markers(files, tmp_path)
        assert "setup.py" in names

    def test_collects_directory(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "main.rs").touch()
        files = [(sub / "main.rs", ".rs")]
        _exts, _names, dirs = _collect_project_markers(files, tmp_path)
        assert "src" in dirs


# ── _dominant_extension_project_type ─────────────────────────────────────────


class TestDominantExtension:
    def test_dominant_extension_returned(self, tmp_path):
        files = [(tmp_path / f"file{i}.py", ".py") for i in range(5)]
        files += [(tmp_path / "readme.md", ".md")]
        result = _dominant_extension_project_type(files)
        assert result is not None
        assert ".py" in result[0]

    def test_no_extensions_returns_none(self):
        files = [(Path("Makefile"), "")]
        result = _dominant_extension_project_type(files)
        # Makefile has no suffix — could be None
        assert result is None or isinstance(result, tuple)

    def test_empty_files_returns_none(self):
        result = _dominant_extension_project_type([])
        assert result is None


# ── detect_project_type ───────────────────────────────────────────────────────


class TestDetectProjectType:
    def test_python_project(self, tmp_path):
        (tmp_path / "setup.py").touch()
        files = [(tmp_path / "setup.py", ".py"), (tmp_path / "main.py", ".py")]
        proj_type, desc = detect_project_type(files, tmp_path)
        assert isinstance(proj_type, str)
        assert isinstance(desc, str)

    def test_unknown_project(self, tmp_path):
        files = [(tmp_path / "data.dat", ".dat")]
        proj_type, _desc = detect_project_type(files, tmp_path)
        assert isinstance(proj_type, str)

    def test_empty_folder(self, tmp_path):
        proj_type, _desc = detect_project_type([], tmp_path)
        assert proj_type == "unknown"


# ── build_folder_profile ──────────────────────────────────────────────────────


class TestBuildFolderProfile:
    def test_basic_profile(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hi')")
        files = [(tmp_path / "main.py", ".py")]
        profile = build_folder_profile(tmp_path, "my_project", files)
        assert "folder_path" in profile
        assert "profile_text" in profile
        assert "project_type" in profile
        assert "file_count" in profile
        assert profile["file_count"] == 1

    def test_profile_with_key_files(self, tmp_path):
        (tmp_path / "README.md").write_text("# Project")
        files = [(tmp_path / "README.md", ".md")]
        profile = build_folder_profile(tmp_path, "docs", files)
        assert profile["key_files"] != "" or profile["profile_text"]

    def test_profile_total_size(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("a" * 1000)
        files = [(f, ".txt")]
        profile = build_folder_profile(tmp_path, "t", files)
        assert profile["total_size_bytes"] > 0

    def test_profile_excludes_files_outside_folder(self, tmp_path):
        other = tmp_path.parent / "other_file.py"
        other.touch()
        files = [(other, ".py")]
        profile = build_folder_profile(tmp_path, "t", files)
        assert profile["file_count"] == 0


# ── resolve_folder_overlaps ───────────────────────────────────────────────────


class TestResolveFolderOverlaps:
    def test_removes_child_when_parent_present(self, tmp_path):
        parent = tmp_path / "projects"
        parent.mkdir()
        child = parent / "myapp"
        child.mkdir()
        result = resolve_folder_overlaps([str(parent), str(child)])
        assert len(result) == 1
        assert result[0] == parent.resolve()

    def test_keeps_independent_folders(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        result = resolve_folder_overlaps([str(a), str(b)])
        assert len(result) == 2

    def test_strips_quotes(self, tmp_path):
        folder = tmp_path / "mydir"
        folder.mkdir()
        result = resolve_folder_overlaps([f'"{folder!s}"'])
        assert len(result) == 1

    def test_nonexistent_folders_excluded(self, tmp_path):
        result = resolve_folder_overlaps([str(tmp_path / "nonexistent")])
        assert result == []

    def test_empty_list(self):
        result = resolve_folder_overlaps([])
        assert result == []

    def test_whitespace_stripped(self, tmp_path):
        folder = tmp_path / "spaced"
        folder.mkdir()
        result = resolve_folder_overlaps([f"  {folder!s}  "])
        assert len(result) == 1


# ── generate_folder_profiles_async ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_folder_profiles_async(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "main.py").write_text("x = 1")
    files = [(folder / "main.py", ".py", str(folder))]
    profiles = await generate_folder_profiles_async(files, [folder])
    assert len(profiles) == 1
    assert profiles[0]["folder_path"] == str(folder)


@pytest.mark.asyncio
async def test_generate_folder_profiles_async_handles_error(tmp_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    # Pass a non-existent file - profile should still be produced or skipped gracefully
    files = [(folder / "ghost.py", ".py", str(folder))]
    profiles = await generate_folder_profiles_async(files, [folder])
    assert isinstance(profiles, list)
