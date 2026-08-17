"""Registry: uv discovery, worker file sync, and fail-closed install guards.

Never provisions a real venv and never touches the network.
"""

import json
import sys
import types
from pathlib import Path

import pytest

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


def test_free_space_preflight_refuses_before_doing_work(monkeypatch, tmp_path):
    """A full disk must fail in seconds, not as a pip traceback minutes later."""
    import collections

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(registry.shutil, "disk_usage", lambda p: usage(0, 0, 1024))

    ok, detail = registry._check_free_space(tmp_path, 500 * 1024 * 1024)
    assert ok is False
    assert "MB needed" in detail

    monkeypatch.setattr(
        registry.shutil, "disk_usage", lambda p: usage(0, 0, 50 * 1024 * 1024 * 1024)
    )
    assert registry._check_free_space(tmp_path, 500 * 1024 * 1024)[0] is True


def test_free_space_preflight_does_not_block_when_it_cannot_measure(monkeypatch, tmp_path):
    """Not knowing the free space is not a reason to refuse the install."""

    def boom(_p):
        raise OSError("no such device")

    monkeypatch.setattr(registry.shutil, "disk_usage", boom)
    assert registry._check_free_space(tmp_path, 1) == (True, "")


def test_model_fetch_caches_inside_our_own_tree(monkeypatch, tmp_path):
    """The HF default cache lives outside `data/` and uninstall cannot reclaim it.

    On Windows the hub copies rather than symlinks, so the default would leave a
    second ~194 MB the user can neither find nor remove.
    """
    monkeypatch.setattr(registry, "ocr_root", lambda: tmp_path)
    monkeypatch.setattr(registry, "_check_free_space", lambda *a: (True, ""))
    src = tmp_path / "blob.onnx"
    src.write_bytes(b"weights")
    seen: dict = {}

    monkeypatch.setattr(
        "app.utils.model_integrity.load_models_lock",
        lambda family=None: {
            "PP-OCRv4-server": {
                "repo_id": "r",
                "revision": "v",
                "files": {
                    "PP-OCRv4/ch_PP-OCRv4_det_server_infer.onnx": {
                        "sha256": __import__("hashlib").sha256(b"weights").hexdigest(),
                        "size_bytes": 7,
                    },
                    "PP-OCRv4/ch_PP-OCRv4_rec_server_infer.onnx": {
                        "sha256": __import__("hashlib").sha256(b"weights").hexdigest(),
                        "size_bytes": 7,
                    },
                },
            }
        },
    )

    def fake_download(**kw):
        seen["cache_dir"] = kw.get("cache_dir")
        return str(src)

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", types.SimpleNamespace(hf_hub_download=fake_download)
    )

    ok, detail = registry._fetch_tier_models("gpu", tmp_path / "models" / "gpu")

    assert ok, detail
    assert seen["cache_dir"], "must not use the shared HF cache"
    assert Path(seen["cache_dir"]).is_relative_to(tmp_path)
    assert not Path(seen["cache_dir"]).exists(), "scratch cache must be reclaimed"
    assert (tmp_path / "models" / "gpu" / "det.onnx").read_bytes() == b"weights"


def test_smoke_fixture_is_a_readable_one_page_pdf(tmp_path):
    """The self-test fixture must be a real PDF, not merely plausible bytes.

    Hand-built because the OCR venv has no PDF writer and adding one for a
    self-test would enlarge every install.
    """
    pypdf = pytest.importorskip("pypdf")

    pdf = registry._smoke_test_pdf(tmp_path / "smoke.pdf")
    reader = pypdf.PdfReader(str(pdf))

    assert len(reader.pages) == 1
    assert registry.SMOKE_TEST_PHRASE in reader.pages[0].extract_text()


def test_smoke_matcher_catches_a_wrong_dictionary_but_tolerates_a_typo():
    """The gate targets confident nonsense, not imperfect accuracy.

    A recognition model paired with the wrong character dictionary loads without
    error and returns text from the wrong alphabet - that must fail the install.
    A single mis-read glyph is a quality issue and must not.
    """
    assert registry._smoke_text_matches(registry.SMOKE_TEST_PHRASE)
    assert registry._smoke_text_matches("PMA 0CR 12345"), "one bad glyph must still pass"
    assert not registry._smoke_text_matches("你好世界汉字"), "wrong dictionary must fail"
    assert not registry._smoke_text_matches(""), "no text at all must fail"


def test_smoke_text_reader_matches_the_format_the_worker_actually_writes(tmp_path):
    """Field names are `text`/`conf`, verified against a live worker.

    `ocr_cache` stores a compacted {t,c,l} form of the same rows, and reading
    the wire format with the cache's names silently produced empty text - which
    looked exactly like a failed OCR. Both spellings are accepted now.
    """
    ndjson = tmp_path / "s.ndjson"
    ndjson.write_text(
        '{"page": 0, "lines": [{"text": "PMA OCR 12345", "conf": 0.983, "low": false}],'
        ' "mean_conf": 0.983, "ms": 1709}\n',
        encoding="utf-8",
    )
    assert registry._read_smoke_text(ndjson) == "PMA OCR 12345"
    assert registry._smoke_text_matches(registry._read_smoke_text(ndjson))


