"""Core-side client for the GPU embedding worker (``worker.py``).

Blocking by design: ``EmbeddingService._run_inference`` already runs in an
executor thread, so a pipe round-trip there blocks nothing on the event loop.
One request at a time under a lock - the worker answers in order and holds a
single DirectML session.

Failure policy: any worker error, crash or timeout returns None so the caller
re-runs that batch on the CPU INT8 session (same model, cos ~0.991), and the client
goes ``degraded`` for the rest of the process.
ponytail: no respawn after a failure; a flaky worker would otherwise pay its
~3 s startup on every batch. Restarting PMA clears it.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import queue
import subprocess  # nosec B404 - argv comes from fixed install paths, never request input
import sys
import threading
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

#: Must equal ``worker.PROTOCOL``; tests/test_embed_gpu.py pins the two together.
PROTOCOL = 1

_CREATE_NO_WINDOW = 0x08000000


class WorkerError(RuntimeError):
    pass


class GpuEmbedClient:
    def __init__(
        self,
        argv: list[str],
        model_path: str,
        *,
        ready_timeout_s: float = 120.0,
        call_timeout_s: float = 60.0,
        idle_s: float = 60.0,
    ) -> None:
        self._argv = argv
        self._model_path = model_path
        self._ready_timeout_s = ready_timeout_s
        self._call_timeout_s = call_timeout_s
        self._idle_s = idle_s
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._idle_timer: threading.Timer | None = None
        self._seq = 0
        self.dim = 0
        self.ep = ""
        self.degraded = False
        self.last_error = ""

    def status(self) -> dict[str, Any]:
        return {
            "running": self._proc is not None,
            "degraded": self.degraded,
            "last_error": self.last_error,
            "ep": self.ep,
        }

    def embed(self, ids: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
        """Pooled, normalised float32 vectors for one tokenised batch, or None."""
        if self.degraded:
            return None
        with self._lock:
            try:
                if self._proc is None:
                    self._spawn()
                self._seq += 1
                rows, cols = ids.shape
                rsp = self._call(
                    {
                        "type": "embed",
                        "id": self._seq,
                        "rows": rows,
                        "cols": cols,
                        "ids": _b64(ids.astype("<i4")),
                        "mask": _b64(mask.astype("<i4")),
                    },
                    self._call_timeout_s,
                )
                if rsp.get("type") != "vecs" or rsp.get("id") != self._seq:
                    raise WorkerError(f"unexpected reply {str(rsp)[:120]}")
                vecs = np.frombuffer(base64.b64decode(rsp["b64"]), dtype="<f4")
                if vecs.size != rows * self.dim:
                    raise WorkerError(f"got {vecs.size} floats for {rows}x{self.dim}")
                self._arm_idle()
                return vecs.reshape(rows, self.dim)
            except Exception as exc:
                self._fail(exc)
                return None

    def close(self) -> None:
        with self._lock:
            self._stop()

    # ── internals (callers hold self._lock) ──────────────────────────────

    def _spawn(self) -> None:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            # Diagnostics travel as error{} messages. An undrained stderr pipe
            # would deadlock the worker once its buffer filled.
            "stderr": subprocess.DEVNULL,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "shell": False,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        self._proc = subprocess.Popen(self._argv, **kwargs)  # nosec B603
        self._lines = queue.Queue()
        threading.Thread(
            target=_pump, args=(self._proc, self._lines), daemon=True, name="embed-gpu-reader"
        ).start()
        ready = self._call(
            {"type": "hello", "protocol": PROTOCOL, "model_path": self._model_path},
            self._ready_timeout_s,
        )
        if ready.get("type") != "ready" or ready.get("protocol") != PROTOCOL:
            raise WorkerError(f"bad handshake {str(ready)[:120]}")
        self.ep = str(ready.get("ep", ""))
        self.dim = int(ready.get("dim") or 0)
        if self.ep != "DmlExecutionProvider" or self.dim <= 0:
            raise WorkerError(f"worker reported ep={self.ep!r} dim={self.dim}")
        logger.info("GPU embed worker ready on %s (dim %d)", self.ep, self.dim)

    def _call(self, msg: dict[str, Any], timeout: float) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise WorkerError("worker not running")
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise WorkerError(f"no reply within {timeout:.0f}s") from None
        if line is None:
            raise WorkerError(f"worker exited (code {proc.poll()})")
        rsp: dict[str, Any] = json.loads(line)
        if rsp.get("type") == "error":
            raise WorkerError(f"{rsp.get('code')}: {rsp.get('detail', '')}")
        return rsp

    def _fail(self, exc: Exception) -> None:
        self.degraded = True
        self.last_error = str(exc)[:300]
        logger.warning("GPU embedding disabled for this session, using CPU: %s", self.last_error)
        self._stop(graceful=False)

    def _arm_idle(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        self._idle_timer = threading.Timer(self._idle_s, self._retire)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _retire(self) -> None:
        # Frees the worker's VRAM between ingests; the next batch respawns it.
        with self._lock:
            self._stop()

    def _stop(self, graceful: bool = True) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if graceful and proc.stdin:
                proc.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                proc.stdin.close()
                proc.wait(timeout=5)
        if proc.poll() is None:
            proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)


def _pump(proc: subprocess.Popen[str], lines: queue.Queue[str | None]) -> None:
    assert proc.stdout is not None  # nosec B101 - Popen(stdout=PIPE) above
    for line in proc.stdout:
        lines.put(line)
    lines.put(None)


def _b64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")
