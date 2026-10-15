"""Provision the opt-in GPU embedding venv, persist the device choice, report status.

Same doctrine as ``app/ocr/registry.py``, whose helpers this reuses: exact pins,
``--no-deps``, ``shell=False``, build in a temp dir and promote with one rename.
The venv holds only onnxruntime-directml and numpy - tokenising and the CPU
fallback stay in Core, and the fp16 model is read from the pinned HF cache
entry Core already verifies, so nothing is copied or downloaded twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from app.config import settings
from app.embeddings.gpu.client import PROTOCOL, GpuEmbedClient
from app.ocr import registry as ocr_registry
from app.ocr.settings import _persist_base

logger = logging.getLogger(__name__)

#: The complete transitive closure of onnxruntime-directml 1.19.2 on win_amd64,
#: taken from OCR's PINNED_DEPS_GPU (which compiles the same runtime) minus the
#: OCR-only packages. --no-deps is mandatory for the same reason it is there:
#: a resolved install can drag in CPU `onnxruntime`, which unpacks over the
#: DirectML build's `onnxruntime/` package and silently drops DmlExecutionProvider.
PINNED_DEPS: tuple[str, ...] = (
    "coloredlogs==15.0.1",
    "flatbuffers==25.12.19",
    "humanfriendly==10.0",
    "mpmath==1.3.0",
    "numpy==1.26.4",
    "onnxruntime-directml==1.19.2",
    "packaging==26.3",
    "protobuf==7.35.1",
    "pyreadline3==3.5.6",
    "sympy==1.14.0",
)

FP16 = "onnx/model_fp16.onnx"
INT8 = "onnx/model_quantized.onnx"
STAMP = ".tier.json"

#: Smoke-test inputs, and how close the worker's fp16 vectors must sit to the
#: shipped CPU INT8 ones: measured min cos 0.9908 over 2000 chunks, while a
#: wrong provider or broken pooling lands far lower.
_SMOKE_TEXTS = (
    "PMA GPU embedding smoke test.",
    "The quick brown fox jumps over the lazy dog.",
    "Mitochondria are the powerhouse of the cell.",
)
_SMOKE_MIN_COS = 0.98

_state = ocr_registry.InstallState()
_state_lock = threading.Lock()
_cancel = threading.Event()


# ── paths and status ─────────────────────────────────────────────────────


def root() -> Path:
    return _persist_base() / "embed_gpu"


def env_dir() -> Path:
    return root() / "env"


def env_python(env: Path | None = None) -> Path:
    env = env or env_dir()
    return env / "Scripts" / "python.exe" if sys.platform == "win32" else env / "bin" / "python"


def worker_file() -> Path:
    """Run in place, not copied into the venv, so an upgrade can never leave a
    stale worker behind. pma.spec ships it as a data file for frozen builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "app" / "embeddings" / "gpu" / "worker.py"  # type: ignore[attr-defined]
    return Path(__file__).with_name("worker.py")


def worker_argv(python: Path | None = None) -> list[str]:
    return [str(python or env_python()), "-I", "-u", str(worker_file())]


def unavailable_reason() -> str:
    if sys.platform != "win32":
        return "GPU embedding needs DirectML, which is only available on Windows."
    return ""


