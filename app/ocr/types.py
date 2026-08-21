"""Value types shared across the OCR subsystem.

Frozen dataclasses and enums only - no I/O, no `app.*` imports beyond
`app.config`. The worker does *not* import this module (it is on the far side
of the subprocess boundary and speaks JSON); the manager uses these types to
give the wire payloads a shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum


class PageVerdict(StrEnum):
    """What the detection gate decided about a single PDF page."""

    NATIVE = "native"
    OCR = "ocr"
    BLANK = "blank"


@dataclass(frozen=True)
class PageSignal:
    """Gate result plus the evidence behind it.

    ``image_xobjects`` and ``stream_bytes`` are ``-1`` when the gate
    short-circuited to NATIVE before inspecting page resources. That is not a
    missing value - it is the assertion that no resource traversal happened,
    and the gate tests check for it.
    """

    verdict: PageVerdict
    char_count: int
    garbage_ratio: float
    image_xobjects: int = -1
    stream_bytes: int = -1


@dataclass(frozen=True)
class OcrLine:
    """One recognized line. ``low`` marks it as below the confidence floor."""

    text: str
    conf: float
    low: bool = False


@dataclass(frozen=True)
class OcrPage:
    """All recognized lines for one page, plus timing and error state.

    Low-confidence lines are *kept* here and in the cache, and excluded only
    from the text handed to the indexer. That asymmetry is deliberate: raising
    ``ocr_conf_floor`` later re-filters from cache instead of forcing a re-OCR
    of the whole corpus.
    """

    page_num: int  # 0-based, matching reader.pages indexing
    lines: tuple[OcrLine, ...] = ()
    mean_conf: float = 0.0
    elapsed_ms: int = 0
    error: str | None = None

    @property
    def indexable_text(self) -> str:
        """High-confidence text only. This is what reaches chunks and FTS."""
        return "\n".join(ln.text for ln in self.lines if not ln.low and ln.text)

    @property
    def full_text(self) -> str:
        """Every line regardless of confidence. Cache and debug surfaces only."""
        return "\n".join(ln.text for ln in self.lines if ln.text)

    def to_cache_json(self) -> str:
        """Serialize for ``ocr_cache.text``.

        Stored as JSON rather than flat text so the ``low`` flags survive - a
        flat column could not support re-filtering at a different floor.
        """
        return json.dumps(
            {
                "lines": [{"t": ln.text, "c": round(ln.conf, 4), "l": ln.low} for ln in self.lines],
                "mean_conf": round(self.mean_conf, 4),
                "ms": self.elapsed_ms,
                "error": self.error,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @classmethod
    def from_cache_json(cls, page_num: int, raw: str) -> OcrPage:
        """Inverse of :meth:`to_cache_json`. Tolerates corrupt rows."""
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return cls(page_num=page_num, error="CACHE_CORRUPT")
        if not isinstance(data, dict):
            return cls(page_num=page_num, error="CACHE_CORRUPT")

        lines = []
        for entry in data.get("lines") or ():
            if not isinstance(entry, dict):
                continue
            lines.append(
                OcrLine(
                    text=str(entry.get("t") or ""),
                    conf=float(entry.get("c") or 0.0),
                    low=bool(entry.get("l")),
                )
            )
        return cls(
            page_num=page_num,
            lines=tuple(lines),
            mean_conf=float(data.get("mean_conf") or 0.0),
            elapsed_ms=int(data.get("ms") or 0),
            error=data.get("error") or None,
        )

    @classmethod
    def from_worker_json(cls, data: dict, conf_floor: float) -> OcrPage:
        """Build from one NDJSON line written by the worker.

        The worker already applies the floor, but we re-apply it here so a
        stale worker (older venv, different floor) can never smuggle
        low-confidence text into the index.
        """
        lines = []
        for entry in data.get("lines") or ():
            if not isinstance(entry, dict):
                continue
            conf = float(entry.get("conf") or 0.0)
            lines.append(
                OcrLine(
                    text=str(entry.get("text") or ""),
                    conf=conf,
                    low=bool(entry.get("low")) or conf < conf_floor,
                )
            )
        return cls(
            page_num=int(data.get("page", -1)),
            lines=tuple(lines),
            mean_conf=float(data.get("mean_conf") or 0.0),
            elapsed_ms=int(data.get("ms") or 0),
            error=data.get("error") or None,
        )


class QueueStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class OcrQueueRow:
    """One row of ``ocr_queue``, keyed by absolute file path."""

    file_path: str
    pages: tuple[int, ...]
    page_count: int
    pages_done: int
    tier: str
    status: QueueStatus
    force_ocr: bool
    attempts: int
    last_error: str
    enqueued_at: str
    updated_at: str
