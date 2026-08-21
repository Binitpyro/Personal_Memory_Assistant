"""Shared pinned-model loading and checksum verification.

Extracted from `EmbeddingService` so the OCR registry verifies its model
bundle with the same code rather than a second copy. Duplicating a
constant-time digest comparison is exactly the kind of thing that drifts.

`models.lock.json` schema::

    {"schema": 1,
     "models": {
       "<logical-name>": {
         "repo_id": "...", "revision": "<commit sha>", "family": "embedding|ocr",
         "files": {"<repo-relative path>": {"sha256": "...", "size_bytes": 123}}
       }}}
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

#: Set before huggingface_hub is imported anywhere. Defaults there are
#: privacy-hostile for a local-first product: the user agent carries usage
#: telemetry, and an implicit token means a user who happens to have an HF
#: credential on disk has every model fetch attributed to their identity.
#: Neither is a tradeoff we get to make on their behalf - CLAUDE.md §1.4.
_HF_PRIVACY_ENV = {
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",  # nosec B105
}


def configure_hf_env() -> None:
    """Mute huggingface_hub telemetry and implicit auth. Call before importing it.

    Uses setdefault so an operator who deliberately set one of these keeps their
    value; we only supply the default the library should have had.
    """
    for key, value in _HF_PRIVACY_ENV.items():
        os.environ.setdefault(key, value)


def models_lock_path() -> Path:
    """Locate models.lock.json, frozen build or source tree.

    PyInstaller places it at the bundle root; see PMA.spec `datas`.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / "models.lock.json"  # type: ignore[attr-defined]
        if candidate.exists():
            return candidate
    return Path(__file__).parent.parent.parent / "models.lock.json"


def load_models_lock(*, family: str | None = None) -> dict[str, Any]:
    """Return the `models` map, optionally filtered to one family.

    Fails closed: a missing or unparseable lockfile raises unless
    `embedding_allow_unpinned` is set. An unpinned model is an unverified
    download, so silence here would defeat the whole mechanism.
    """
    lock_file = models_lock_path()

    if not lock_file.exists():
        if not settings.embedding_allow_unpinned:
            raise ValueError(
                f"models.lock.json missing at {lock_file} and embedding_allow_unpinned=False."
            )
        return {}

    try:
        with open(lock_file, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error("Failed to parse models.lock.json at %s: %s", lock_file, e)
        if not settings.embedding_allow_unpinned:
            raise ValueError(
                f"models.lock.json invalid or missing and unpinned loading disallowed: {e}"
            ) from e
        return {}

    models: dict[str, Any] = data.get("models", {})
    if not models and not settings.embedding_allow_unpinned:
        raise ValueError(f"models.lock.json at {lock_file} contains no models.")

    if family:
        return {k: v for k, v in models.items() if (v or {}).get("family") == family}
    return models


def verify_file_sha256(path: Path, expected_sha256: str, *, label: str = "") -> bool:
    """Constant-time SHA256 check of one file. Never raises."""
    name = label or path.name

    if not path.exists() or path.stat().st_size == 0:
        logger.error("Pinned file missing or empty: %s", path)
        return False

    hasher = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        if not hmac.compare_digest(digest, expected_sha256):
            logger.error(
                "Integrity FAILED for %s: expected %s..., got %s...",
                name,
                expected_sha256[:16],
                digest[:16],
            )
            return False
        logger.info("Integrity verified: %s (%s...)", name, digest[:16])
        return True
    except Exception as e:
        logger.error("Failed to calculate SHA256 for %s: %s", path, e)
        return False
