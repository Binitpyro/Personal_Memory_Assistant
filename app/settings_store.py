import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path("data/settings.json")
CURRENT_SCHEMA_VERSION = 2


class SettingsStore:
    """Centralized, thread-safe atomic accessor for data/settings.json."""

    @staticmethod
    def read() -> dict[str, Any]:
        """Reads settings from data/settings.json.
        
        Returns empty dict if file does not exist.
        Raises OSError or json.JSONDecodeError if file is corrupted/unreadable
        so callers do not silently overwrite existing settings on error.
        """
        if not SETTINGS_PATH.exists():
            return {}

        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("data/settings.json does not contain a JSON object")
                return {}
            return data
        except Exception as e:
            logger.error("Failed to read settings from %s: %s", SETTINGS_PATH, e)
            raise

    @staticmethod
    def save(data: dict[str, Any]) -> None:
        """Atomically saves data to data/settings.json with schema versioning.
        
        Includes retry logic for Windows file lock collisions during os.replace.
        """
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        data["schema_version"] = CURRENT_SCHEMA_VERSION

        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=str(SETTINGS_PATH.parent),
                delete=False,
                encoding="utf-8",
            ) as tf:
                temp_file = Path(tf.name)
                json.dump(data, tf, indent=2)
                tf.flush()
                os.fsync(tf.fileno())

            # Atomic replace with retry for Windows PermissionError
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    os.replace(temp_file, SETTINGS_PATH)
                    temp_file = None
                    break
                except PermissionError as pe:
                    if attempt == max_retries - 1:
                        raise pe
                    time.sleep(0.1)
        finally:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass
