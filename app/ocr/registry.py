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
    ocr_scratch_dir,
    ocr_tier_models_dir,
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
#:
#: opencv-python, not opencv-python-headless. rapidocr-onnxruntime 1.4.4
#: requires `opencv-python>=4.5.1.48`, and the headless build is a *different
#: distribution* that does not satisfy it. Pinning headless therefore installed
#: both: `uv pip compile` on the old pin set resolves to
#: `opencv-python-headless==4.10.0.84` *and* `opencv-python==4.11.0.86`, which
#: ship the same `cv2` package so one silently overwrites the other - and the
#: one rapidocr actually pulled was unpinned, defeating the whole point of the
#: exact pins above. Naming the distribution rapidocr asks for gives one cv2 at
#: a fixed version, and is one package smaller than what shipped before.
PINNED_DEPS: tuple[str, ...] = (
    "onnxruntime==1.19.2",
    "rapidocr-onnxruntime==1.4.4",
    "pypdfium2==4.30.0",
    "opencv-python==4.10.0.84",
    "numpy<2",
)

#: Tier 2. Installed with `uv pip install --no-deps`, which is mandatory rather
#: than stylistic: rapidocr-onnxruntime's metadata requires `onnxruntime`, so
#: any *resolved* install re-adds the CPU build alongside the DirectML one.
#: Verified with `uv pip compile` - the naive swap resolves to
#: onnxruntime-directml==1.19.2 AND an unpinned onnxruntime==1.28.0, which unpack
#: into the same `onnxruntime/` package, so DmlExecutionProvider silently
#: disappears and the runtime version floats.
#:
#: Because --no-deps skips resolution, this list is the *complete* transitive
#: closure and must be regenerated, not hand-edited: compile PINNED_DEPS and
#: substitute the onnxruntime line.
PINNED_DEPS_GPU: tuple[str, ...] = (
    "colorama==0.4.6",
    "coloredlogs==15.0.1",
    "flatbuffers==25.12.19",
    "humanfriendly==10.0",
    "mpmath==1.3.0",
    "numpy==1.26.4",
    "onnxruntime-directml==1.19.2",
    "opencv-python==4.10.0.84",
    "packaging==26.3",
    "pillow==12.3.0",
    "protobuf==7.35.1",
    "pyclipper==1.4.0",
    "pypdfium2==4.30.0",
    "pyreadline3==3.5.6",
    "pyyaml==6.0.3",
    "rapidocr-onnxruntime==1.4.4",
    "shapely==2.1.2",
    "six==1.17.0",
    "sympy==1.14.0",
    "tqdm==4.70.0",
)

#: Per-tier dependency sets, and whether the tier's install must skip resolution.
TIER_DEPS: dict[str, tuple[tuple[str, ...], bool]] = {
    "cpu": (PINNED_DEPS, False),
    "gpu": (PINNED_DEPS_GPU, True),
}


#: Tiers that cannot be provisioned on this platform, with the reason shown to
#: the user. DirectML ships win_amd64 wheels only; there is no point offering a
#: "GPU" tier elsewhere that would silently run 194 MB of server weights on the
#: CPU, strictly slower than Tier 1.
def unavailable_reason(tier: str) -> str:
    if tier == "gpu" and sys.platform != "win32":
        return "The GPU tier needs DirectML, which is only available on Windows."
    return ""


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
        # The execution provider the installer verified. Previously computed,
        # stamped, and then never exposed - which left the UI unable to tell a
        # GPU tier that fell back to CPU from one that did not.
        "ep": stamp.get("ep"),
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
            if existing.get("protocol") == PROTOCOL_VERSION and existing.get("files") == digests:
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


def begin_install() -> bool:
    """Arm the install state. False when one is already running.

    Split out of `install_tier1` so the HTTP layer can flip the state to
    "running" *before* it responds. Under `create_task` the coroutine body does
    not execute until the loop next yields, so a caller that armed inside the
    task would answer "idle" to the request that started it - and the UI polls
    on "running", so it would never begin polling.
    """
    global _state

    with _state_lock:
        if _state.status == "running":
            return False
        _cancel.clear()
        _state = InstallState(status="running", step="uv", message="Locating uv...")
        return True


