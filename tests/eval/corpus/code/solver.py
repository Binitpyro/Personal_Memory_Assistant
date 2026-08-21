"""Frame-stepping state solver."""


class Solver:
    """Evaluates a step function once per frame, feeding back the result."""

    def __init__(self, start_frame: int, initial_state):
        self.start_frame = start_frame
        self.initial_state = initial_state
        self._cache: dict = {self.start_frame: initial_state}

    def state_at(self, frame: int, step_fn):
        """Return the state at a frame, re-evaluating from the start if needed."""
        if frame in self._cache:
            return self._cache[frame]
        state = self._cache[self.start_frame]
        for f in range(self.start_frame + 1, frame + 1):
            state = step_fn(state, f)
            self._cache[f] = state
        return state
