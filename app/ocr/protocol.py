"""Wire protocol between the OCR manager and its worker subprocess.

STDLIB ONLY, AND DUAL-IMPORTABLE. This file is copied verbatim into
``<ocr_env>/worker/`` at install time, so it must import cleanly both as
``app.ocr.protocol`` (manager side) and as bare ``protocol`` (worker side,
where the module is run by path and ``sys.path[0]`` is its own directory).

That means: no relative imports, no third-party imports, no ``app.*``
imports. ``tests/test_ocr_protocol.py`` enforces this by AST inspection - if
you add an import here that is not in ``sys.stdlib_module_names``, that test
fails.

Transport: JSON-lines, UTF-8, one object per line. Manager -> worker on stdin,
worker -> manager on stdout. stderr is diagnostics only and is drained by the
manager on a dedicated thread (an undrained stderr pipe deadlocks the worker
once its buffer fills).

Page payloads never travel over the pipe - the worker writes them to an NDJSON
side file. That keeps stdout small and bounded, and it means partial results
survive a ``kill()``: whatever reached the file is still indexable.
"""

from __future__ import annotations

import json

PROTOCOL_VERSION = 1

# ── Message kinds ────────────────────────────────────────────────────────
# manager -> worker
REQ_HELLO = "hello"
REQ_DOC = "doc"
REQ_CANCEL = "cancel"
REQ_SHUTDOWN = "shutdown"

# worker -> manager
RSP_READY = "ready"
RSP_PAGE = "page"
RSP_DOC_DONE = "doc_done"
RSP_ERROR = "error"
RSP_LOG = "log"

# ── Error codes ──────────────────────────────────────────────────────────
E_MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
E_PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
E_TIER_NOT_INSTALLED = "TIER_NOT_INSTALLED"
E_OCR_OOM = "OCR_OOM"
E_OCR_DOC_TIMEOUT = "OCR_DOC_TIMEOUT"
E_WORKER_CRASHED = "WORKER_CRASHED"
E_OCR_PAGE_TIMEOUT = "OCR_PAGE_TIMEOUT"
E_RASTER_FAILED = "RASTER_FAILED"

#: Tier is unusable. Stop draining and surface to the UI.
FATAL_ERRORS = frozenset(
    {
        E_MODEL_LOAD_FAILED,
        E_PROTOCOL_MISMATCH,
        E_TIER_NOT_INSTALLED,
    }
)

#: This document failed. Retry it, then mark it failed.
DOC_LEVEL_ERRORS = frozenset(
    {
        E_OCR_OOM,
        E_OCR_DOC_TIMEOUT,
        E_WORKER_CRASHED,
    }
)

#: This page failed. Skip it and keep going - a bad page never fails a doc.
PAGE_LEVEL_ERRORS = frozenset(
    {
        E_OCR_PAGE_TIMEOUT,
        E_RASTER_FAILED,
    }
)

# Worker exit codes, so the manager can distinguish causes without parsing.
EXIT_OK = 0
EXIT_CRASHED = 1
EXIT_PROTOCOL_MISMATCH = 3
EXIT_OOM = 4


class ProtocolError(ValueError):
    """A line could not be parsed as a protocol message."""


def encode(msg: dict) -> str:
    """Serialize one message, newline-terminated. Never raises on valid dicts."""
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n"


def decode(line: str) -> dict:
    """Parse one message line.

    Raises :class:`ProtocolError` on anything that is not a JSON object -
    including the blank lines and truncated tails a killed process leaves
    behind.
    """
    text = (line or "").strip()
    if not text:
        raise ProtocolError("empty line")
    try:
        obj = json.loads(text)
    except ValueError as exc:
        raise ProtocolError(f"malformed JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError(f"expected object, got {type(obj).__name__}")
    if not obj.get("t"):
        raise ProtocolError("missing message kind 't'")
    return obj


# ── Constructors ─────────────────────────────────────────────────────────
# Keyword-only so a field added later can never be silently absorbed by a
# positional argument on the far side of a version skew.


def make_hello(
    *,
    protocol_version: int = PROTOCOL_VERSION,
    models_dir: str,
    dpi: int,
    conf_floor: float,
    page_timeout_s: int,
) -> dict:
    return {
        "t": REQ_HELLO,
        "protocol": protocol_version,
        "models_dir": models_dir,
        "dpi": dpi,
        "conf_floor": conf_floor,
        "page_timeout_s": page_timeout_s,
    }


def make_ready(*, protocol_version: int = PROTOCOL_VERSION, model_version: str, ep: str) -> dict:
    return {
        "t": RSP_READY,
        "protocol": protocol_version,
        "model_version": model_version,
        "ep": ep,
    }


def make_doc(*, doc_id: str, path: str, pages: list, ndjson_path: str, dpi: int) -> dict:
    return {
        "t": REQ_DOC,
        "doc_id": doc_id,
        "path": path,
        "pages": list(pages),
        "ndjson": ndjson_path,
        "dpi": dpi,
    }


def make_page(*, doc_id: str, page: int, ok: bool, ms: int) -> dict:
    """Ack for one page. Progress signal only - the text went to the NDJSON."""
    return {"t": RSP_PAGE, "doc_id": doc_id, "page": page, "ok": ok, "ms": ms}


def make_doc_done(*, doc_id: str, pages_ok: int, pages_failed: int, mean_conf: float) -> dict:
    return {
        "t": RSP_DOC_DONE,
        "doc_id": doc_id,
        "pages_ok": pages_ok,
        "pages_failed": pages_failed,
        "mean_conf": mean_conf,
    }


def make_error(
    *, code: str, detail: str = "", doc_id: str | None = None, page: int | None = None
) -> dict:
    msg: dict = {"t": RSP_ERROR, "code": code, "detail": detail}
    if doc_id is not None:
        msg["doc_id"] = doc_id
    if page is not None:
        msg["page"] = page
    return msg


def make_shutdown() -> dict:
    return {"t": REQ_SHUTDOWN}


def make_cancel(*, doc_id: str) -> dict:
    return {"t": REQ_CANCEL, "doc_id": doc_id}


def is_fatal(code: str) -> bool:
    return code in FATAL_ERRORS
