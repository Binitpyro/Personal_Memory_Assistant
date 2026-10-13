"""Score QueryPlanner against the frozen routing set (A3-08, A3-11; MEASUREMENT_PREREG.md M7).

    .venv/Scripts/python.exe scripts/eval_routing.py [--json-out baseline.json]

Planner-only: no index, no models. Two scores per item - strict mode accuracy,
and outcome pass (does an answer of the right kind come back; definitions in
_outcome). Reports per class and per stratum; the pooled number over-weights
the deliberately adversarial strata and is not a usage-weighted accuracy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SET = ROOT / "tests" / "eval" / "routing_set.json"
FROZEN_SHA256 = "404283b740dd153aeccbf23ff767d5f1822b21e9c9dd4440f269569290320bb7"
# Held-out paraphrases written after the first planner change (M7 addendum 2).
HELDOUT = ROOT / "tests" / "eval" / "routing_heldout.json"
HELDOUT_SHA256 = "bfe60d022bf2dd4be1118f359ffacfd4bd55b62b24386bb5b30183fafa97b16c"
SETS = {"main": (SET, FROZEN_SHA256), "heldout": (HELDOUT, HELDOUT_SHA256)}


def _outcome(intent: str, mode: str, intents: dict[str, bool]) -> bool:
    if intent == "inventory":
        return bool(intents.get("inventory"))  # only then are file stats loaded
    if intent == "project":
        return bool(intents.get("project")) and mode in ("FAST_PROJECT", "FULL_RAG")
    if intent == "recency":
        return bool(intents.get("latest") or intents.get("largest")) and mode != "FAST_METADATA"
    if intent == "graph":
        return mode in ("GRAPH_SEARCH", "FULL_RAG")
    return mode in ("FULL_RAG", "GRAPH_SEARCH")  # content: no canned answer


def score(which: str = "main") -> dict:
    path, frozen = SETS[which]
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != frozen:
        raise SystemExit(f"{path.name} changed (sha256 {digest}); record an amendment in M7 first")

    from app.search.planner import QueryPlanner

    planner = QueryPlanner()
    rows = []
    for item in json.loads(raw)["items"]:
        plan = planner.plan(item["query"])
        mode = plan.mode.value
        rows.append(
            {
                **item,
                "mode": mode,
                "mode_ok": mode == item["gold_mode"],
                "outcome_ok": _outcome(item["intent"], mode, plan.intents),
                "graph_on_content": item["intent"] == "content" and mode == "GRAPH_SEARCH",
                "stats_noise": item["intent"] == "content" and bool(plan.intents.get("inventory")),
            }
        )
    return {"sha256": digest, "rows": rows}


def _table(rows: list[dict], key: str) -> list[str]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    out = []
    for name in sorted(groups):
        g = groups[name]
        out.append(
            f"  {name:12s} n={len(g):3d}  mode {sum(r['mode_ok'] for r in g):3d}/{len(g):<3d}"
            f"  outcome {sum(r['outcome_ok'] for r in g):3d}/{len(g):<3d}"
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=sorted(SETS), default="main")
    ap.add_argument("--json-out")
    args = ap.parse_args()
    res = score(args.set)
    rows = res["rows"]
    print(f"{args.set} set sha256 {res['sha256']}  n={len(rows)}")
    print(
        f"mode accuracy {sum(r['mode_ok'] for r in rows)}/{len(rows)}"
        f"  outcome pass {sum(r['outcome_ok'] for r in rows)}/{len(rows)}"
    )
    print("by intent:", *_table(rows, "intent"), sep="\n")
    print("by stratum:", *_table(rows, "stratum"), sep="\n")
    content = [r for r in rows if r["intent"] == "content"]
    for stratum in sorted({r["stratum"] for r in content}):
        g = [r for r in content if r["stratum"] == stratum]
        print(f"  content/{stratum:9s} outcome {sum(r['outcome_ok'] for r in g)}/{len(g)}")
    print(
        f"reported: GRAPH_SEARCH on content {sum(r['graph_on_content'] for r in rows)}"
        f"  inventory-stats noise on content {sum(r['stats_noise'] for r in rows)}"
    )
    print("failures (outcome):")
    for r in rows:
        if not r["outcome_ok"]:
            print(f"  {r['id']} [{r['intent']}/{r['stratum']}] -> {r['mode']}  {r['query']}")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
