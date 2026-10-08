"""A8-07: the Rust layout ran synchronously on the event loop."""

import asyncio
import threading
import time
from types import SimpleNamespace

from app.insights import visualizer


class _FakeDB:
    async def execute_query(self, query, params=()):
        return [{"id": 1, "path": "C:/d/a.txt", "type": ".txt", "size": 10}]


def test_layout_runs_off_the_event_loop(monkeypatch):
    seen = {}

    def slow_layout(files):
        seen["thread"] = threading.current_thread()
        time.sleep(0.3)  # releases the GIL, as a Rust layout with allow_threads would
        return b"\x00" * 32

    monkeypatch.setattr(visualizer, "_RUST_AVAILABLE", True)
    monkeypatch.setattr(
        visualizer, "rust_core", SimpleNamespace(get_spatial_binary=slow_layout), raising=False
    )

    async def run():
        stall = 0.0
        stop = False

        async def ticker():
            nonlocal stall
            last = time.perf_counter()
            while not stop:
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                stall = max(stall, now - last)
                last = now

        t = asyncio.create_task(ticker())
        resp = await visualizer._stream_visualizer_binary_impl(None, _FakeDB())
        stop = True
        await t
        return resp, stall

    resp, stall = asyncio.run(run())
    assert resp.body == b"\x00" * 32
    assert seen["thread"] is not threading.main_thread()
    assert stall < 0.15, f"event loop stalled {stall:.3f}s during layout"