async def install_tier(
    tier: str = "cpu",
    on_progress: Callable[[InstallState], None] | None = None,
    *,
    _armed: bool = False,
) -> dict[str, Any]:
    """Provision an OCR tier. Idempotent; safe to re-run after a failure.

    Pass `_armed=True` when `begin_install()` has already claimed the state.
    """
    global _state

    if not _armed:
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
        if tier not in TIER_DEPS:
            return fail("UNKNOWN_TIER", f"No such OCR tier: {tier!r}")
        blocked = unavailable_reason(tier)
        if blocked:
            return fail("TIER_UNAVAILABLE", blocked)
        deps, skip_resolution = TIER_DEPS[tier]

        uv = find_uv()
        if not uv:
            # Deliberately no `sys.executable -m venv` fallback: under
            # PyInstaller sys.executable is PMA.exe, not an interpreter, and
            # -m venv fails in a way that is very hard to read.
            return fail(
                "UV_NOT_FOUND",
                f"uv is required to install the OCR engine. Install it from {UV_INSTALL_URL}",
            )

        # The venv alone is ~230 MB unpacked, plus uv's own build scratch.
        ok, detail = _check_free_space(ocr_root(), 400 * 1024 * 1024)
        if not ok:
            return fail("NO_DISK_SPACE", detail)

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

        # 2. deps - for "cpu" this also delivers the OCR models (see PINNED_DEPS)
        emit("deps", 30, "Installing OCR engine (this can take a few minutes)...")
        _check_cancelled()
        install_argv = [uv, "pip", "install", "--python", str(tmp_python), "--no-cache"]
        if skip_resolution:
            # Mandatory for the GPU tier: rapidocr requires `onnxruntime`, so a
            # resolved install re-adds the CPU build next to the DirectML one and
            # silently loses DmlExecutionProvider. See PINNED_DEPS_GPU.
            install_argv.append("--no-deps")
        code, out = await asyncio.to_thread(_run, [*install_argv, *deps], timeout=1800)
        if code != 0:
            return fail("DEPS_FAILED", f"dependency install failed: {out[-400:]}")

        # 2b. weights that do not ride in on a wheel
        models_dir = ocr_tier_models_dir(tier)
        if tier in _TIER_MODEL_LOCK:
            emit("models", 55, "Downloading OCR models...")
            _check_cancelled()
            ok, detail = await asyncio.to_thread(_fetch_tier_models, tier, models_dir)
            if not ok:
                return fail("MODEL_DOWNLOAD_FAILED", detail)

        # 3. worker sources
        emit("worker", 70, "Copying worker...")
        _check_cancelled()
        if not sync_worker_files(tmp / "worker", force=True):
            return fail("TIER_NOT_INSTALLED", "OCR worker sources missing from this build")

        # 4. smoke test - for "cpu" this is what proves the models loaded at all,
        #    since they arrive with the wheel rather than through a checksum gate.
        emit("verify", 90, "Verifying the engine starts...")
        _check_cancelled()
        ok, detail, ready = await asyncio.to_thread(
            _smoke_test, tmp_python, tmp / "worker", models_dir
        )
        if not ok:
            return fail("MODEL_LOAD_FAILED", detail)

        # 7. promote
        emit("verify", 95, "Finalizing...")
        _check_cancelled()
        # Record what the engine *reported*, not what we assumed it would do.
        # The manager compares the two at runtime to spot a degraded engine; a
        # stamp built from assumptions would compare an assumption to itself.
        (tmp / TIER_STAMP).write_text(
            json.dumps(
                {
                    "tier": tier,
                    "protocol": PROTOCOL_VERSION,
                    "ep": ready.get("ep") or "CPUExecutionProvider",
                    "model_version": ready.get("model_version") or MODEL_VERSION,
                    "installed_at": _now(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        # Not atomic on Windows: there is a window between rmtree and replace
        # where neither directory exists. If we die in it, tier_status()
        # reports not-installed and the next install starts clean.
        shutil.rmtree(ocr_env_dir(tier), ignore_errors=True)
        os.replace(tmp, ocr_env_dir(tier))
        promoted = True

        _state.status = "ok"
        _state.pct = 100
        _state.message = "OCR engine installed."
        logger.info(
            "OCR tier %s installed at %s (%s, %s)",
            tier,
            ocr_env_dir(tier),
            ready.get("model_version", "?"),
            ready.get("ep", "?"),
        )
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


async def install_tier1(
    on_progress: Callable[[InstallState], None] | None = None,
    *,
    _armed: bool = False,
) -> dict[str, Any]:
    """Back-compat alias for the CPU tier."""
    return await install_tier("cpu", on_progress, _armed=_armed)


#: models.lock.json entry per tier, for tiers whose weights are not in a wheel.
#: `_custom_model_paths()` in worker/engine.py looks for these exact filenames.
_TIER_MODEL_LOCK: dict[str, tuple[str, dict[str, str]]] = {
    "gpu": (
        "PP-OCRv4-server",
        {
            "PP-OCRv4/ch_PP-OCRv4_det_server_infer.onnx": "det.onnx",
            "PP-OCRv4/ch_PP-OCRv4_rec_server_infer.onnx": "rec.onnx",
        },
    ),
}


def _fetch_tier_models(tier: str, dest: Path) -> tuple[bool, str]:
    """Download and digest-verify a tier's weights into `dest`.

    Verify-then-rename, never rename-then-verify: a truncated file left under
    the name the engine looks for would be loaded on the next start, and
    `_custom_model_paths()` only checks that the path exists. Nothing sweeps
    this directory either, so a bad file would persist indefinitely.
    """
    from app.utils.model_integrity import (
        configure_hf_env,
        load_models_lock,
        verify_file_sha256,
    )

    entry_name, wanted = _TIER_MODEL_LOCK[tier]
    try:
        lock = load_models_lock(family="ocr")
    except ValueError as exc:
        return False, f"OCR model lockfile unusable: {exc}"

    entry = lock.get(entry_name)
    if not entry:
        return False, f"models.lock.json has no OCR entry named {entry_name!r}"

    expected_files: dict[str, Any] = entry.get("files") or {}
    needed = sum(int((expected_files.get(p) or {}).get("size_bytes") or 0) for p in wanted)
    # Twice the payload: the download lands in a cache before it is verified and
    # moved into place, so both copies exist at once.
    ok, detail = _check_free_space(dest, needed * 2)
    if not ok:
        return False, detail

    configure_hf_env()
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return False, "huggingface_hub is not installed; cannot fetch OCR models."

    dest.mkdir(parents=True, exist_ok=True)
    # Cache inside our own tree, not the shared HF cache under ~/.cache. On
    # Windows the hub falls back from symlinks to real copies, so the default
    # would leave a second ~194 MB outside `data/` that uninstall cannot reach
    # and the user cannot find. Scoped here, it is deleted as soon as the
    # verified bytes are in place.
    hf_cache = ocr_root() / ".hf-cache"

    try:
        for repo_path, local_name in wanted.items():
            spec = expected_files.get(repo_path)
            if not spec or not spec.get("sha256"):
                return False, f"{repo_path} is not pinned in models.lock.json"
            try:
                cached = hf_hub_download(  # nosec B615 - repo and revision are pinned
                    repo_id=entry["repo_id"],
                    revision=entry["revision"],
                    filename=repo_path,
                    cache_dir=str(hf_cache),
                )
            except Exception as exc:  # network, 404, gated repo, disk full
                return False, f"could not fetch {repo_path}: {exc}"

            if not verify_file_sha256(Path(cached), spec["sha256"], label=repo_path):
                return False, f"{repo_path} failed checksum verification"

            target = dest / local_name
            staging = dest / f".{local_name}.partial"
            try:
                shutil.copyfile(cached, staging)
                os.replace(staging, target)
            except OSError as exc:
                with contextlib.suppress(OSError):
                    staging.unlink(missing_ok=True)
                return False, f"could not place {local_name}: {exc}"

        return True, ""
    finally:
        # Reclaimed whether or not the fetch succeeded; a half-downloaded blob
        # here is pure waste once the verified copy exists.
        shutil.rmtree(hf_cache, ignore_errors=True)


#: Slack on top of a computed requirement, covering pip's own temporary files
#: and leaving the volume something to breathe with afterwards.
_FREE_SPACE_MARGIN_BYTES = 300 * 1024 * 1024


def _check_free_space(where: Path, needed_bytes: int) -> tuple[bool, str]:
    """Refuse before a long download rather than failing part-way through it.

    A full disk previously surfaced as the tail of a pip traceback after several
    minutes of work, with a half-written model left behind.
    """
    probe = where
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return True, ""  # not knowing is not a reason to block the install

    required = needed_bytes + _FREE_SPACE_MARGIN_BYTES
    if free >= required:
        return True, ""
    return False, (
        f"Not enough free space on {probe.drive or probe}: "
        f"{free / 1024 / 1024:.0f} MB available, about "
        f"{required / 1024 / 1024:.0f} MB needed."
    )


#: Rendered into the smoke-test fixture and expected back out of the engine.
#: Digits and letters only, no punctuation the tokenizer might drop, and short
#: enough to survive a conservative confidence floor.
SMOKE_TEST_PHRASE = "PMA OCR 12345"

#: Fraction of `SMOKE_TEST_PHRASE` characters that must come back. Not 1.0: a
#: single mis-read glyph is a quality issue, whereas a wrong *dictionary*
#: produces near-total garbage. This gate is aimed at the latter - the failure
#: mode where a model loads happily and emits confident nonsense.
_SMOKE_TEST_MIN_RATIO = 0.6


def _smoke_test_pdf(dest: Path) -> Path:
    """Write a one-page PDF containing SMOKE_TEST_PHRASE in a large font.

    Built by hand rather than with a library: the OCR venv has no PDF *writer*,
    and adding one purely for a self-test would enlarge every install. A text
    PDF is a valid OCR fixture because the worker rasterizes the page first -
    the engine only ever sees pixels.
    """
    body = f"BT /F1 48 Tf 60 690 Td ({SMOKE_TEST_PHRASE}) Tj ET".encode("ascii")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length " + str(len(body)).encode("ascii") + b">>stream\n" + body + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(i).encode("ascii") + b" 0 obj" + obj + b"endobj\n"

    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode("ascii") + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += b"trailer<</Size " + str(len(objects) + 1).encode("ascii") + b"/Root 1 0 R>>\n"
    out += b"startxref\n" + str(xref_at).encode("ascii") + b"\n%%EOF\n"

    dest.write_bytes(bytes(out))
    return dest


def _smoke_text_matches(recognized: str) -> bool:
    """True when enough of the expected phrase came back.

    Compared on the stripped alphanumeric stream so spacing and line-splitting
    differences between engines do not fail an otherwise correct read.
    """
    got = {c for c in recognized.upper() if c.isalnum()}
    want = [c for c in SMOKE_TEST_PHRASE.upper() if c.isalnum()]
    if not want:
        return True
    hits = sum(1 for c in want if c in got)
    return (hits / len(want)) >= _SMOKE_TEST_MIN_RATIO


def _now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


def _smoke_test(
    python: Path, worker_dir: Path, models_dir: Path | None = None
) -> tuple[bool, str, dict[str, Any]]:
    """Start the worker, handshake, shut it down. Proves the venv actually runs.

    Returns the `ready` payload alongside the verdict so the caller can stamp
    what the engine *reported* rather than what it assumed. Deriving the stamp
    independently would let the two drift, and the manager compares them at
    runtime to detect a degraded engine - a stamp built from assumptions would
    make that check compare an assumption against itself.
    """
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
        return False, f"could not start worker: {exc}", {}

    scratch = ocr_scratch_dir()
    scratch.mkdir(parents=True, exist_ok=True)
    fixture = scratch / f".smoke-{uuid.uuid4().hex[:8]}.pdf"
    ndjson = scratch / f"{fixture.stem}.ndjson"

    try:
        hello = proto.make_hello(
            models_dir=str(models_dir or ocr_models_dir()),
            dpi=settings.ocr_dpi,
            conf_floor=settings.ocr_conf_floor,
            page_timeout_s=settings.ocr_page_timeout_s,
        )
        # Recognize a page whose text we already know. Proving the worker starts
        # says nothing about whether it reads correctly: a recognition model
        # paired with the wrong character dictionary loads without complaint and
        # emits confident nonsense, which would be cached and indexed as if it
        # were the document's real text.
        _smoke_test_pdf(fixture)
        doc = proto.make_doc(
            doc_id="smoke",
            path=str(fixture),
            pages=[0],
            ndjson_path=str(ndjson),
            dpi=settings.ocr_dpi,
        )
        out, err = proc.communicate(
            input=proto.encode(hello) + proto.encode(doc) + proto.encode(proto.make_shutdown()),
            timeout=_SMOKE_TEST_TIMEOUT_S,
        )
        for line in (out or "").splitlines():
            try:
                msg = proto.decode(line)
            except proto.ProtocolError:
                continue
        ready: dict[str, Any] = {}
        for line in (out or "").splitlines():
            try:
                msg = proto.decode(line)
            except proto.ProtocolError:
                continue
            if msg.get("t") == proto.RSP_READY:
                ready = msg
            if msg.get("t") == proto.RSP_ERROR:
                return False, f"{msg.get('code')}: {msg.get('detail', '')[:300]}", {}
        if not ready:
            return False, f"worker never reported ready. stderr: {(err or '')[-300:]}", {}

        recognized = _read_smoke_text(ndjson)
        if not _smoke_text_matches(recognized):
            return (
                False,
                "the engine started but did not read the test page correctly "
                f"(expected {SMOKE_TEST_PHRASE!r}, got {recognized[:120]!r}). "
                "The recognition model and its character dictionary are probably "
                "mismatched, which produces confident but wrong text.",
                {},
            )
        return True, "", ready
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return False, f"worker did not respond within {_SMOKE_TEST_TIMEOUT_S}s", {}
    except Exception as exc:
        return False, str(exc), {}
    finally:
        for leftover in (fixture, ndjson):
            with contextlib.suppress(OSError):
                leftover.unlink(missing_ok=True)


def _read_smoke_text(ndjson: Path) -> str:
    """Concatenate the recognized lines the worker wrote for the fixture page."""
    try:
        raw = ndjson.read_text(encoding="utf-8")
    except OSError:
        return ""
    words: list[str] = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a truncated tail is not a failure to read
        for entry in row.get("lines") or []:
            if not isinstance(entry, dict):
                continue
            # The worker writes the long field names; `ocr_cache` stores a
            # compacted {t,c,l} form of the same rows. Accept either so this
            # keeps working whichever side of that boundary it is handed.
            text = entry.get("text") or entry.get("t")
            if text:
                words.append(str(text))
    return " ".join(words)


async def cancel_install() -> bool:
    _cancel.set()
    proc = _active_proc
    if proc is not None:
        with contextlib.suppress(Exception):
            proc.terminate()
    return True


async def uninstall_tier(tier: str | None = None) -> dict[str, Any]:
    """Remove the venv and this tier's own models.

    `ocr_cache` is intentionally untouched, and so is the shared
    `ocr_models_dir()`: that directory is documented - here and in
    `worker/engine.py` - as a slot the *user* drops their own weights into.
    Uninstalling PMA's engine must not delete files PMA never put there. Only
    `ocr_tier_models_dir()`, which a tier downloads into and therefore owns, is
    removed.
    """
    removed = []
    for target in (ocr_env_dir(tier), ocr_tier_models_dir(tier)):
        if target.exists():
            await asyncio.to_thread(shutil.rmtree, target, True)
            removed.append(str(target))
    logger.info(
        "Uninstalled OCR tier %s: %s", tier or "active", ", ".join(removed) or "nothing to remove"
    )
    return {"ok": True, "removed": removed}


async def uninstall_tier1() -> dict[str, Any]:
    """Back-compat alias for the CPU tier."""
    return await uninstall_tier("cpu")
