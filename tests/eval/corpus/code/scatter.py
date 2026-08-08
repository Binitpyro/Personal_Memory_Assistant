"""Point distribution over a surface."""

import random


def scatter(triangles: list, count: int, seed: int = 0) -> list:
    """Distribute points across triangles weighted by area."""
    rng = random.Random(seed)
    areas = [_area(t) for t in triangles]
    total = sum(areas)
    if total <= 0:
        return []

    points = []
    for _ in range(count):
        target = rng.random() * total
        running = 0.0
        for tri, area in zip(triangles, areas):
            running += area
            if running >= target:
                points.append(_barycentric(tri, rng))
                break
    return points


def _area(tri: tuple) -> float:
    (ax, ay), (bx, by), (cx, cy) = tri
    return abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) * 0.5


def _barycentric(tri: tuple, rng) -> tuple:
    u, v = rng.random(), rng.random()
    if u + v > 1.0:
        u, v = 1.0 - u, 1.0 - v
    (ax, ay), (bx, by), (cx, cy) = tri
    return (ax + u * (bx - ax) + v * (cx - ax), ay + u * (by - ay) + v * (cy - ay))
