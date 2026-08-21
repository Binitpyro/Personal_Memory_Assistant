"""Protocol round-trips, and the test that actually enforces worker isolation.

The isolation rule ("worker/ and protocol.py must not import app.* or any
third-party package") cannot be enforced by code - only by checking. That is
what `test_worker_imports_are_stdlib_only` is for. Without it the rule decays
into a comment.
"""

import ast
import sys
from pathlib import Path

import pytest

from app.ocr import protocol

WORKER_DIR = Path(__file__).parent.parent / "app" / "ocr" / "worker"
PROTOCOL_FILE = Path(__file__).parent.parent / "app" / "ocr" / "protocol.py"

#: Modules the worker installs into its own venv. Legitimate for worker/*.py,
#: never for protocol.py.
WORKER_VENV_DEPS = {"numpy", "pypdfium2", "rapidocr_onnxruntime", "onnxruntime", "cv2"}

#: The worker's own sibling modules, imported by bare name because they are
#: copied flat into <ocr_env>/worker/ and run by path.
WORKER_SIBLINGS = {"protocol", "engine", "raster", "postproc"}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import
                roots.add(".")
            elif node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_protocol_imports_are_stdlib_only():
    """protocol.py is shared byte-identically across the process boundary."""
    roots = _imported_roots(PROTOCOL_FILE)
    offenders = {r for r in roots if r not in sys.stdlib_module_names}
    assert not offenders, f"protocol.py must import stdlib only, found: {offenders}"


def test_protocol_has_no_relative_imports():
    """It is imported both as app.ocr.protocol and as bare `protocol`."""
    assert "." not in _imported_roots(PROTOCOL_FILE)


@pytest.mark.parametrize(
    "worker_file",
    sorted(p for p in WORKER_DIR.glob("*.py") if p.name != "__init__.py"),
    ids=lambda p: p.name,
)
def test_worker_imports_are_stdlib_only(worker_file):
    """No worker module may import `app.*` - it runs in a separate venv."""
    roots = _imported_roots(worker_file)
    assert "app" not in roots, f"{worker_file.name} imports app.* across the venv boundary"
    assert "." not in roots, f"{worker_file.name} uses a relative import but is run by path"

    allowed = sys.stdlib_module_names | WORKER_VENV_DEPS | WORKER_SIBLINGS
    offenders = {r for r in roots if r not in allowed}
    assert not offenders, f"{worker_file.name} imports unpinned packages: {offenders}"


def test_worker_files_are_flat():
    """Copied flat into the venv, so no subpackages may exist."""
    assert not [d for d in WORKER_DIR.iterdir() if d.is_dir() and d.name != "__pycache__"]


# ── codec ────────────────────────────────────────────────────────────────


def test_encode_decode_round_trip():
    original = protocol.make_doc(
        doc_id="d-1",
        path=r"C:\scan.pdf",
        pages=[0, 3, 7],
        # Just a payload string; nothing is opened here.
        ndjson_path=r"C:\scratch\x.ndjson",
        dpi=300,
    )
    assert protocol.decode(protocol.encode(original)) == original


def test_encode_is_newline_terminated():
    """The reader is line-oriented; a missing newline would stall it."""
    assert protocol.encode({"t": "ping"}).endswith("\n")


def test_encode_preserves_non_ascii_paths():
    msg = protocol.make_doc(
        doc_id="d", path="C:/文書/スキャン.pdf", pages=[0], ndjson_path="x", dpi=300
    )
    assert protocol.decode(protocol.encode(msg))["path"] == "C:/文書/スキャン.pdf"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   \n",
        "not json at all",
        '{"t":"doc"',  # truncated, as a killed process leaves behind
        "[1, 2, 3]",  # valid JSON, wrong shape
        '{"no_kind": true}',
    ],
)
def test_decode_rejects_malformed_lines(line):
    with pytest.raises(protocol.ProtocolError):
        protocol.decode(line)


# ── error taxonomy ───────────────────────────────────────────────────────


def test_error_classes_are_disjoint():
    assert not protocol.FATAL_ERRORS & protocol.DOC_LEVEL_ERRORS
    assert not protocol.FATAL_ERRORS & protocol.PAGE_LEVEL_ERRORS
    assert not protocol.DOC_LEVEL_ERRORS & protocol.PAGE_LEVEL_ERRORS


def test_page_level_errors_are_not_fatal():
    """A bad page must never take down the tier."""
    for code in protocol.PAGE_LEVEL_ERRORS:
        assert not protocol.is_fatal(code)


def test_protocol_mismatch_is_fatal():
    assert protocol.is_fatal(protocol.E_PROTOCOL_MISMATCH)


def test_version_is_an_int():
    assert isinstance(protocol.PROTOCOL_VERSION, int)
