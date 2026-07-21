import logging
import math
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)


class LatencyTracker:
    """Lightweight in-memory tracker for stage-level latencies."""

    def __init__(self, window_size: int = 100):
        self._history: dict[str, deque] = {}
        self._window_size = window_size
        self._lock = threading.Lock()

    def record(self, stage: str, duration_ms: float):
        with self._lock:
            if stage not in self._history:
                self._history[stage] = deque(maxlen=self._window_size)
            self._history[stage].append(duration_ms)

    def get_stats(self) -> dict[str, dict[str, float]]:
        import statistics
        stats = {}
        with self._lock:
            for stage, values in self._history.items():
                if not values:
                    continue
                v = list(values)
                # quantiles(n=100) returns 99 cut points: index 49 is p50, 94 is p95, 98 is p99
                q = statistics.quantiles(v, n=100) if len(v) > 1 else [v[0]] * 99
                stats[stage] = {
                    "avg": round(statistics.mean(v), 2),
                    "p50": round(q[49], 2),
                    "p95": round(q[94], 2),
                    "p99": round(q[98], 2),
                    "max": round(max(v), 2),
                    "count": len(v),
                }
        return stats


metrics_tracker = LatencyTracker()


class Timer:
    """Context manager for timing code blocks and recording to the tracker."""

    def __init__(self, stage: str):
        self.stage = stage
        self.start_time = 0.0

    def __enter__(self):
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (time.perf_counter() - self.start_time) * 1000
        metrics_tracker.record(self.stage, duration_ms)
