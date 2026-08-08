"""Provision and tear down the OCR virtualenv.

Follows the subprocess doctrine established in `app/providers/launcher.py`:
every argv element is a module-level constant, `shell=False` throughout, and
no request input ever reaches a command line.

The install builds into a temporary directory and is promoted with a single
rename at the end, so an interrupted install never leaves a half-populated
venv that `is_tier_installed()` would accept.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
import subprocess  # nosec B404 - argv is built from module constants only
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any

from app.config import settings
from app.ocr.protocol import PROTOCOL_VERSION
from app.ocr.settings import (
    MODEL_VERSION,
    TIER_STAMP,
    ensure_dirs,
    ocr_env_dir,
    ocr_models_dir,
    ocr_python,
    ocr_root,
    ocr_worker_dir,
    protocol_source_file,
    read_tier_stamp,
    worker_source_dir,
)

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000

#: Exact pins. Ranges here would mean an install that succeeds today and
#: produces different numbers next month.
#:
#: rapidocr-onnxruntime 1.4.x carries its PP-OCRv4 mobile ONNX models *inside*
#: the wheel - its config.yaml refers to them by package-relative paths, which
#: `update_model_path()` resolves at import. So pinning this one line pins the
#: models too: there is no separate download, no second digest to maintain, and
#: no runtime fetch from a CDN. (The 2.x `rapidocr` package changed this and
#: lazily downloads from ModelScope on first use, which is exactly why we are
#: not on it.)
PINNED_DEPS: tuple[str, ...] = (
    "onnxruntime==1.19.2",
    "rapidocr-onnxruntime==1.4.4",
    "pypdfium2==4.30.0",
    "opencv-python-headless==4.10.0.84",
    "numpy<2",
)

PYTHON_VERSION = "3.12"

UV_INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"

#: Optional local override. Empty in a normal install - the engine falls back
#: to the models bundled with the pinned wheel. Only consulted so a user can
#: drop in different weights without us re-provisioning anything.
MODEL_TARGETS = {
    "det": "det.onnx",
    "rec": "rec.onnx",
    "cls": "cls.onnx",
    "keys": "rec_keys.txt",
}

_SMOKE_TEST_TIMEOUT_S = 60


@dataclass
class InstallState:
    status: str = "idle"  # idle|running|ok|failed|cancelled
    step: str = ""
    pct: int = 0
    message: str = ""
    error_code: str = ""
    log_tail: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "step": self.step,
            "pct": self.pct,
            "message": self.message,
            "error_code": self.error_code,
            "log_tail": list(self.log_tail[-40:]),
        }


_state = InstallState()
# threading.Lock, not asyncio.Lock: this module is imported once per process
# but may be used from more than one event loop over that process's life, and
# an asyncio.Lock binds to the loop it is first awaited on. The critical
# section is a few field assignments, so a non-blocking thread lock is enough.
_state_lock = threading.Lock()
_cancel = threading.Event()
_active_proc: subprocess.Popen | None = None


def get_install_state() -> dict[str, Any]:
    return _state.as_dict()


# ── uv discovery ─────────────────────────────────────────────────────────


def _env_path(root: str | None, *parts: str) -> str | None:
    """Absolute candidate path, or None when the env root is unset.

    Without the guard an empty %LOCALAPPDATA% yields a *relative* path that
    could match a file in the current working directory - the same trap
    `launcher._existing_dir_path` documents.
    """
    if not root:
        return None
    return str(Path(root).joinpath(*parts))


def _uv_candidates() -> tuple[str, ...]:
    exe = "uv.exe" if sys.platform == "win32" else "uv"
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    raw = (
        _env_path(os.environ.get("LOCALAPPDATA"), "uv", "bin", exe),
        _env_path(home, ".local", "bin", exe),
        _env_path(home, ".cargo", "bin", exe),
    )
    return tuple(c for c in raw if c)


def find_uv() -> str | None:
    """Locate the uv binary, or None."""
    found = shutil.which("uv")
    if found:
        return found
    for candidate in _uv_candidates():
        if Path(candidate).is_file():
            return candidate
    return None


# ── status ───────────────────────────────────────────────────────────────


def tier_status() -> dict[str, Any]:
    from app.ocr.settings import is_tier_installed

    stamp = read_tier_stamp()
    return {
        "tier": settings.ocr_tier,
        "enabled": bool(settings.ocr_enabled),
        "installed": is_tier_installed(),
        "env_dir": str(ocr_env_dir()),
        "python": str(ocr_python()),
        "protocol": stamp.get("protocol"),
        "model_version": stamp.get("model_version"),
        "installed_at": stamp.get("installed_at"),
        "uv_available": find_uv() is not None,
    }


# ── worker file sync ─────────────────────────────────────────────────────


def _sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync_worker_files(target_dir: Path | None = None, *, force: bool = False) -> bool:
    """Copy worker sources + protocol.py into the venv, if they differ.

    Called on every manager start, not just at install. Upgrading PMA over an
    existing venv would otherwise leave last version's worker in place,
    talking a protocol the manager no longer speaks.
    """
    target = target_dir or ocr_worker_dir()
    source = worker_source_dir()
    if not source.is_dir():
        logger.error("OCR worker sources missing from this build: %s", source)
        return False

    payload: dict[str, Path] = {}
    for py in sorted(source.glob("*.py")):
        if py.name == "__init__.py":
            continue  # copied flat and run by path; no package semantics there
        payload[py.name] = py
    proto = protocol_source_file()
    if not proto.is_file():
        logger.error("OCR protocol source missing: %s", proto)
        return False
    payload["protocol.py"] = proto

    digests = {name: _sha256_text(p) for name, p in payload.items()}
    stamp_path = target / ".stamp"

    if not force and stamp_path.is_file():
        try:
            existing = json.loads(stamp_path.read_text(encoding="utf-8"))
            if (
                existing.get("protocol") == PROTOCOL_VERSION
                and existing.get("files") == digests
            ):
                return True
        except (OSError, ValueError):
            pass

    try:
        target.mkdir(parents=True, exist_ok=True)
        for name, src in payload.items():
            shutil.copy2(src, target / name)
        stamp_path.write_text(
            json.dumps({"protocol": PROTOCOL_VERSION, "files": digests}, indent=2),
            encoding="utf-8",
        )
        logger.info("Synced %d OCR worker file(s) to %s", len(payload), target)
        return True
    except OSError as exc:
        logger.error("Failed to sync OCR worker files: %s", exc)
        return False


def sweep_stale_installs() -> int:
    """Remove leftover .install-* directories from interrupted runs."""
    removed = 0
    root = ocr_root()
    if not root.is_dir():
        return 0
    for entry in root.glob(".install-*"):
        try:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Removed %d stale OCR install director(ies).", removed)
    return removed


# ── subprocess helpers ───────────────────────────────────────────────────


def _run(argv: list[str], *, timeout: int = 900) -> tuple[int, str]:
    """Run a command to completion, capturing combined output.

    CREATE_NO_WINDOW keeps a console from flashing: PMA ships with
    console=False, so any child that opens one is visible to the user.
    """
    global _active_proc
    kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "shell": False,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = CREATE_NO_WINDOW

    proc = subprocess.Popen(argv, **kwargs)  # nosec B603 - constant argv
    _active_proc = proc
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return 1, f"timed out after {timeout}s"
    finally:
        _active_proc = None


def _check_cancelled() -> None:
    if _cancel.is_set():
        raise _CancelledError()


class _CancelledError(Exception):
    """Raised at a step boundary when cancel_install() has been called."""


# ── install ──────────────────────────────────────────────────────────────


async def install_tier1(
    on_progress: Callable[[InstallState], None] | None = None,
) -> dict[str, Any]:
    """Provision the CPU OCR tier. Idempotent; safe to re-run after a failure."""
    global _state

    with _state_lock:
        if _state.status == "running":
            return _state.as_dict()
        _cancel.clear()
        _state = InstallState(status="running", step="uv", message="Locating uv...")

    def emit(step: str, pct: int, message: str) -> None:
        _state.step = step
        _state.pct = pct
        _state.message = message
        _state.log_tail.append(f"[{step}] {message}")
        if on_progress:
            with contextlib.suppress(Exception):
                on_progress(_state)
        logger.info("OCR install: %s", message)

    def fail(code: str, message: str) -> dict[str, Any]:
        _state.status = "failed"
        _state.error_code = code
        _state.message = message
        _state.log_tail.append(f"[error] {message}")
        logger.error("OCR install failed (%s): %s", code, message)
        return _state.as_dict()

    ensure_dirs()
    tmp = ocr_root() / f".install-{uuid.uuid4().hex}"
    promoted = False

    try:
        uv = find_uv()
        if not uv:
            # Deliberately no `sys.executable -m venv` fallback: under
            # PyInstaller sys.executable is PMA.exe, not an interpreter, and
            # -m venv fails in a way that is very hard to read.
            return fail(
                "UV_NOT_FOUND",
                f"uv is required to install the OCR engine. Install it from {UV_INSTALL_URL}",
            )

        # 1. venv
        emit("venv", 15, "Creating OCR environment...")
        _check_cancelled()
        code, out = await asyncio.to_thread(
            _run, [uv, "venv", "--python", PYTHON_VERSION, str(tmp)], timeout=300
        )
        if code != 0:
            return fail("VENV_FAILED", f"uv venv failed: {out[-400:]}")

        tmp_python = (
            tmp / "Scripts" / "python.exe" if sys.platform == "win32" else tmp / "bin" / "python"
        )

        # 2. deps - this also delivers the OCR models (see PINNED_DEPS)
        emit("deps", 35, "Installing OCR engine and models (this can take a few minutes)...")
        _check_cancelled()
        code, out = await asyncio.to_thread(
            _run,
            [uv, "pip", "install", "--python", str(tmp_python), "--no-cache", *PINNED_DEPS],
            timeout=1800,
        )
        if code != 0:
            return fail("DEPS_FAILED", f"dependency install failed: {out[-400:]}")

        # 3. worker sources
        emit("worker", 70, "Copying worker...")
        _check_cancelled()
        if not sync_worker_files(tmp / "worker", force=True):
            return fail("TIER_NOT_INSTALLED", "OCR worker sources missing from this build")

        # 4. smoke test - this is what actually proves the models loaded, since
        #    they came in with the wheel rather than through a checksum gate.
        emit("verify", 90, "Verifying the engine starts...")
        _check_cancelled()
        ok, detail = await asyncio.to_thread(_smoke_test, tmp_python, tmp / "worker")
        if not ok:
            return fail("MODEL_LOAD_FAILED", detail)

        # 7. promote
        emit("verify", 95, "Finalizing...")
        _check_cancelled()
        (tmp / TIER_STAMP).write_text(
            json.dumps(
                {
                    "tier": "cpu",
                    "protocol": PROTOCOL_VERSION,
                    "ep": "CPUExecutionProvider",
                    "model_version": MODEL_VERSION,
                    "installed_at": _now(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        # Not atomic on Windows: there is a window between rmtree and replace
        # where neither directory exists. If we die in it, tier_status()
        # reports not-installed and the next install starts clean.
        shutil.rmtree(ocr_env_dir(), ignore_errors=True)
        os.replace(tmp, ocr_env_dir())
        promoted = True

        _state.status = "ok"
        _state.pct = 100
        _state.message = "OCR engine installed."
        logger.info("OCR tier 1 installed at %s", ocr_env_dir())
        return _state.as_dict()

    except _CancelledError:
        _state.status = "cancelled"
        _state.message = "Install cancelled."
        return _state.as_dict()
    except Exception as exc:
        return fail("INSTALL_FAILED", str(exc))
    finally:
        if not promoted:
            shutil.rmtree(tmp, ignore_errors=True)


def _now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


def _smoke_test(python: Path, worker_dir: Path) -> tuple[bool, str]:
    """Start the worker, handshake, shut it down. Proves the venv actually runs."""
    from app.ocr import protocol as proto

    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "shell": False,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = CREATE_NO_WINDOW

    try:
        proc = subprocess.Popen(  # nosec B603 - constant argv
            [str(python), "-u", str(worker_dir / "__main__.py")], **kwargs
        )
    except OSError as exc:
        return False, f"could not start worker: {exc}"

    try:
        hello = proto.make_hello(
            models_dir=str(ocr_models_dir()),
            dpi=settings.ocr_dpi,
            conf_floor=settings.ocr_conf_floor,
            page_timeout_s=settings.ocr_page_timeout_s,
        )
        out, err = proc.communicate(
            input=proto.encode(hello) + proto.encode(proto.make_shutdown()),
            timeout=_SMOKE_TEST_TIMEOUT_S,
        )
        for line in (out or "").splitlines():
            try:
                msg = proto.decode(line)
            except proto.ProtocolError:
                continue
            if msg.get("t") == proto.RSP_READY:
                return True, ""
            if msg.get("t") == proto.RSP_ERROR:
                return False, f"{msg.get('code')}: {msg.get('detail', '')[:300]}"
        return False, f"worker never reported ready. stderr: {(err or '')[-300:]}"
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return False, f"worker did not respond within {_SMOKE_TEST_TIMEOUT_S}s"
    except Exception as exc:
        return False, str(exc)


async def cancel_install() -> bool:
    _cancel.set()
    proc = _active_proc
    if proc is not None:
        with contextlib.suppress(Exception):
            proc.terminate()
    return True


async def uninstall_tier1() -> dict[str, Any]:
    """Remove the venv and models. `ocr_cache` is intentionally untouched."""
    removed = []
    for target in (ocr_env_dir(), ocr_models_dir()):
        if target.exists():
            await asyncio.to_thread(shutil.rmtree, target, True)
            removed.append(str(target))
    logger.info("Uninstalled OCR tier: %s", ", ".join(removed) or "nothing to remove")
    return {"ok": True, "removed": removed}
