"""
Desktop launcher for Personal Memory Assistant.

Uses **pywebview** to create a native OS window backed by the system's
built-in browser engine (Edge WebView2 on Windows).  Overhead is minimal:
~5 MB for the window chrome on top of the FastAPI server.

Usage
-----
    python desktop.py            # normal
    pythonw desktop.py           # no console window
    python . --mode desktop      # via CLI entry point
"""

import sys
import os
import threading
import time
import logging
from urllib.request import urlopen
from urllib.error import URLError

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("desktop")

SERVER_HOST = settings.host
SERVER_PORT = settings.port
# Desktop app binds to localhost natively, so HTTP is safe here.
_SCHEME = "http"
SERVER_URL = f"{_SCHEME}://{SERVER_HOST}:{SERVER_PORT}"


def _start_server() -> None:
    """Run the FastAPI app via uvicorn (blocking, runs in a thread)."""
    import uvicorn

    config = uvicorn.Config(
        "app.main:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()


def _wait_for_server(timeout: int = 120) -> bool:
    """Poll /health until the server responds or *timeout* elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(f"{SERVER_URL}/health", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except URLError:
            pass
        time.sleep(0.25)
    return False


def main() -> None:
    try:
        import webview  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        print(
            "pywebview is required for the desktop app.\n"
            "Install it with:  pip install pywebview\n"
        )
        sys.exit(1)

    # 1. Start FastAPI server in a daemon thread
    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start()

    logger.info("Waiting for server...")
    if not _wait_for_server():
        print("ERROR: Server did not start within 120 seconds.")
        sys.exit(1)
    logger.info("Server ready at %s", SERVER_URL)

    # 2. Open a native window pointing at the local server
    webview.create_window(
        title="Personal Memory Assistant",
        url=SERVER_URL,
        width=1280,
        height=860,
        min_size=(900, 600),
        text_select=True,
    )
    try:
        webview.start(debug=False)
    except Exception as e:
        logger.error("webview crashed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
