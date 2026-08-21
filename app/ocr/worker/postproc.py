"""Turn raw detector boxes into reading-ordered lines. Runs inside `<ocr_env>`.

MUST NOT IMPORT `app.*`.
"""

import unicodedata

#: Two boxes belong to the same visual line when their vertical centres are
#: within this fraction of the taller box's height.
_LINE_TOLERANCE = 0.6

#: A horizontal gap wider than this fraction of the page splits columns.
_COLUMN_GAP_RATIO = 0.15


def strip_control_chars(text):
    """Drop control characters that would poison the FTS trigram tokenizer.

    OCR output regularly contains stray C0/C1 bytes from misread glyphs. They
    are invisible in the UI but break tokenization downstream.
    """
    if not text:
        return ""
    return "".join(
        ch for ch in text if ch in ("\t", "\n") or not unicodedata.category(ch).startswith("C")
    ).strip()


def _box_bounds(box):
    """Return (x0, y0, x1, y1) from a 4-point polygon."""
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def to_lines(raw_results, conf_floor, page_width=None):
    """Order recognized boxes into lines and flag low-confidence ones.

    `raw_results` is RapidOCR's output: a sequence of (box, text, score).

    Low-confidence lines are *kept*, flagged rather than dropped. The indexer
    excludes them from the text it stores; the cache keeps them. That is what
    lets the confidence floor be raised later without re-running OCR.
    """
    entries = []
    for item in raw_results or ():
        try:
            box, text, score = item[0], item[1], item[2]
        except (TypeError, IndexError):
            continue
        clean = strip_control_chars(str(text))
        if not clean:
            continue
        x0, y0, x1, y1 = _box_bounds(box)
        entries.append(
            {
                "text": clean,
                "conf": float(score or 0.0),
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
            }
        )

    if not entries:
        return [], 0.0

    if page_width is None:
        page_width = max(e["x1"] for e in entries)

    # Cluster into visual lines by vertical overlap, then read each left-to-right.
    entries.sort(key=lambda e: (e["y0"], e["x0"]))
    rows = []
    current = [entries[0]]
    for entry in entries[1:]:
        prev = current[-1]
        prev_height = max(prev["y1"] - prev["y0"], 1.0)
        prev_centre = (prev["y0"] + prev["y1"]) / 2.0
        entry_centre = (entry["y0"] + entry["y1"]) / 2.0
        if abs(entry_centre - prev_centre) <= prev_height * _LINE_TOLERANCE:
            current.append(entry)
        else:
            rows.append(current)
            current = [entry]
    rows.append(current)

    lines = []
    conf_sum = 0.0
    gap_threshold = page_width * _COLUMN_GAP_RATIO if page_width else None

    for row in rows:
        row.sort(key=lambda e: e["x0"])
        # A wide horizontal gap means two columns landed on the same y-band;
        # joining them would interleave unrelated text.
        segments = [[row[0]]]
        for entry in row[1:]:
            if gap_threshold and entry["x0"] - segments[-1][-1]["x1"] > gap_threshold:
                segments.append([entry])
            else:
                segments[-1].append(entry)

        for segment in segments:
            text = " ".join(e["text"] for e in segment).strip()
            if not text:
                continue
            conf = sum(e["conf"] for e in segment) / len(segment)
            lines.append({"text": text, "conf": round(conf, 4), "low": conf < conf_floor})
            conf_sum += conf

    mean_conf = (conf_sum / len(lines)) if lines else 0.0
    return lines, round(mean_conf, 4)