def read_stamp() -> dict[str, Any]:
    try:
        data = json.loads((env_dir() / STAMP).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def is_installed() -> bool:
    return env_python().is_file() and read_stamp().get("protocol") == PROTOCOL


def status(gpu: dict[str, Any] | None = None) -> dict[str, Any]:
    """`gpu` is the live service's view (EmbeddingService.gpu_status())."""
    stamp = read_stamp()
    return {
        "available": not unavailable_reason(),
        "reason": unavailable_reason(),
        "installed": is_installed(),
        "installed_ep": stamp.get("ep"),
        "installed_at": stamp.get("installed_at"),
        "uv_available": ocr_registry.find_uv() is not None,
        # Saved choice vs what this process loaded: a switch applies on restart.
        "device_saved": saved_device() or settings.embedding_device,
        "device_active": settings.embedding_device,
        "worker": gpu,
        "install": _state.as_dict(),
    }


# ── persisted choice ─────────────────────────────────────────────────────


def saved_device() -> str | None:
    try:
        from app.settings_store import SettingsStore

        device = (SettingsStore.read().get("embedding") or {}).get("device")
    except Exception as exc:
        logger.debug("Could not read persisted embedding device: %s", exc)
        return None
    return device if device in ("cpu", "gpu") else None


def load_persisted_device() -> None:
    """Apply the saved device before the embedder loads (main._lifespan)."""
    device = saved_device()
    if device:
        settings.embedding_device = device  # type: ignore[assignment]


def persist_device(device: str) -> None:
    from app.settings_store import SettingsStore

    data = SettingsStore.read()
    data.setdefault("embedding", {})["device"] = device
    SettingsStore.save(data)


# ── install ──────────────────────────────────────────────────────────────


def pinned_model_file(rel: str, *, allow_download: bool) -> Path:
    """Resolve one pinned embedding file in the HF cache and verify its digest."""
    from app.utils.model_integrity import configure_hf_env, load_models_lock, verify_file_sha256

    entry = load_models_lock().get(settings.embedding_model) or {}
    spec = (entry.get("files") or {}).get(rel) or {}
    if not spec.get("sha256"):
        raise ValueError(f"{rel} is not pinned for {settings.embedding_model} in models.lock.json")
    configure_hf_env()
    from huggingface_hub import hf_hub_download

    path = Path(
        hf_hub_download(  # nosec B615 - repo and revision are pinned
            repo_id=entry["repo_id"],
            revision=entry["revision"],
            filename=rel,
            local_files_only=not allow_download,
        )
    )
    if not verify_file_sha256(path, spec["sha256"], label=rel):
        raise ValueError(f"{rel} failed checksum verification")
    return path


def begin_install() -> bool:
    """Arm the state before the HTTP response; False when already running."""
    global _state
    with _state_lock:
        if _state.status == "running":
            return False
        _cancel.clear()
        _state = ocr_registry.InstallState(status="running", step="uv", message="Locating uv...")
        return True


def cancel_install() -> None:
    # ponytail: honoured at step boundaries only; the longest step is a ~1 min pip install.
    _cancel.set()


async def install() -> dict[str, Any]:
    """Provision the venv. Call begin_install() first. Never enables GPU mode."""

    def emit(step: str, pct: int, message: str) -> None:
        if _cancel.is_set():
            raise _CancelledError
        _state.step, _state.pct, _state.message = step, pct, message
        _state.log_tail.append(f"[{step}] {message}")
        logger.info("GPU embed install: %s", message)

    def fail(code: str, message: str) -> dict[str, Any]:
        _state.status, _state.error_code, _state.message = "failed", code, message
        _state.log_tail.append(f"[error] {message}")
        logger.error("GPU embed install failed (%s): %s", code, message)
        return _state.as_dict()

    tmp = root() / f".install-{uuid.uuid4().hex}"
    promoted = False
    try:
        if unavailable_reason():
            return fail("UNAVAILABLE", unavailable_reason())
        uv = ocr_registry.find_uv()
        if not uv:
            return fail(
                "UV_NOT_FOUND", f"uv is required. Install it from {ocr_registry.UV_INSTALL_URL}"
            )
        root().mkdir(parents=True, exist_ok=True)
        # ~150 MB venv plus ~67 MB of fp16 weights if not cached yet.
        ok, detail = ocr_registry._check_free_space(root(), 400 * 1024 * 1024)
        if not ok:
            return fail("NO_DISK_SPACE", detail)

        emit("models", 10, "Fetching and verifying the GPU model files...")
        try:
            fp16 = await asyncio.to_thread(pinned_model_file, FP16, allow_download=True)
            int8 = await asyncio.to_thread(pinned_model_file, INT8, allow_download=True)
            tok = await asyncio.to_thread(pinned_model_file, "tokenizer.json", allow_download=True)
        except Exception as exc:
            return fail("MODEL_FAILED", str(exc))

        emit("venv", 30, "Creating GPU environment...")
        venv_argv = [uv, "venv", "--python", ocr_registry.PYTHON_VERSION, str(tmp)]
        code, out = await asyncio.to_thread(ocr_registry._run, venv_argv, timeout=300)
        if code != 0:
            return fail("VENV_FAILED", f"uv venv failed: {out[-400:]}")

        emit("deps", 45, "Installing DirectML runtime (this can take a minute)...")
        argv = [uv, "pip", "install", "--python", str(env_python(tmp)), "--no-cache", "--no-deps"]
        code, out = await asyncio.to_thread(ocr_registry._run, [*argv, *PINNED_DEPS], timeout=1800)
        if code != 0:
            return fail("DEPS_FAILED", f"dependency install failed: {out[-400:]}")

        emit("verify", 80, "Checking the GPU runs and matches the CPU...")
        ok, detail, ep = await asyncio.to_thread(_smoke_test, env_python(tmp), fp16, int8, tok)
        if not ok:
            return fail("SMOKE_FAILED", detail)

        emit("verify", 95, "Finalizing...")
        (tmp / STAMP).write_text(
            json.dumps({"protocol": PROTOCOL, "ep": ep, "installed_at": ocr_registry._now()}),
            encoding="utf-8",
        )
        shutil.rmtree(env_dir(), ignore_errors=True)
        tmp.replace(env_dir())
        promoted = True
        _state.status, _state.pct, _state.message = "ok", 100, "GPU embedding installed."
        return _state.as_dict()
    except _CancelledError:
        _state.status, _state.message = "cancelled", "Install cancelled."
        return _state.as_dict()
    except Exception as exc:
        return fail("INSTALL_FAILED", str(exc))
    finally:
        if not promoted:
            shutil.rmtree(tmp, ignore_errors=True)


class _CancelledError(Exception):
    pass


def _smoke_test(python: Path, fp16: Path, int8: Path, tok_file: Path) -> tuple[bool, str, str]:
    """Embed fixed strings in the new venv and compare against CPU INT8 here.

    Proves more than "it starts": a worker on the wrong provider or with broken
    pooling would load fine and write vectors from another space into the index.
    """
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(tok_file))
    tok.enable_truncation(max_length=512)
    tok.enable_padding(pad_id=0, pad_token="[PAD]")  # nosec B106 # noqa: S106
    enc = tok.encode_batch(list(_SMOKE_TEXTS))
    ids = np.array([e.ids for e in enc], dtype=np.int64)
    mask = np.array([e.attention_mask for e in enc], dtype=np.int64)

    client = GpuEmbedClient(worker_argv(python), str(fp16))
    try:
        got = client.embed(ids, mask)
    finally:
        client.close()
    if got is None:
        return False, client.last_error or "worker failed", ""

    opts = ort.SessionOptions()
    opts.enable_cpu_mem_arena = False
    cpu = ort.InferenceSession(str(int8), opts, providers=["CPUExecutionProvider"])
    names = {i.name for i in cpu.get_inputs()}
    feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
    hidden = cpu.run(None, {k: v for k, v in feed.items() if k in names})[0].astype(np.float32)
    m = mask[..., None].astype(np.float32)
    ref = (hidden * m).sum(1) / np.maximum(m.sum(1), 1e-9)
    ref /= np.linalg.norm(ref, axis=1, keepdims=True)
    cos = float((got * ref).sum(1).min())
    if cos < _SMOKE_MIN_COS:
        return False, f"GPU vectors differ from CPU (min cos {cos:.4f} < {_SMOKE_MIN_COS})", ""
    return True, "", client.ep
