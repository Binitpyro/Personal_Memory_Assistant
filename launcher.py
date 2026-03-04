"""
Lightweight launcher that starts the PMA server and opens a native desktop
window (pywebview) instead of the default browser.

Compile to .exe with:
    pip install pyinstaller
    pyinstaller --onefile --noconsole --name PMA --icon=static/icon.ico launcher.py

The .exe must live in the project root (next to app/ and .venv/).
"""

import multiprocessing
import os
import sys
import subprocess
import time
import ctypes
from pathlib import Path

APP_NAME = "Personal Memory Assistant"
HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"

# ── Re-entrance guard ────────────────────────────────────────────────────
# When PyInstaller-frozen, sys.executable == the .exe itself.  If we fall
# back to sys.executable as the "python" binary, subprocess.Popen would
# re-launch the exe → open another window → launch again → browser bomb.
_ENV_GUARD = "_PMA_LAUNCHER_RUNNING"


def _is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def find_python() -> str:
    """Locate the venv Python interpreter.

    NEVER returns sys.executable when frozen, because that is the .exe
    itself, which would cause an infinite-launch loop.
    """
    root = Path(os.path.dirname(os.path.abspath(sys.argv[0])))
    candidates = [
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
        root / "venv" / "Scripts" / "python.exe",
        root / "venv" / "bin" / "python",
    ]
    for p in candidates:
        if p.exists():
            return str(p)

    if _is_frozen():
        # Cannot use sys.executable (it's the .exe).  Try system PATH.
        import shutil
        py = shutil.which("python") or shutil.which("python3")
        if py:
            return py
        raise FileNotFoundError(
            "Could not find a Python interpreter.\n"
            "Place the .exe next to a .venv/ folder or make sure python is on PATH."
        )

    return sys.executable


def is_admin() -> bool:
    """Check whether the current process has administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def wait_for_server(timeout: int = 120) -> bool:
    """Wait until the server responds."""
    import urllib.request
    import urllib.error
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{URL}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def _show_error(msg: str) -> None:
    """Show an error dialog on Windows, or print to stderr."""
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, APP_NAME, 0x10)
    except Exception:
        print(f"ERROR: {msg}", file=sys.stderr)


def _kill_stale_server() -> None:
    """Kill any process already listening on PORT so the new server can bind."""
    if sys.platform != "win32":
        return
    try:
        # Use netstat to find PIDs on our port (avoids needing admin for Get-NetTCPConnection)
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        my_pid = os.getpid()
        for line in result.stdout.splitlines():
            # Match lines like  TCP  127.0.0.1:8000  ...  LISTENING  12345
            # Use exact port match (e.g. :8000 followed by space) to avoid
            # false positives like :80000
            if f":{PORT} " in line and "LISTENING" in line:
                parts = line.split()
                try:
                    pid = int(parts[-1])
                except (ValueError, IndexError):
                    continue
                if pid and pid != my_pid:
                    print(f"  Killing stale server (PID {pid}) on port {PORT}...")
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
    except Exception as e:
        print(f"  (Could not check for stale server: {e})", file=sys.stderr)


def main():
    # ── Prevent infinite re-launch ────────────────────────────────────
    if os.environ.get(_ENV_GUARD):
        print("Launcher re-entrance blocked – exiting.", file=sys.stderr)
        sys.exit(0)
    os.environ[_ENV_GUARD] = "1"

    root = Path(os.path.dirname(os.path.abspath(sys.argv[0])))

    # Set working directory to project root
    os.chdir(root)

    try:
        python = find_python()
    except FileNotFoundError as exc:
        _show_error(str(exc))
        sys.exit(1)

    # Check for required files
    main_py = root / "__main__.py"
    if not main_py.exists():
        _show_error(
            f"Cannot find __main__.py in:\n{root}\n\n"
            "Make sure the .exe is in the project root folder."
        )
        sys.exit(1)

    admin_note = " (Admin - MFT enabled)" if is_admin() else ""
    print(f"  {APP_NAME}{admin_note}")
    print(f"  Starting server at {URL} ...")
    print(f"  Python: {python}")
    print()

    # Kill any leftover server on the same port before starting a new one
    _kill_stale_server()

    # Launch the server as a subprocess
    env = os.environ.copy()
    env["PMA_DEV_MODE"] = "0"  # Production mode for .exe
    server_proc = subprocess.Popen(
        [python, "__main__.py", "--no-reload"],
        cwd=str(root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    # Wait for server to be ready
    if not wait_for_server():
        _show_error(
            "Server failed to start within 120 seconds.\n\n"
            "Check that dependencies are installed:\n  pip install -r requirements.txt"
        )
        server_proc.terminate()
        sys.exit(1)

    # ── Open a native desktop window instead of the browser ───────────
    try:
        import webview  # pywebview
    except ImportError:
        _show_error(
            "pywebview is required for the desktop window.\n"
            "Install it with:  pip install pywebview\n\n"
            "Falling back to default browser."
        )
        import webbrowser
        webbrowser.open(URL)
        try:
            server_proc.wait()
        except KeyboardInterrupt:
            server_proc.terminate()
            server_proc.wait(timeout=5)
        return

    print(f"  Opening desktop window ...")
    print(f"  Close the window to stop the server.")
    print()

    window = webview.create_window(
        title="Personal Memory Assistant",
        url=URL,
        width=1280,
        height=860,
        min_size=(900, 600),
        text_select=True,
    )
    try:
        webview.start(debug=False)
    except Exception as e:
        print(f"  webview error: {e}", file=sys.stderr)
    finally:
        # When the window is closed, terminate the server
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except Exception:
            server_proc.kill()


if __name__ == "__main__":
    multiprocessing.freeze_support()   # required for PyInstaller on Windows
    main()
