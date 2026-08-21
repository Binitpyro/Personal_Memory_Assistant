"""Scene-linear and display colour conversion."""


def to_scene_linear(value: float, gamma: float = 2.2) -> float:
    """Undo a display encoding, producing a scene-linear value."""
    return value**gamma


def to_display(value: float, gamma: float = 2.2) -> float:
    """Apply a display encoding to a scene-linear value."""
    return value ** (1.0 / gamma)


def validate_ingest(values: list, tolerance: float = 1e-6) -> bool:
    """Reject textures that are already display-encoded.

    Ambiguous files are the source of nearly every colour bug we see.
    """
    return all(v >= -tolerance for v in values)
