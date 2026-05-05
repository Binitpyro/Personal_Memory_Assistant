"""
Entry point for ``python -m .`` or ``python __main__.py``.

Usage
-----
    python .                        # Web server (dev, auto-reload)
    python . --mode server          # Web server (production)
    python . --host 0.0.0.0        # Bind to all interfaces
    python . --port 9000           # Custom port
    python . --workers 4           # Multi-worker production mode

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
    p.add_argument(
        "--workers", type=int, default=1, help="Worker processes (prod only, default: 1)"
    )
    p.add_argument("--reload", action="store_true", help="Enable auto-reload (dev)")
    p.add_argument("--no-reload", dest="reload", action="store_false")
    p.set_defaults(reload=None)  # auto-detect from dev_mode
    return p.parse_args()


def run_server(args: argparse.Namespace) -> None:
    import uvicorn

    from app.config import settings

    host = args.host or settings.host
    port = args.port or settings.port
    reload = args.reload if args.reload is not None else settings.dev_mode

    uvicorn_kwargs: dict = {
        "app": "app.main:app",
        "host": host,
        "port": port,
        "log_level": settings.log_level.lower(),
    }

    # If running as a frozen PyInstaller bundle AND not spawned by Tauri, open browser
    # Tauri sets X_LOCAL_ACCESS_TOKEN. If it's missing, handle standalone opening.
    if getattr(sys, "frozen", False) and not os.getenv("X_LOCAL_ACCESS_TOKEN"):
        Timer(2.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()

    if reload:
        # Dev mode: single process with hot-reload
        uvicorn_kwargs["reload"] = True
        uvicorn_kwargs["reload_dirs"] = [os.path.dirname(os.path.abspath(__file__))]
    elif args.workers > 1:
        # Production multi-worker (no reload)
        uvicorn_kwargs["workers"] = args.workers
        uvicorn_kwargs["access_log"] = False
    else:
        # Single production process
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
