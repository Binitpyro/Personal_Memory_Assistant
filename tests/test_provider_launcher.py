"""Tests for on-demand local provider start-up.

Nothing here may actually spawn a process: _spawn_detached is always replaced with a
spy, and reachability is always stubbed.
"""

import subprocess
import sys
import time

import pytest

from app.providers import launcher
from app.providers.launcher import LaunchCandidate, ResolvedCandidate

OLLAMA_URL = "http://localhost:11434"
LM_STUDIO_URL = "http://localhost:1234/v1"


class SpawnSpy:
    def __init__(self):
        self.calls: list[tuple[str, LaunchCandidate]] = []

    def __call__(self, executable: str, candidate: LaunchCandidate) -> None:
        self.calls.append((executable, candidate))

    @property
    def called(self) -> bool:
        return bool(self.calls)


@pytest.fixture
def spawn(monkeypatch):
    spy = SpawnSpy()
    monkeypatch.setattr(launcher, "_spawn_detached", spy)
    return spy


@pytest.fixture
def no_warmup(monkeypatch):
    """Skip the post-launch validate() so tests never touch the network."""

    async def _noop(provider_id: str, base_url: str) -> None:
        return None

    monkeypatch.setattr(launcher, "_warm_validation_cache", _noop)


def set_reachable(monkeypatch, value: bool | list[bool]) -> None:
    """Stub the socket probe. A list is consumed one entry per call, last value sticks."""
    results = value if isinstance(value, list) else None

    def _probe(url: str, timeout: float = 0.2, use_cache: bool = True) -> bool:
        if results is None:
            return bool(value)
        return results.pop(0) if len(results) > 1 else results[0]

    monkeypatch.setattr(launcher, "is_local_endpoint_reachable", _probe)


def fake_resolution(monkeypatch, candidate: LaunchCandidate, executable: str = "C:\\fake\\x.exe"):
    monkeypatch.setattr(
        launcher,
        "resolve_candidate",
        lambda pid: ResolvedCandidate(candidate=candidate, executable=executable),
    )


# ── Guard rails ────────────────────────────────────────────────────────────────


def test_only_local_providers_are_launchable():
    """The launch table is the entire attack surface -- it must stay closed."""
    assert set(launcher.LAUNCHERS) == {"ollama", "lm_studio"}


def test_launch_arguments_are_fixed_constants():
    """No candidate may carry anything but a tuple of plain string literals."""
    for candidates in launcher.LAUNCHERS.values():
        for candidate in candidates:
            assert isinstance(candidate.args, tuple)
            assert all(isinstance(arg, str) for arg in candidate.args)
            assert candidate.target
            # A bare name is resolved via PATH; anything else must be absolute.
            assert "\n" not in candidate.target


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CreateProcess flags")
def test_spawn_requests_job_breakaway(monkeypatch):
    """A started provider has to outlive PMA.

    Under Tauri the backend sits in a Job Object with KILL_ON_JOB_CLOSE, and job
    membership is inherited. CREATE_BREAKAWAY_FROM_JOB is the only spawn flag that
    escapes it -- ShellExecute and a plain Popen both die with the job.
    """
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        seen["flags"] = kwargs.get("creationflags", 0)
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    launcher._spawn_detached("C:\\x\\ollama.exe", LaunchCandidate(target="o", args=("serve",)))

    assert seen["argv"] == ["C:\\x\\ollama.exe", "serve"]
    assert seen["flags"] & launcher.CREATE_BREAKAWAY_FROM_JOB
    assert seen["flags"] & launcher.DETACHED_PROCESS


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CreateProcess flags")
def test_spawn_falls_back_when_breakaway_is_forbidden(monkeypatch):
    """An older app shell without BREAKAWAY_OK should still get its provider started."""
    flags_seen: list[int] = []

    def fake_popen(argv, **kwargs):
        flags = kwargs.get("creationflags", 0)
        flags_seen.append(flags)
        if len(flags_seen) == 1:
            raise OSError(13, "Access is denied", None, 5)
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    launcher._spawn_detached("C:\\x\\ollama.exe", LaunchCandidate(target="o"))

    assert len(flags_seen) == 2, "should retry once without the breakaway flag"
    assert flags_seen[0] & launcher.CREATE_BREAKAWAY_FROM_JOB
    assert not flags_seen[1] & launcher.CREATE_BREAKAWAY_FROM_JOB


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CreateProcess flags")
def test_spawn_reraises_unrelated_os_errors(monkeypatch):
    def fake_popen(argv, **kwargs):
        raise OSError(2, "The system cannot find the file specified", None, 2)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(OSError):
        launcher._spawn_detached("C:\\x\\missing.exe", LaunchCandidate(target="o"))


def test_resolve_candidate_returns_none_when_nothing_installed(monkeypatch):
    monkeypatch.setattr(launcher, "_resolve_target", lambda target: None)
    assert launcher.resolve_candidate("ollama") is None


def test_resolve_candidate_picks_first_available(monkeypatch):
    monkeypatch.setattr(
        launcher, "_resolve_target", lambda target: "C:\\resolved.exe" if "app" in target else None
    )
    resolved = launcher.resolve_candidate("ollama")
    assert resolved is not None
    assert resolved.executable == "C:\\resolved.exe"


