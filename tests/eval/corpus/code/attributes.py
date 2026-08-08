"""Per-element attribute storage."""


class AttributeSet:
    """Holds named per-element values for one geometry class."""

    def __init__(self, count: int):
        self.count = count
        self._data: dict = {}

    def create(self, name: str, default=0.0) -> None:
        self._data[name] = [default] * self.count

    def get(self, name: str, index: int):
        return self._data[name][index]

    def set(self, name: str, index: int, value) -> None:
        self._data[name][index] = value

    def promote(self, name: str, groups: list) -> list:
        """Average a per-element value up to one value per group."""
        values = self._data[name]
        return [sum(values[i] for i in g) / len(g) for g in groups if g]
