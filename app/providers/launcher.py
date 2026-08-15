"""On-demand start-up for local LLM providers (Ollama, LM Studio).

This is the only process-spawning code in the backend; everything else that starts a
process lives in the Rust shell. Two properties keep it safe:

1. Nothing from the HTTP request reaches the executable path or its arguments. The
   request supplies a provider id, which is looked up in LAUNCHERS -- a table fixed at
   import time. Executables are resolved from absolute well-known install paths or from
   PATH via shutil.which; arguments are frozen constants. No shell is ever involved.

2. Launched providers must outlive PMA. Under Tauri the Python backend is assigned to a
   Windows Job Object with KILL_ON_JOB_CLOSE (frontend/src-tauri/src/lib.rs), and job
   membership is inherited, so an ordinary child would be killed the moment the app
   exits. CREATE_BREAKAWAY_FROM_JOB is the only reliable way out, and it requires the
   job to permit it -- hence JOB_OBJECT_LIMIT_BREAKAWAY_OK on the Rust side.

   Measured on Windows 11, spawning under a KILL_ON_JOB_CLOSE job:

       os.startfile (ShellExecute)     -> killed with the job
       plain Popen                     -> killed with the job
       CREATE_BREAKAWAY_FROM_JOB       -> ERROR_ACCESS_DENIED if the job forbids it,
                                          survives if the job permits it

   ShellExecute is *not* an escape hatch: it does a direct CreateProcess in-process
   rather than handing off to Explorer, so the child inherits the job like any other.
   The flag is harmless when no job is present (plain `uv run` during development).
"""

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.providers.manifest import is_local_endpoint_reachable
from app.providers.registry import spec_of

logger = logging.getLogger(__name__)

# CreateProcess flags. DETACHED_PROCESS also keeps console providers (ollama serve,
# lms server start) from flashing a window.
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_WINDOWS_SPAWN_FLAGS = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
_ERROR_ACCESS_DENIED = 5

POLL_INTERVAL_S = 0.25
# Measured cold starts: Ollama ~5s, LM Studio (`lms server start`) ~12s. The budget is
# generous because a premature "timeout" reads as failure while the server is in fact
# still coming up.
DEFAULT_DEADLINE_S = 45.0
PROBE_TIMEOUT_S = 0.2

INSTALL_URLS = {
    "ollama": "https://ollama.com/download",
    "lm_studio": "https://lmstudio.ai/download",
}

_LM_STUDIO_MANUAL_STEP = (
    "LM Studio opened, but its local server isn't running yet. "
    "Open the Developer tab and click Start Server."
)


@dataclass(frozen=True)
class LaunchCandidate:
    """One way to start a provider. Every field is a constant defined in this module."""

    target: str
    """Absolute path to an executable, or a bare name to look up on PATH."""

    args: tuple[str, ...] = ()
    label: str = ""
    manual_step: str | None = None
    """Set when launching this candidate does not by itself bring the server up."""


@dataclass(frozen=True)
class ResolvedCandidate:
    candidate: LaunchCandidate
    executable: str


def _existing_dir_path(root: str | None, *parts: str) -> str | None:
    """Build an absolute candidate path, or None if the environment root is unset.

    Without the guard an empty %LOCALAPPDATA% would yield a *relative* path that could
    accidentally match a file in the current working directory.
    """
    if not root:
        return None
    return str(Path(root).joinpath(*parts))


def _windows_launchers() -> dict[str, tuple[LaunchCandidate, ...]]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("PROGRAMFILES")
    home = str(Path.home())

    ollama_tray = _existing_dir_path(local_app_data, "Programs", "Ollama", "ollama app.exe")
    lms_cli = _existing_dir_path(home, ".lmstudio", "bin", "lms.exe")
    lm_studio_app = _existing_dir_path(program_files, "LM Studio", "LM Studio.exe")

    ollama: list[LaunchCandidate] = []
    if ollama_tray:
        # The tray app is how Ollama normally runs on Windows; it owns the server.
        ollama.append(LaunchCandidate(target=ollama_tray, label="Ollama desktop app"))
    ollama.append(LaunchCandidate(target="ollama", args=("serve",), label="ollama serve"))

    lm_studio: list[LaunchCandidate] = []
    if lms_cli:
        lm_studio.append(
            LaunchCandidate(
                target=lms_cli,
                args=("server", "start"),
                label="LM Studio headless server",
            )
        )
    lm_studio.append(
        LaunchCandidate(
            target="lms",
            args=("server", "start"),
            label="LM Studio headless server",
        )
    )
    if lm_studio_app:
        # Last resort: opening the GUI does not start the local server by itself.
        lm_studio.append(
            LaunchCandidate(
                target=lm_studio_app,
                label="LM Studio app",
                manual_step=_LM_STUDIO_MANUAL_STEP,
            )
        )

    return {"ollama": tuple(ollama), "lm_studio": tuple(lm_studio)}


def _posix_launchers() -> dict[str, tuple[LaunchCandidate, ...]]:
    lms_cli = _existing_dir_path(str(Path.home()), ".lmstudio", "bin", "lms")
    lm_studio: list[LaunchCandidate] = []
    if lms_cli:
        lm_studio.append(
            LaunchCandidate(
                target=lms_cli, args=("server", "start"), label="LM Studio headless server"
            )
        )
    lm_studio.append(
        LaunchCandidate(target="lms", args=("server", "start"), label="LM Studio headless server")
    )

    return {
        "ollama": (LaunchCandidate(target="ollama", args=("serve",), label="ollama serve"),),
        "lm_studio": tuple(lm_studio),
    }


def _build_launchers() -> dict[str, tuple[LaunchCandidate, ...]]:
    if sys.platform == "win32":
        return _windows_launchers()
    if sys.platform in ("darwin", "linux"):
        return _posix_launchers()
    return {}


