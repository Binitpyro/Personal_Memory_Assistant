"""Batching frames into farm tasks."""


def batch_frames(frames: list, per_task: int) -> list:
    """Group frames so per-task startup cost is amortised.

    Startup dominates: each task spends a fixed period loading before doing
    useful work, so many short tasks waste most of the farm.
    """
    if per_task < 1:
        raise ValueError("per_task must be at least 1")
    return [frames[i : i + per_task] for i in range(0, len(frames), per_task)]


def estimate_cost(frame_count: int, per_task: int, startup_s: float, frame_s: float) -> float:
    """Total wall-clock seconds for a batching choice."""
    tasks = (frame_count + per_task - 1) // per_task
    return tasks * startup_s + frame_count * frame_s
