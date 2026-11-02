"""
Entry point for ``python -m .`` or ``python __main__.py``.

Usage
-----
    python .                        # Web server (dev, auto-reload)
    python . --mode server          # Web server (production)
    python . --host 0.0.0.0        # Bind to all interfaces
    python . --port 9000           # Custom port
    python . --workers 4           # Refused: single worker only (see run_server)

NOTE: The canonical desktop launcher is now Tauri v2.
      To start the full desktop app, run::

          cd frontend && npm run tauri dev      # development (HMR + Python reload)
          cd frontend && npm run tauri build    # production MSI installer

      This script starts the FastAPI backend in server mode only (no window).
      Useful for backend-only development and CI.

Environment variables (via .env or PMA_ prefix) override defaults.
"""

import argparse
import os
import sys
import webbrowser
from threading import Timer

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# Fix for PyInstaller noconsole mode where sys.stdout and sys.stderr are None,
# which causes uvicorn's logging setup to crash on .isatty() calls.
class _DummyIO:
    def write(self, *args, **kwargs):
        pass

    def flush(self):
        pass

    def isatty(self):
        return False


if sys.stdout is None:
    sys.stdout = _DummyIO()  # type: ignore
if sys.stderr is None:
    sys.stderr = _DummyIO()  # type: ignore


def _get_resource_path(relative_path: str) -> str:
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="pma",
        description="Personal Memory Assistant - local-first RAG for your files",
    )
    p.add_argument("--host", default=None, help="Bind address (default: from config)")
    p.add_argument("--port", type=int, default=None, help="Port (default: from config)")
    p.add_argument("--workers", type=int, default=1, help="Ignored above 1: PMA runs one worker")
    p.add_argument("--reload", action="store_true", help="Enable auto-reload (dev)")
    p.add_argument("--no-reload", dest="reload", action="store_false")
    p.set_defaults(reload=None)  # auto-detect from dev_mode
    return p.parse_args()


def _port_from_env(default: int) -> int:
    raw = os.environ.get("PORT")
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[PMA] Ignoring non-numeric PORT={raw!r}; using {default}", file=sys.stderr)
        return default


def run_server(args: argparse.Namespace) -> None:
    import uvicorn

    from app.config import settings

    host = args.host or settings.host
    # Tauri exports PORT (src-tauri/src/lib.rs), not PMA_PORT.
    port = args.port or _port_from_env(settings.port)
    reload = args.reload if args.reload is not None else settings.dev_mode

    uvicorn_kwargs: dict = {
        "app": "app.main:app",
        "host": host,
        "port": port,
        "log_level": settings.log_level.lower(),
        # Key rate limits (app/api/limiter.py) on the socket peer. uvicorn's
        # default (trust X-Forwarded-For from 127.0.0.1) lets a caller rotate
        # that header to get a fresh bucket per value.
        "proxy_headers": False,
    }

    # If running as a frozen PyInstaller bundle AND not spawned by Tauri, open browser
    # Tauri sets X_LOCAL_ACCESS_TOKEN. If it's missing, handle standalone opening.
    if getattr(sys, "frozen", False) and not os.getenv("X_LOCAL_ACCESS_TOKEN"):
        Timer(2.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()

    if reload:
        # Dev mode: single process with hot-reload restricted to app code
        app_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
        uvicorn_kwargs["reload"] = True
        uvicorn_kwargs["reload_dirs"] = [app_dir]
        uvicorn_kwargs["reload_excludes"] = [
            "tests/*",
            "frontend/*",
            "data/*",
            ".pytest_cache/*",
            "*.db*",
            "*.db-wal",
            "*.db-shm",
        ]
    else:
        # Single production process. Never more: uvicorn does not set
        # UVICORN_WORKER_ID, so the "worker 0 only" guards (split-brain sync,
        # OCR, watcher) would run in every worker, and each worker would mint
        # its own access token and 401 the others' clients. `workers=1` is
        # explicit so WEB_CONCURRENCY cannot raise it either.
        if args.workers > 1:
            print(
                f"[PMA] --workers {args.workers} is not supported (single-user local app); "
                "running 1 worker.",
                file=sys.stderr,
            )
            args.workers = 1
        uvicorn_kwargs["workers"] = 1
        uvicorn_kwargs["access_log"] = False

    print(f"  PMA server -> http://{host}:{port}")
    print(f"  Mode: {'dev (reload)' if reload else f'production ({args.workers} worker(s))'}")
    print()
    uvicorn.run(**uvicorn_kwargs)


def main() -> None:
    args = parse_args()
    run_server(args)


if __name__ == "__main__":
    main()
