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


#: Env dir used when nothing is installed yet and no tier is named. Keeps a
#: fresh machine looking exactly where it always has.
_FALLBACK_TIER = "cpu"


def _resolve_tier(tier: str | None) -> str:
    """Tier to build a path for: explicit, else the active one, else the default."""
    if tier:
        return tier
    active = (settings.ocr_tier or "").strip().lower()
    return active if active and active != "none" else _FALLBACK_TIER


def ocr_env_dir(tier: str | None = None) -> Path:
    """The provisioned virtualenv for a tier. One tier at a time; switching
    re-provisions.

    Parameterised rather than hardcoded to ``env_cpu`` so a second tier gets its
    own venv - the ONNX Runtime builds are mutually exclusive within one
    interpreter, so they cannot share. ``env_cpu`` is unchanged for tier "cpu",
    so existing installs keep working untouched.
    """
    return ocr_root() / f"env_{_resolve_tier(tier)}"


def ocr_worker_dir(tier: str | None = None) -> Path:
    return ocr_env_dir(tier) / "worker"


def ocr_python(tier: str | None = None) -> Path:
    if sys.platform == "win32":
        return ocr_env_dir(tier) / "Scripts" / "python.exe"
    return ocr_env_dir(tier) / "bin" / "python"


def ocr_models_dir() -> Path:
    """User drop-in weights, shared across tiers.

    Deliberately not tier-scoped: `registry.MODEL_TARGETS` and
    `worker/engine.py` both document this as a slot the *user* fills, and
    re-homing it would orphan anything already dropped there. A tier that
    downloads its own weights must use `ocr_tier_models_dir()` instead, so it
    cannot be picked up by a different tier's engine.
    """
    return ocr_root() / "models"


def ocr_tier_models_dir(tier: str | None = None) -> Path:
    """Weights owned by one tier, never shared. Safe to delete on uninstall."""
    return ocr_root() / "models" / _resolve_tier(tier)


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


def read_tier_stamp(tier: str | None = None) -> dict:
    """Contents of ``<ocr_env>/.tier.json``, or ``{}`` if absent/corrupt."""
    stamp = ocr_env_dir(tier) / TIER_STAMP
    try:
        data: dict = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_tier_installed(tier: str | None = None) -> bool:
    """True when this tier is usable.

    For engine tiers that means a venv whose stamp matches this build's
    protocol - a protocol bump makes an existing venv unusable rather than
    subtly wrong, so we report not-installed and let the registry re-sync.

    The VLM tier has no venv by design; it is usable once the user has chosen a
    provider and model. Answering "not installed" for it would have left the
    drain loop refusing to run forever while documents kept enqueuing.
    """
    if (tier or _resolve_tier(None)) == VLM_TIER:
        return bool(vlm_selection())

    if not ocr_python(tier).is_file():
        return False
    stamp = read_tier_stamp(tier)
    if not stamp:
        return False

    from app.ocr.protocol import PROTOCOL_VERSION

    return stamp.get("protocol") == PROTOCOL_VERSION


#: Tier that runs no local engine at all - it sends page images to a vision
#: model in the user's own Ollama or LM Studio. "Installed" therefore cannot
#: mean "a venv exists"; it means a provider and model have been chosen.
VLM_TIER = "vlm"

#: DirectML tier. Named here because callers need to special-case it on
#: memory grounds: a resident session holds VRAM, and Tier 2 measures
#: ~5.2 GB against a 4 GB target.
GPU_TIER = "gpu"


def vlm_selection() -> dict[str, str]:
    """The user's chosen Tier 3 provider and model, or empty when unset."""
    try:
        from app.settings_store import SettingsStore

        stored = (SettingsStore.read().get("ocr") or {}).get("vlm") or {}
    except Exception as exc:
        logger.debug("Could not read persisted VLM selection: %s", exc)
        return {}

    provider = str(stored.get("provider") or "").strip()
    model = str(stored.get("model") or "").strip()
    if not provider or not model:
        return {}
    return {"provider": provider, "model": model}


