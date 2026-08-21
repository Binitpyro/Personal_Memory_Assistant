"""Divergence-free vector field generation."""

import math


def potential(x: float, y: float, z: float, scale: float = 1.0) -> tuple:
    """Sample a smooth vector potential at a position."""
    return (
        math.sin(y * scale) * math.cos(z * scale),
        math.sin(z * scale) * math.cos(x * scale),
        math.sin(x * scale) * math.cos(y * scale),
    )


def curl(x: float, y: float, z: float, eps: float = 1e-3, scale: float = 1.0) -> tuple:
    """Finite-difference curl of the potential field.

    Taking the curl guarantees zero divergence, so advected volume is
    preserved without a pressure solve.
    """
    px0 = potential(x, y - eps, z, scale)
    px1 = potential(x, y + eps, z, scale)
    pz0 = potential(x, y, z - eps, scale)
    pz1 = potential(x, y, z + eps, scale)
    inv = 1.0 / (2.0 * eps)
    return (
        (px1[2] - px0[2]) * inv - (pz1[1] - pz0[1]) * inv,
        (pz1[0] - pz0[0]) * inv - (px1[2] - px0[2]) * inv,
        (px1[1] - px0[1]) * inv - (pz1[0] - pz0[0]) * inv,
    )