LAUNCHERS: dict[str, tuple[LaunchCandidate, ...]] = _build_launchers()

_locks: dict[str, asyncio.Lock] = {}


def _lock_for(provider_id: str) -> asyncio.Lock:
    """Serialise launches per provider so a double-click can't spawn two servers."""
    lock = _locks.get(provider_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[provider_id] = lock
    return lock


def _display_name(provider_id: str) -> str:
    try:
        return spec_of(provider_id).display_name
    except ValueError:
        return provider_id


def _resolve_target(target: str) -> str | None:
    path = Path(target)
    if path.is_absolute():
        return str(path) if path.is_file() else None
    return shutil.which(target)


def resolve_candidate(provider_id: str) -> ResolvedCandidate | None:
    """First candidate that actually exists on this machine, or None if none do."""
    for candidate in LAUNCHERS.get(provider_id, ()):
        executable = _resolve_target(candidate.target)
        if executable:
            return ResolvedCandidate(candidate=candidate, executable=executable)
    return None


def get_launch_status(provider_id: str, base_url: str) -> dict:
    if provider_id not in LAUNCHERS:
        return {
            "provider_id": provider_id,
            "supported": False,
            "installed": False,
            "running": False,
            "method": None,
            "install_url": INSTALL_URLS.get(provider_id),
        }

    resolved = resolve_candidate(provider_id)
    return {
        "provider_id": provider_id,
        "supported": True,
        "installed": resolved is not None,
        "running": is_local_endpoint_reachable(base_url),
        "method": resolved.candidate.label if resolved else None,
        "install_url": INSTALL_URLS.get(provider_id),
    }


def _popen(argv: list[str], creationflags: int = 0, new_session: bool = False) -> None:
    subprocess.Popen(  # fixed argv from LAUNCHERS, shell=False
        argv,
        creationflags=creationflags,
        start_new_session=new_session,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _spawn_detached(executable: str, candidate: LaunchCandidate) -> None:
    """Start the process so that it survives this backend exiting.

    `executable` and `candidate` both originate from the LAUNCHERS table, never from a
    request, so no untrusted string reaches the command line.
    """
    argv = [executable, *candidate.args]

    if sys.platform != "win32":
        _popen(argv, new_session=True)
        return

    try:
        _popen(argv, creationflags=_WINDOWS_SPAWN_FLAGS)
    except OSError as e:
        if getattr(e, "winerror", None) != _ERROR_ACCESS_DENIED:
            raise
        # The surrounding job object forbids breakaway -- an app shell built before
        # BREAKAWAY_OK was added. Start it anyway; it just won't outlive PMA.
        logger.warning("Job object forbids breakaway; %s will shut down when PMA does.", executable)
        _popen(argv, creationflags=_WINDOWS_SPAWN_FLAGS & ~CREATE_BREAKAWAY_FROM_JOB)


async def _reachable(base_url: str) -> bool:
    return await asyncio.to_thread(is_local_endpoint_reachable, base_url, PROBE_TIMEOUT_S, False)


async def _poll_until_reachable(base_url: str, budget_s: float) -> bool:
    deadline = time.monotonic() + max(budget_s, 0.0)
    while True:
        if await _reachable(base_url):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(POLL_INTERVAL_S)


async def _warm_validation_cache(provider_id: str, base_url: str) -> None:
    """Populate the 60s validation cache and model heap so the UI shows models at once."""
    from app.providers import create_provider

    try:
        provider = create_provider(provider_id, base_url=base_url)
        try:
            await provider.validate()
        finally:
            await provider.close()
    except Exception as e:  # never fail the launch over a warm-up
        logger.debug("Post-launch validation of %s failed: %s", provider_id, e)


async def launch(provider_id: str, base_url: str, deadline_s: float = DEFAULT_DEADLINE_S) -> dict:
    """Start a local provider and wait until its port answers.

    deadline_s is a parameter so tests can use a short budget instead of really waiting.
    """
    started = time.monotonic()
    name = _display_name(provider_id)

    def result(
        ok: bool,
        running: bool,
        message: str,
        error_code: str | None = None,
        already_running: bool = False,
    ) -> dict:
        return {
            "ok": ok,
            "running": running,
            "already_running": already_running,
            "message": message,
            "error_code": error_code,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    if not LAUNCHERS:
        return result(
            False,
            False,
            f"Starting local providers isn't supported on {sys.platform}.",
            "unsupported_platform",
        )

    if provider_id not in LAUNCHERS:
        return result(False, False, f"{name} can't be started from PMA.", "not_supported")

    async with _lock_for(provider_id):
        if await _reachable(base_url):
            return result(True, True, f"{name} is already running.", already_running=True)

        resolved = resolve_candidate(provider_id)
        if resolved is None:
            return result(False, False, f"{name} doesn't appear to be installed.", "not_installed")

        logger.info("Starting %s via %s", name, resolved.executable)
        try:
            await asyncio.to_thread(_spawn_detached, resolved.executable, resolved.candidate)
        except OSError as e:
            logger.warning("Failed to start %s via %s: %s", name, resolved.executable, e)
            return result(False, False, f"Could not start {name}: {e}", "launch_failed")

        remaining = deadline_s - (time.monotonic() - started)
        if await _poll_until_reachable(base_url, remaining):
            await _warm_validation_cache(provider_id, base_url)
            return result(True, True, f"{name} is running.")

        if resolved.candidate.manual_step:
            return result(False, False, resolved.candidate.manual_step, "manual_step_required")

        return result(
            False,
            False,
            f"Started {name}, but it hasn't answered at {base_url} yet. "
            "It may still be starting up -- check again in a moment.",
            "timeout",
        )
