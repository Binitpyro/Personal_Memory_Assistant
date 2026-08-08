"""Registry: uv discovery, worker file sync, and fail-closed install guards.

Never provisions a real venv and never touches the network.
"""

import json
import sys
from pathlib import Path

from app.ocr import registry
from app.ocr.protocol import PROTOCOL_VERSION

WORKER_SRC = Path(__file__).parent.parent / "app" / "ocr" / "worker"


# ── uv discovery ─────────────────────────────────────────────────────────


def test_find_uv_prefers_path(monkeypatch):
    monkeypatch.setattr(registry.shutil, "which", lambda name: r"C:\tools\uv.exe")
    assert registry.find_uv() == r"C:\tools\uv.exe"


def test_find_uv_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(registry.shutil, "which", lambda name: None)
    monkeypatch.setattr(registry, "_uv_candidates", lambda: ())
    assert registry.find_uv() is None


def test_find_uv_checks_known_install_locations(monkeypatch, tmp_path):
    exe = tmp_path / ("uv.exe" if sys.platform == "win32" else "uv")
    exe.write_text("stub")
    monkeypatch.setattr(registry.shutil, "which", lambda name: None)
    monkeypatch.setattr(registry, "_uv_candidates", lambda: (str(exe),))
    assert registry.find_uv() == str(exe)


def test_empty_env_root_never_yields_a_relative_path(monkeypatch):
    """An empty %LOCALAPPDATA% would otherwise match a file in the CWD."""
    for var in ("LOCALAPPDATA", "USERPROFILE", "HOME"):
        monkeypatch.setenv(var, "")
    assert registry._uv_candidates() == ()
    assert registry._env_path("", "uv", "uv.exe") is None
    assert registry._env_path(None, "uv") is None


# ── worker file sync ─────────────────────────────────────────────────────


def test_sync_copies_worker_and_protocol(tmp_path):
    target = tmp_path / "worker"
    assert registry.sync_worker_files(target) is True

    copied = {p.name for p in target.glob("*.py")}
    expected = {p.name for p in WORKER_SRC.glob("*.py") if p.name != "__init__.py"}
    assert expected <= copied
    assert "protocol.py" in copied
    # __init__.py must NOT be copied: the files are run flat, by path.
    assert "__init__.py" not in copied


def test_sync_writes_a_stamp_with_the_protocol_version(tmp_path):
    target = tmp_path / "worker"
    registry.sync_worker_files(target)

    stamp = json.loads((target / ".stamp").read_text(encoding="utf-8"))
    assert stamp["protocol"] == PROTOCOL_VERSION
    assert "protocol.py" in stamp["files"]


def test_sync_is_a_noop_when_the_stamp_matches(tmp_path):
    target = tmp_path / "worker"
    registry.sync_worker_files(target)
    marker = target / "protocol.py"
    original_mtime = marker.stat().st_mtime_ns

    registry.sync_worker_files(target)
    assert marker.stat().st_mtime_ns == original_mtime


def test_sync_recopies_when_the_stamp_is_stale(tmp_path):
    """A PMA upgrade over an existing venv must refresh the worker."""
    target = tmp_path / "worker"
    registry.sync_worker_files(target)
    (target / "protocol.py").write_text("# tampered", encoding="utf-8")
    (target / ".stamp").write_text(
        json.dumps({"protocol": PROTOCOL_VERSION, "files": {"protocol.py": "stale"}}),
        encoding="utf-8",
    )

    registry.sync_worker_files(target)

    assert "# tampered" not in (target / "protocol.py").read_text(encoding="utf-8")


def test_sync_fails_loudly_when_sources_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "worker_source_dir", lambda: tmp_path / "nope")
    assert registry.sync_worker_files(tmp_path / "worker") is False


def test_sweep_removes_stale_install_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "ocr_root", lambda: tmp_path)
    (tmp_path / ".install-abc123").mkdir()
    (tmp_path / ".install-def456").mkdir()
    (tmp_path / "env_cpu").mkdir()

    assert registry.sweep_stale_installs() == 2
    assert (tmp_path / "env_cpu").is_dir()


# ── install guards ───────────────────────────────────────────────────────


