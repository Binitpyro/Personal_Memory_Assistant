"""Particle advection through a sampled field."""


def step(position: tuple, velocity: tuple, dt: float) -> tuple:
    """Move a position along a velocity for one timestep."""
    return (
        position[0] + velocity[0] * dt,
        position[1] + velocity[1] * dt,
        position[2] + velocity[2] * dt,
    )


def trace_back(position: tuple, velocity: tuple, dt: float) -> tuple:
    """Find where the value at a position came from one timestep ago."""
    return step(position, velocity, -dt)


def advect_all(positions: list, sample, dt: float) -> list:
    """Advance every position by sampling the field at its own location."""
    return [step(p, sample(p), dt) for p in positions]
