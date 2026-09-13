"""The query-type fixture's labels, which the whole adaptive-k test stands on.

A known-item label that silently matches two files scores a correct retrieval as
half wrong, and no metric downstream can tell. So every label is re-derived from
disk by `verify`, and the uniqueness filters are negative-controlled here: turned
off, `verify` must catch what they would have dropped.

Deterministic and offline: synthetic sources, no git, no SQuAD download.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from scripts.build_query_type_fixture import build, matches, record_queries, verify

CODE = {
    "app/alpha/widget_store.py": (
        "def compute_widget_total(x):\n    return x\n\n\nclass WidgetRegistry:\n    pass\n"
    ),
    # compute_widget_total is defined in BOTH files - a filter-off query on it
    # is labelled with one file while two define it.
    "app/beta/gadget_loader.py": (
        "def load_gadget_manifest():\n    return 1\n\n\ndef compute_widget_total(y):\n    return y\n"
    ),
    "app/gamma/report_builder.py": (
        "def assemble_quarterly_report():\n    return 2\n\n\nclass ReportFormatter:\n    pass\n"
    ),
    "tests/test_widget_store.py": "def helper_for_widgets():\n    pass\n",
}
TITLES = ["Solar_System", "Solar_Wind_Patterns", "Black_Death", "Apollo_program", "Amazon_River"]
UNIVERSE = [f"squad/{t}.txt" for t in TITLES]


def _build(out: Path, unique_only: bool = True, seed: int = 7) -> list[dict]:
    return build(out, CODE, TITLES, UNIVERSE, 12, 40, seed, unique_only=unique_only)


def test_every_label_holds_on_disk(tmp_path: Path) -> None:
    queries = _build(tmp_path / "fx")

    assert verify(tmp_path / "fx", queries, UNIVERSE) == []
    assert {q["type"] for q in queries} == {"code_identifier", "navigation", "structured_lookup"}
    assert len({q["id"] for q in queries}) == len(queries)
    for q in queries:
        rel = q["relevant_files"][0]
        assert rel in UNIVERSE or (tmp_path / "fx" / rel).is_file(), rel


def test_same_seed_gives_the_same_fixture(tmp_path: Path) -> None:
    assert json.dumps(_build(tmp_path / "a")) == json.dumps(_build(tmp_path / "b"))


def test_uniqueness_filter_off_is_caught(tmp_path: Path) -> None:
    queries = _build(tmp_path / "fx", unique_only=False)

    errors = verify(tmp_path / "fx", queries, UNIVERSE)
    assert any("compute_widget_total" in e for e in errors), errors


def test_record_filter_drops_a_query_two_records_answer() -> None:
    base = {"record_type": "invoice", "vendor": "Bluefen Software", "currency": "USD"}
    records = [
        (
            "qt_records/record_0000.json",
            {**base, "invoice_id": "INV-1", "date": "2025-03-02", "amount": "10.00"},
        ),
        (
            "qt_records/record_0001.json",
            {**base, "invoice_id": "INV-2", "date": "2025-03-20", "amount": "20.00"},
        ),
    ]

    def vendor_month(unique_only: bool) -> list[dict]:
        qs = record_queries(records, 5, random.Random(0), unique_only=unique_only)  # noqa: S311
        return [q for q in qs if q["note"] == "rec_vendor_month"]

    assert vendor_month(True) == []
    leaked = vendor_month(False)
    assert leaked, "filter off should admit the ambiguous vendor-month query"
    assert sum(matches(r, leaked[0]["match"]) for _, r in records) == 2
