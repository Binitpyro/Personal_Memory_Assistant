import math
import time
import logging
import threading
from collections import deque
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

class LatencyTracker:
    """Lightweight in-memory tracker for stage-level latencies."""
    
    def __init__(self, window_size: int = 100):
        self._history: Dict[str, deque] = {}
        self._window_size = window_size
        self._lock = threading.Lock()

    def record(self, stage: str, duration_ms: float):
        with self._lock:
            if stage not in self._history:
                self._history[stage] = deque(maxlen=self._window_size)
            self._history[stage].append(duration_ms)

    def get_stats(self) -> Dict[str, Dict[str, float]]:
        stats = {}
        with self._lock:
            for stage, values in self._history.items():
                if not values:
                    continue
                sorted_vals = sorted(values)
                stats[stage] = {
                    "avg": round(sum(sorted_vals) / len(sorted_vals), 2),
                    "p50": round(sorted_vals[len(sorted_vals) // 2], 2),
                    "p95": round(sorted_vals[max(0, math.ceil(len(sorted_vals) * 0.95) - 1)], 2),
                    "count": len(sorted_vals)
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