# ── get_launch_status ──────────────────────────────────────────────────────────


def test_status_for_cloud_provider_is_unsupported(monkeypatch):
    set_reachable(monkeypatch, False)
    status = launcher.get_launch_status("openai", "https://api.openai.com/v1")
    assert status["supported"] is False
    assert status["installed"] is False
    assert status["install_url"] is None


def test_status_reports_installed_and_running(monkeypatch):
    set_reachable(monkeypatch, True)
    fake_resolution(monkeypatch, LaunchCandidate(target="ollama", label="ollama serve"))

    status = launcher.get_launch_status("ollama", OLLAMA_URL)
    assert status["supported"] is True
    assert status["installed"] is True
    assert status["running"] is True
    assert status["method"] == "ollama serve"
    assert status["install_url"] == "https://ollama.com/download"


def test_status_reports_not_installed(monkeypatch):
    set_reachable(monkeypatch, False)
    monkeypatch.setattr(launcher, "resolve_candidate", lambda pid: None)

    status = launcher.get_launch_status("lm_studio", LM_STUDIO_URL)
    assert status["installed"] is False
    assert status["running"] is False
    assert status["method"] is None
    assert status["install_url"] == "https://lmstudio.ai/download"


# ── launch ─────────────────────────────────────────────────────────────────────


async def test_cloud_provider_is_never_launched(spawn, no_warmup):
    res = await launcher.launch("openai", "https://api.openai.com/v1")
    assert res["ok"] is False
    assert res["error_code"] == "not_supported"
    assert not spawn.called


async def test_already_running_short_circuits(monkeypatch, spawn, no_warmup):
    set_reachable(monkeypatch, True)

    res = await launcher.launch("ollama", OLLAMA_URL)
    assert res["ok"] is True
    assert res["running"] is True
    assert res["already_running"] is True
    assert not spawn.called, "must not start a second server when one is already up"


async def test_not_installed_does_not_spawn(monkeypatch, spawn, no_warmup):
    set_reachable(monkeypatch, False)
    monkeypatch.setattr(launcher, "resolve_candidate", lambda pid: None)

    res = await launcher.launch("ollama", OLLAMA_URL)
    assert res["ok"] is False
    assert res["error_code"] == "not_installed"
    assert not spawn.called


async def test_successful_launch(monkeypatch, spawn):
    # Down on the pre-flight check, up once polling starts.
    set_reachable(monkeypatch, [False, True])
    fake_resolution(monkeypatch, LaunchCandidate(target="ollama", args=("serve",)))

    warmed: list[str] = []

    async def _warm(provider_id: str, base_url: str) -> None:
        warmed.append(provider_id)

    monkeypatch.setattr(launcher, "_warm_validation_cache", _warm)

    res = await launcher.launch("ollama", OLLAMA_URL, deadline_s=2.0)
    assert res["ok"] is True
    assert res["running"] is True
    assert res["already_running"] is False
    assert res["error_code"] is None
    assert spawn.calls == [("C:\\fake\\x.exe", LaunchCandidate(target="ollama", args=("serve",)))]
    assert warmed == ["ollama"], "successful launch should warm the validation cache"


async def test_launch_times_out_without_hanging(monkeypatch, spawn, no_warmup):
    set_reachable(monkeypatch, False)
    fake_resolution(monkeypatch, LaunchCandidate(target="ollama", args=("serve",)))

    started = time.monotonic()
    res = await launcher.launch("ollama", OLLAMA_URL, deadline_s=0.3)
    elapsed = time.monotonic() - started

    assert res["ok"] is False
    assert res["error_code"] == "timeout"
    assert spawn.called
    assert elapsed < 3.0, "deadline must be honoured"


async def test_gui_fallback_reports_manual_step(monkeypatch, spawn, no_warmup):
    set_reachable(monkeypatch, False)
    fake_resolution(
        monkeypatch,
        LaunchCandidate(
            target="C:\\Program Files\\LM Studio\\LM Studio.exe",
            label="LM Studio app",
            manual_step="Open the Developer tab and click Start Server.",
        ),
    )

    res = await launcher.launch("lm_studio", LM_STUDIO_URL, deadline_s=0.3)
    assert res["ok"] is False
    assert res["error_code"] == "manual_step_required"
    assert "Start Server" in res["message"]


async def test_os_error_is_reported_not_raised(monkeypatch, no_warmup):
    set_reachable(monkeypatch, False)
    fake_resolution(monkeypatch, LaunchCandidate(target="ollama"))

    def _boom(executable: str, candidate: LaunchCandidate) -> None:
        raise OSError("access denied")

    monkeypatch.setattr(launcher, "_spawn_detached", _boom)

    res = await launcher.launch("ollama", OLLAMA_URL, deadline_s=0.3)
    assert res["ok"] is False
    assert res["error_code"] == "launch_failed"
    assert "access denied" in res["message"]


async def test_unsupported_platform(monkeypatch, spawn, no_warmup):
    monkeypatch.setattr(launcher, "LAUNCHERS", {})

    res = await launcher.launch("ollama", OLLAMA_URL)
    assert res["ok"] is False
    assert res["error_code"] == "unsupported_platform"
    assert not spawn.called