def persist_vlm_selection(provider: str, model: str) -> None:
    try:
        from app.settings_store import SettingsStore

        data = SettingsStore.read()
        data.setdefault("ocr", {})["vlm"] = {"provider": provider, "model": model}
        SettingsStore.save(data)
    except Exception as exc:
        logger.warning("Could not persist VLM selection: %s", exc)


def persist_active_tier(tier: str) -> None:
    """Remember the user's active OCR tier across restarts."""
    try:
        from app.settings_store import SettingsStore

        data = SettingsStore.read()
        data.setdefault("ocr", {})["tier"] = tier
        SettingsStore.save(data)
    except Exception as exc:
        logger.warning("Could not persist active OCR tier: %s", exc)


def detect_installed_tier() -> str:
    """Name of the tier actually provisioned/configured, or "" if none is.

    Checks persisted tier preference first so a user's selection (e.g. VLM or GPU)
    is respected even if another engine venv exists.
    """
    try:
        from app.settings_store import SettingsStore

        stored_tier = str((SettingsStore.read().get("ocr") or {}).get("tier") or "").strip().lower()
        if stored_tier and stored_tier != "none" and is_tier_installed(stored_tier):
            return stored_tier
    except Exception as exc:
        logger.debug("Could not read preferred OCR tier: %s", exc)

    root = ocr_root()
    try:
        candidates = sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return ""

    for name in candidates:
        if not name.startswith("env_"):
            continue
        tier = name[len("env_") :]
        if tier and is_tier_installed(tier):
            return tier
    return VLM_TIER if vlm_selection() else ""


#: Execution provider that needs no marker in the cache key. Tier 1 has always
#: run on it, so leaving it unqualified keeps every already-cached page valid.
_DEFAULT_EP = "CPUExecutionProvider"


def engine_identity(model_version: str | None, ep: str | None = None) -> str:
    """The `ocr_cache.model_version` value for a given engine.

    Part of the cache primary key, so two engines that produce different text
    must never map to the same string. DirectML runs many ops in fp16 where CPU
    uses fp32, so the execution provider is part of the identity, not a detail.

    Deliberately *not* symmetric: a CPU run stays plain ``"ppocrv4-mobile"``
    rather than ``"ppocrv4-mobile@cpu"``. Qualifying it would change the key for
    every page cached before this existed and silently force a full re-OCR of
    the corpus - work the user already paid for.
    """
    base = (model_version or MODEL_VERSION).strip() or MODEL_VERSION
    provider = (ep or "").strip()
    if not provider or provider == _DEFAULT_EP:
        return base
    # "DmlExecutionProvider" -> "dml"; the full name adds length, not meaning.
    short = provider.removesuffix("ExecutionProvider").lower() or provider.lower()
    return f"{base}@{short}"


def expected_engine_identity() -> str:
    """Identity the installed tier *should* report, read from the install stamp.

    The cache is consulted before any worker is spawned, so the read path cannot
    wait for a `ready` message. The stamp is written only after the installer's
    smoke test passes, so it is the best available statement of what this venv
    will load. Writes are gated on the worker's actual report instead - see
    `OcrManager._engine_identity`.
    """
    # The VLM tier has no venv and therefore no stamp; its identity is the
    # model the user picked. Two different vision models produce materially
    # different transcriptions, so switching must miss cache rather than serve
    # the previous model's text.
    if _resolve_tier(None) == VLM_TIER:
        selection = vlm_selection()
        if selection:
            return f"vlm:{selection['provider']}:{selection['model']}"

    stamp = read_tier_stamp()
    return engine_identity(stamp.get("model_version"), stamp.get("ep"))


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
    # Whatever is on disk wins, and it names itself rather than being assumed
    # to be "cpu" - the adopted tier ends up in the OCR cache key.
    detected = detect_installed_tier()
    if not detected:
        settings.ocr_tier = "none"
        settings.ocr_enabled = False
        return
    if settings.ocr_tier != detected:
        settings.ocr_tier = detected

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