def test_smoke_text_reader_tolerates_a_truncated_ndjson(tmp_path):
    """A killed worker leaves a partial last line; that is not a read failure."""
    ndjson = tmp_path / "s.ndjson"
    ndjson.write_text(
        '{"page":0,"lines":[{"text":"PMA","conf":0.9}]}\n{"page":1,"lines":[{"text":"OCR"',
        encoding="utf-8",
    )
    assert "PMA" in registry._read_smoke_text(ndjson)

    assert registry._read_smoke_text(tmp_path / "missing.ndjson") == ""


def test_gpu_tier_never_pins_the_cpu_onnxruntime():
    """The DirectML build and the CPU build cannot share an interpreter.

    Both distributions unpack into the same `onnxruntime/` package, so one
    overwrites the other's DLLs. Verified with `uv pip compile`: the naive swap
    resolves to onnxruntime-directml==1.19.2 *and* an unpinned
    onnxruntime==1.28.0, which is why the GPU tier must install --no-deps.
    """
    gpu_deps, skip_resolution = registry.TIER_DEPS["gpu"]

    assert skip_resolution is True, "--no-deps is mandatory, not stylistic"
    assert "onnxruntime-directml==1.19.2" in gpu_deps
    assert not [d for d in gpu_deps if d.startswith("onnxruntime==")]
    # --no-deps means nothing resolves the closure for us, so it must be exact.
    assert all("==" in dep for dep in gpu_deps)
    # rapidocr's own requirements must all be present, since nothing adds them.
    for required in ("opencv-python", "pyclipper", "shapely", "pillow", "numpy", "tqdm"):
        assert [d for d in gpu_deps if d.startswith(required)], f"{required} missing"


def test_gpu_tier_is_refused_off_windows(monkeypatch):
    """DirectML publishes win_amd64 wheels only.

    A "GPU" tier elsewhere would run 194 MB of server weights on the CPU -
    strictly slower than Tier 1 - so it must be refused, not silently degraded.
    """
    monkeypatch.setattr(registry.sys, "platform", "linux")
    assert registry.unavailable_reason("gpu")
    assert registry.unavailable_reason("cpu") == ""

    monkeypatch.setattr(registry.sys, "platform", "win32")
    assert registry.unavailable_reason("gpu") == ""


async def test_install_refuses_an_unavailable_tier(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "ocr_root", lambda: tmp_path)
    monkeypatch.setattr(registry.sys, "platform", "linux")
    spawned = []
    monkeypatch.setattr(registry, "find_uv", lambda: "uv")
    monkeypatch.setattr(registry, "_run", lambda *a, **k: spawned.append(a) or (0, ""))

    result = await registry.install_tier("gpu")

    assert result["status"] == "failed"
    assert result["error_code"] == "TIER_UNAVAILABLE"
    assert not spawned, "must refuse before provisioning anything"


async def test_install_refuses_an_unknown_tier(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "ocr_root", lambda: tmp_path)
    result = await registry.install_tier("quantum")
    assert result["error_code"] == "UNKNOWN_TIER"


def test_model_fetch_rejects_a_bad_digest(monkeypatch, tmp_path):
    """A file that fails verification must never reach the name the engine loads.

    `_custom_model_paths()` only checks that the path exists, and nothing sweeps
    this directory, so a truncated det.onnx placed before verification would be
    loaded on every subsequent start.
    """
    bad = tmp_path / "downloaded.onnx"
    bad.write_bytes(b"not the real weights")

    monkeypatch.setattr(
        "app.utils.model_integrity.load_models_lock",
        lambda family=None: {
            "PP-OCRv4-server": {
                "repo_id": "SWHL/RapidOCR",
                "revision": "deadbeef",
                "files": {
                    "PP-OCRv4/ch_PP-OCRv4_det_server_infer.onnx": {"sha256": "00" * 32},
                    "PP-OCRv4/ch_PP-OCRv4_rec_server_infer.onnx": {"sha256": "11" * 32},
                },
            }
        },
    )
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        types.SimpleNamespace(hf_hub_download=lambda **kw: str(bad)),
    )

    dest = tmp_path / "models" / "gpu"
    ok, detail = registry._fetch_tier_models("gpu", dest)

    assert ok is False
    assert "checksum" in detail
    assert not (dest / "det.onnx").exists(), "unverified bytes must not be placed"
    assert not list(dest.glob("*.partial")), "staging file must be cleaned up"


