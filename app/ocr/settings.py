"""Derived paths and tier state for the OCR subsystem.

`app.config.Settings` holds the tunables; this module holds everything derived
from them. Paths live here rather than on `Settings` because pydantic
`BaseSettings` cannot use `@cached_property` - the same reason
`config._extensions_cache` exists.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

#: Identifies the recognition model in ``ocr_cache.model_version``. Changing
#: this invalidates every cached page, which is the intent when the model
#: changes - stale text must never be silently reused under a new engine.
#:
#: PP-OCRv4 mobile, not v5: these are the models bundled with the pinned
#: rapidocr-onnxruntime 1.4.x wheel. v5 ONNX exports exist only in third-party
#: mirrors and target the 2.x engine, which downloads weights at runtime.
MODEL_VERSION = "ppocrv4-mobile"

#: Name of the install stamp written into the venv by the registry.
TIER_STAMP = ".tier.json"

_path_cache: dict[str, Path] = {}


def _persist_base() -> Path:
    """Root for PMA's on-disk state, in whichever mode we're running.

    Derived from ``db_path`` so it tracks portable vs split-brain without
    duplicating the branch in ``config.compute_paths``.
    """
    raw = settings.db_path or ""
    # ":memory:" (tests) has no meaningful parent.
    if not raw or raw == ":memory:":
        return Path("data").resolve()
    return Path(raw).resolve().parent


def ocr_root() -> Path:
    if "root" not in _path_cache:
        _path_cache["root"] = _persist_base() / "ocr"
    return _path_cache["root"]


def ocr_env_dir() -> Path:
    """The provisioned virtualenv. One tier at a time; switching re-provisions."""
    return ocr_root() / "env_cpu"


def ocr_worker_dir() -> Path:
    return ocr_env_dir() / "worker"


def ocr_python() -> Path:
    if sys.platform == "win32":
        return ocr_env_dir() / "Scripts" / "python.exe"
    return ocr_env_dir() / "bin" / "python"


def ocr_models_dir() -> Path:
    return ocr_root() / "models"


def ocr_scratch_dir() -> Path:
    """Where the worker writes per-document NDJSON page results."""
    return ocr_root() / "scratch"


def reset_path_cache() -> None:
    """Drop memoized paths. For tests that repoint ``settings.db_path``."""
    _path_cache.clear()


def worker_source_dir() -> Path:
    """Where ``worker/*.py`` lives *now*, so the registry can copy it.

    Under PyInstaller the sources are real files on disk: ``PMA.spec`` ships
    ``datas=[('app', 'app')]``, and data entries are copied verbatim rather
    than frozen into the PYZ. This is the same mechanism that locates
    ``app/storage/schema.sql`` at runtime.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / "app" / "ocr" / "worker"  # type: ignore[attr-defined]
        if candidate.is_dir():
            return candidate
    return Path(__file__).parent / "worker"


def protocol_source_file() -> Path:
    """Path to ``protocol.py``, which is copied alongside the worker."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / "app" / "ocr" / "protocol.py"  # type: ignore[attr-defined]
        if candidate.is_file():
            return candidate
    return Path(__file__).parent / "protocol.py"


def read_tier_stamp() -> dict:
    """Contents of ``<ocr_env>/.tier.json``, or ``{}`` if absent/corrupt."""
    stamp = ocr_env_dir() / TIER_STAMP
    try:
        data: dict = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_tier_installed() -> bool:
    """True when a usable venv exists whose stamp matches this build's protocol.

    A protocol bump makes an existing venv unusable rather than subtly wrong,
    so we report not-installed and let the registry re-sync the worker files.
    """
    if not ocr_python().is_file():
        return False
    stamp = read_tier_stamp()
    if not stamp:
        return False

    from app.ocr.protocol import PROTOCOL_VERSION

    return stamp.get("protocol") == PROTOCOL_VERSION


def preproc_hash() -> str:
    """Fingerprint of the rasterization settings a cached page was produced under.

    Part of the ``ocr_cache`` primary key: changing DPI must miss cache rather
    than return text rendered at the old resolution.
    """
    payload = json.dumps(
        {"dpi": settings.ocr_dpi, "grayscale": True, "deskew": False},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def load_persisted_state() -> None:
    """Reconcile `settings.ocr_*` with what is actually on disk.

    `PMA_OCR_TIER` in the environment only ever describes a fresh machine. Once
    a venv exists, *it* is the install state - so an installed tier is adopted
    here rather than requiring the user to also edit .env. The enable toggle is
    a user choice, so it comes from data/settings.json.

    Without this, installing OCR worked until the next restart and then
    silently stopped: config re-read tier="none" from env and normalize_ocr
    dutifully forced ocr_enabled back to False.
    """
    installed = is_tier_installed()
    if installed and settings.ocr_tier == "none":
        settings.ocr_tier = "cpu"
    elif not installed:
        settings.ocr_tier = "none"
        settings.ocr_enabled = False
        return

    try:
        from app.settings_store import SettingsStore

        stored = (SettingsStore.read().get("ocr") or {}).get("enabled")
    except Exception as exc:
        logger.debug("Could not read persisted OCR state: %s", exc)
        return

    if stored is not None:
        settings.ocr_enabled = bool(stored)


def persist_enabled(enabled: bool) -> None:
    """Remember the user's OCR toggle across restarts."""
    try:
        from app.settings_store import SettingsStore

        data = SettingsStore.read()
        data.setdefault("ocr", {})["enabled"] = bool(enabled)
        SettingsStore.save(data)
    except Exception as exc:
        logger.warning("Could not persist OCR enabled state: %s", exc)


def ensure_dirs() -> None:
    """Create the OCR directory tree. Safe to call repeatedly."""
    for directory in (ocr_root(), ocr_models_dir(), ocr_scratch_dir()):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Could not create OCR directory %s: %s", directory, exc)
