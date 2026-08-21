import os
import sys
import tempfile
from pathlib import Path

import pytest

from app.scanner.scanner import ScanResult, scan_folder


def test_fast_scan():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some dummy files
        for i in range(3):
            with open(os.path.join(tmpdir, f"test{i}.txt"), "w") as f:
                f.write("hello word dummy")

        # Test scandir (by skipping MFT)
        result = scan_folder(Path(tmpdir), {".txt"})
        assert isinstance(result, ScanResult)
        assert len(result.files) >= 3
        # Ensure 'test0.txt' is in the results
        assert any("test0.txt" in str(f) for f in result.files)


# UNC / network-share handling
#
# `std::fs::canonicalize` returns extended-length paths, and rust_core strips the
# `\\?\` prefix so downstream consumers can use them. That strip used to be a
# plain 4-character `trim_start_matches`, which is right for a drive path and
# wrong for a UNC one: Windows canonicalizes `\\server\share\x` to
# `\\?\UNC\server\share\x`, so the strip left the *relative* string
# `UNC\server\share\x`. `Path.absolute()` then glued it onto the process
# working directory, `stat()` raised, and `_detect_changes` counted every file on
# a network share as skipped without recording why.

BS = chr(92)


def _unc_root_for(path: Path) -> str | None:
    r"""A `\\localhost\<drive>$` spelling of *path*, or None if unreachable.

    Admin shares are on by default on Windows but can be disabled by policy, and
    there is no share at all on other platforms, so this is a skip and not a
    failure.
    """
    drive = path.drive
    if len(drive) != 2 or not drive.endswith(":"):
        return None
    unc = BS + BS + "localhost" + BS + drive[0] + "$" + str(path)[2:]
    return unc if os.path.isdir(unc) else None


@pytest.mark.skipif(sys.platform != "win32", reason="UNC paths are Windows-only")
def test_rust_scanner_keeps_a_unc_path_absolute():
    rust_core = pytest.importorskip("rust_core")

    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir).resolve()
        (target / "share_probe.txt").write_text("x", encoding="utf-8")

        unc_root = _unc_root_for(target)
        if unc_root is None:
            pytest.skip("no reachable administrative share to test UNC against")

        found = rust_core.scan_folders([unc_root], [".txt"])
        assert len(found) == 1, f"scanned nothing through the share: {found}"

        result = Path(found[0])
        assert result.is_absolute(), f"UNC path came back relative: {found[0]!r}"
        assert not found[0].startswith("UNC"), f"prefix left mangled: {found[0]!r}"
        assert str(result) == str(result.absolute()), "absolute() rewrote the path"
        # The whole point: the indexer must be able to stat it. Before the fix
        # this raised FileNotFoundError and the file was silently skipped.
        assert result.stat().st_size == 1


@pytest.mark.skipif(sys.platform != "win32", reason="UNC paths are Windows-only")
def test_unc_file_is_attributed_to_its_indexed_root():
    """`_scan_all_folders_rust` derives folder_tag and root_path via relative_to.

    A relative scan result made that raise ValueError, so a network-share file
    landed with tag "Unknown" and no root -- ungrouped in the Explorer even if it
    had been indexed at all.
    """
    rust_core = pytest.importorskip("rust_core")

    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir).resolve()
        (target / "share_probe.txt").write_text("x", encoding="utf-8")

        unc_root = _unc_root_for(target)
        if unc_root is None:
            pytest.skip("no reachable administrative share to test UNC against")

        found = rust_core.scan_folders([unc_root], [".txt"])
        root = Path(unc_root).resolve()
        # Must not raise.
        rel = Path(found[0]).resolve().relative_to(root)
        assert rel.name == "share_probe.txt"