def test_opencv_pin_matches_the_distribution_rapidocr_requires():
    """Must be opencv-python, never opencv-python-headless.

    rapidocr-onnxruntime 1.4.4 requires `opencv-python>=4.5.1.48`. The headless
    build is a separate distribution that does not satisfy that, so pinning it
    installed *both* - verified with `uv pip compile`, which resolved
    opencv-python-headless==4.10.0.84 alongside an unpinned
    opencv-python==4.11.0.86. They ship the same `cv2`, so one overwrote the
    other and the effective version floated.
    """
    opencv_pins = [p for p in registry.PINNED_DEPS if p.startswith("opencv")]
    assert opencv_pins == ["opencv-python==4.10.0.84"]


def test_ocr_lockfile_entries_are_fully_pinned():
    """Every OCR weight we fetch must be digest-pinned before it can be used.

    Replaces `test_lockfile_has_no_ocr_family_entry`, which asserted the family
    was empty. That was correct while Tier 1's weights rode in on the pinned
    wheel and nothing was downloaded; the GPU tier fetches PP-OCRv4 *server*
    weights, so the assertion now has to be that they are pinned rather than
    that they are absent.
    """
    from app.utils.model_integrity import load_models_lock

    ocr_models = load_models_lock(family="ocr")
    assert ocr_models, "the GPU tier downloads weights, so they must be pinned"

    for name, entry in ocr_models.items():
        assert entry.get("repo_id"), f"{name} has no repo_id"
        # A branch name would let the bytes change under a fixed lockfile.
        assert len(entry.get("revision", "")) == 40, f"{name} revision is not a commit sha"
        assert entry.get("files"), f"{name} pins no files"
        for path, spec in entry["files"].items():
            assert len(spec.get("sha256", "")) == 64, f"{name}:{path} has no sha256"
            assert spec.get("size_bytes", 0) > 0, f"{name}:{path} has no size"


def test_every_downloaded_tier_model_is_in_the_lockfile():
    """The fetch map and the lockfile must not drift apart."""
    from app.utils.model_integrity import load_models_lock

    ocr_models = load_models_lock(family="ocr")
    for tier, (entry_name, wanted) in registry._TIER_MODEL_LOCK.items():
        entry = ocr_models.get(entry_name)
        assert entry, f"tier {tier} fetches {entry_name}, which is not pinned"
        for repo_path in wanted:
            assert repo_path in entry["files"], f"{repo_path} is fetched but not pinned"


async def test_uninstall_keeps_user_supplied_weights(monkeypatch, tmp_path):
    """Uninstalling the engine must not delete files PMA never put there.

    `ocr_models_dir()` is documented - in registry.MODEL_TARGETS and in
    worker/engine.py - as a slot the *user* drops their own weights into.
    Uninstall used to rmtree it wholesale.
    """
    monkeypatch.setattr(registry, "ocr_root", lambda: tmp_path)
    monkeypatch.setattr(registry, "ocr_env_dir", lambda tier=None: tmp_path / "env_cpu")
    monkeypatch.setattr(
        registry, "ocr_tier_models_dir", lambda tier=None: tmp_path / "models" / "cpu"
    )

    (tmp_path / "env_cpu").mkdir()
    tier_models = tmp_path / "models" / "cpu"
    tier_models.mkdir(parents=True)
    (tier_models / "det.onnx").write_bytes(b"downloaded by pma")
    user_drop = tmp_path / "models" / "rec.onnx"
    user_drop.write_bytes(b"supplied by the user")

    result = await registry.uninstall_tier1()

    assert result["ok"] is True
    assert not (tmp_path / "env_cpu").exists()
    assert not tier_models.exists(), "the tier's own download should go"
    assert user_drop.read_bytes() == b"supplied by the user", "user weights must survive"


def test_detect_installed_tier_is_empty_when_nothing_is_provisioned(monkeypatch, tmp_path):
    from app.ocr import settings as ocr_settings

    monkeypatch.setattr(ocr_settings, "ocr_root", lambda: tmp_path)
    assert ocr_settings.detect_installed_tier() == ""

    # A directory alone is not an install - it needs a protocol-matching stamp,
    # which only the installer writes.
    (tmp_path / "env_cpu").mkdir()
    assert ocr_settings.detect_installed_tier() == ""


# ── install state across restarts ─────────────────────────────────────────


def test_installed_tier_is_adopted_even_when_env_says_none(monkeypatch, tmp_path):
    """The venv on disk outranks PMA_OCR_TIER.

    Regression guard: installing OCR used to work until the next restart, then
    silently stop - config re-read tier="none" from the environment and
    normalize_ocr forced ocr_enabled back to False.
    """
    from app.config import settings
    from app.ocr import settings as ocr_settings

    # The install now names itself rather than being assumed to be "cpu", so
    # the seam to stub is the detection, not the per-tier check.
    monkeypatch.setattr(ocr_settings, "detect_installed_tier", lambda: "cpu")
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

    monkeypatch.setattr(ocr_settings, "detect_installed_tier", lambda: "")
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

    # The install now names itself rather than being assumed to be "cpu", so
    # the seam to stub is the detection, not the per-tier check.
    monkeypatch.setattr(ocr_settings, "detect_installed_tier", lambda: "cpu")
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