async def test_install_without_uv_spawns_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "ocr_root", lambda: tmp_path)
    monkeypatch.setattr(registry, "find_uv", lambda: None)

    def _explode(*a, **kw):
        raise AssertionError("install must not spawn a process without uv")

    monkeypatch.setattr(registry, "_run", _explode)

    result = await registry.install_tier1()

    assert result["status"] == "failed"
    assert result["error_code"] == "UV_NOT_FOUND"


async def test_install_cleans_up_its_temp_dir_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "ocr_root", lambda: tmp_path)
    monkeypatch.setattr(registry, "find_uv", lambda: "uv")
    monkeypatch.setattr(registry, "_run", lambda *a, **kw: (1, "uv venv exploded"))

    result = await registry.install_tier1()

    assert result["status"] == "failed"
    assert not list(tmp_path.glob(".install-*")), "partial venv was left behind"


def test_ocr_models_ride_in_on_the_pinned_wheel():
    """The engine dependency is what delivers the models.

    rapidocr-onnxruntime 1.4.x resolves its ONNX models from package-relative
    paths, so pinning the wheel pins the weights. If this pin is ever moved to
    the 2.x `rapidocr` package, models start downloading from a CDN at first
    run and the registry needs a verification step again.
    """
    engine_pins = [p for p in registry.PINNED_DEPS if p.startswith("rapidocr")]
    assert engine_pins == ["rapidocr-onnxruntime==1.4.4"]
    assert all("==" in pin or "<" in pin for pin in registry.PINNED_DEPS)


def test_lockfile_has_no_ocr_family_entry():
    """Nothing to pin, so nothing should claim to be pinned."""
    from app.utils.model_integrity import load_models_lock

    assert load_models_lock(family="ocr") == {}


# ── install state across restarts ─────────────────────────────────────────


def test_installed_tier_is_adopted_even_when_env_says_none(monkeypatch, tmp_path):
    """The venv on disk outranks PMA_OCR_TIER.

    Regression guard: installing OCR used to work until the next restart, then
    silently stop - config re-read tier="none" from the environment and
    normalize_ocr forced ocr_enabled back to False.
    """
    from app.config import settings
    from app.ocr import settings as ocr_settings

    monkeypatch.setattr(ocr_settings, "is_tier_installed", lambda: True)
    monkeypatch.setattr(settings, "ocr_tier", "none")
    monkeypatch.setattr(settings, "ocr_enabled", False)

    from app.settings_store import SettingsStore

    monkeypatch.setattr(SettingsStore, "read", staticmethod(lambda: {"ocr": {"enabled": True}}))

    ocr_settings.load_persisted_state()

    assert settings.ocr_tier == "cpu"
    assert settings.ocr_enabled is True


def test_uninstalled_tier_forces_ocr_off(monkeypatch):
    """A stale PMA_OCR_ENABLED=true must not survive an uninstall."""
    from app.config import settings
    from app.ocr import settings as ocr_settings

    monkeypatch.setattr(ocr_settings, "is_tier_installed", lambda: False)
    monkeypatch.setattr(settings, "ocr_tier", "cpu")
    monkeypatch.setattr(settings, "ocr_enabled", True)

    ocr_settings.load_persisted_state()

    assert settings.ocr_tier == "none"
    assert settings.ocr_enabled is False


def test_persist_enabled_round_trips(monkeypatch, tmp_path):
    from app.ocr import settings as ocr_settings
    from app.settings_store import SettingsStore

    monkeypatch.setattr("app.settings_store.SETTINGS_PATH", tmp_path / "settings.json")

    ocr_settings.persist_enabled(True)
    assert SettingsStore.read()["ocr"]["enabled"] is True

    ocr_settings.persist_enabled(False)
    assert SettingsStore.read()["ocr"]["enabled"] is False


def test_a_broken_settings_file_does_not_break_boot(monkeypatch):
    from app.config import settings
    from app.ocr import settings as ocr_settings
    from app.settings_store import SettingsStore

    monkeypatch.setattr(ocr_settings, "is_tier_installed", lambda: True)
    monkeypatch.setattr(settings, "ocr_tier", "none")

    def _explode():
        raise OSError("settings.json is a directory somehow")

    monkeypatch.setattr(SettingsStore, "read", staticmethod(_explode))

    ocr_settings.load_persisted_state()  # must not raise
    assert settings.ocr_tier == "cpu"


def test_tier_status_is_readable_with_nothing_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "ocr_root", lambda: tmp_path)
    status = registry.tier_status()
    assert status["installed"] is False
    assert "uv_available" in status
