"""Sweep configurations, score them, and keep a journal of every experiment.

The loop the chunking workstream needed and did not have. CLAUDE.md 8.4a (summary
weight six times too high) and 8.7d (`rrf_k` reused at the wrong scale) are the
same failure twice: a constant shipped without being swept in its own context.
There are roughly twenty such knobs and each hand-run sweep has cost a session,
so the sweeps stopped happening. This makes them cheap.

Deliberately small. `app/config.py` sets ``env_prefix="PMA_"``, so every setting
is already addressable from the environment - a sweep is a subprocess with a
different environment, not a framework. stdlib only: no optuna, no wandb, no
MLflow. Section 6's dependency policy wants a measured bottleneck before a
dependency, and there is none.

    .venv\\Scripts\\python.exe scripts/autoresearch.py \\
        --sweep PMA_CHUNK_SIZE=512,1024,2048 --builds 3 \\
        --gen-models gemma4-local,gemma2-2b

**Three builds per configuration is the default and it is not paranoia.**
Chunk ids are assigned in completion order by a concurrent pipeline, so ties
resolve differently per build; 8.7d records the same configuration measuring
0.509 on one run and 0.634 on another, and a conclusion drawn from one run being
wrong as a result. Configurations are compared on their *floor*, not their mean,
for the same reason.

The journal is append-only JSONL and `--resume` skips rows already in it, because
an overnight run on this machine will hit an intermittent file lock sooner or
later (section 13, and the exFAT corruption behind it).
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess  # nosec B404 - fixed argv, no shell, no user input on the path
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVAL = REPO / "scripts" / "eval_chunking.py"


def _parse_sweep(specs: list[str]) -> dict[str, list[str]]:
    """``["PMA_CHUNK_SIZE=512,1024"]`` -> ``{"PMA_CHUNK_SIZE": ["512", "1024"]}``."""
    out: dict[str, list[str]] = {}
    for spec in specs:
        if "=" not in spec:
            raise SystemExit(f"--sweep needs NAME=v1,v2 form, got {spec!r}")
        name, _, values = spec.partition("=")
        name = name.strip()
        if not name.startswith("PMA_"):
            raise SystemExit(
                f"{name!r} is not a PMA_ setting. Only app/config.py knobs are sweepable; "
                "anything else would change the run without appearing in provenance."
            )
        out[name] = [v.strip() for v in values.split(",") if v.strip()]
    return out


def _configs(sweep: dict[str, list[str]]) -> list[dict[str, str]]:
    if not sweep:
        return [{}]
    names = sorted(sweep)
    return [
        dict(zip(names, combo, strict=True))
        for combo in itertools.product(*(sweep[n] for n in names))
    ]


def _key(config: dict[str, str], build: int, gen: str = "") -> str:
    """Identity of one experiment, for --resume.

    `gen` is part of it because a delivery-only screening run and a generation
    run of the SAME config measure different things. Without it, --resume would
    see the cheap run and skip the expensive one, and the leaderboard would
    average the two together.
    """
    return json.dumps({"config": config, "build": build, "gen": gen}, sort_keys=True)


def _done(journal: Path) -> set[str]:
    """Keys already recorded. Malformed trailing lines are ignored, not fatal:
    a killed run can leave a half-written line and that must not block a resume."""
    if not journal.exists():
        return set()
    seen = set()
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        seen.add(_key(row["config"], row["build"], row.get("gen", "")))
    return seen


def _summarise(payload: dict) -> dict:
    """Pull the comparable numbers out of one eval_chunking.py result.

    Generation recall is the primary scalar - see tests/eval/metrics.py for why
    it is recall and not F1. Delivery coverage is kept per model_class because
    that is where 2026-09-02 found chunk_size=2048 delivering 1.000 to cloud and
    0.437 to 3b_local off the same retrieval.
    """
    out: dict = {}
    for arm, block in payload.get("arms", {}).items():
        if "skipped" in block:
            out[arm] = {"skipped": block["skipped"]}
            continue
        entry: dict = {
            "document_ndcg": block.get("document", {}).get("ndcg"),
            "chunk": block.get("chunk", {}),
            "delivery_by_class": {
                cls: {"coverage": v.get("context_coverage"), "tokens": v.get("context_tokens")}
                for cls, v in block.get("delivery_by_class", {}).items()
            },
        }
        gen: dict = {}
        for tag, g in (block.get("generation") or {}).items():
            if "skipped" in g:
                gen[tag] = {"skipped": g["skipped"]}
                continue
            rows = [r for r in g["per_query"].values() if "error" not in r]
            errors = len(g["per_query"]) - len(rows)
            gen[tag] = (
                {
                    "model_class": g["model_class"],
                    "recall": sum(r["recall"] for r in rows) / len(rows),
                    "recall_answered": (
                        sum(r["recall"] for r in rows if not r["empty"])
                        / len([r for r in rows if not r["empty"]])
                        if any(not r["empty"] for r in rows)
                        else 0.0
                    ),
                    "f1": sum(r["f1"] for r in rows) / len(rows),
                    "abstained": sum(r["abstained"] for r in rows),
                    "empty": sum(r["empty"] for r in rows),
                    "errors": errors,
                }
                if rows
                else {"model_class": g["model_class"], "errors": errors, "recall": None}
            )
        entry["generation"] = gen
        out[arm] = entry
    return out


def _run_one(config: dict[str, str], k: int, gen_models: str, gen_max_tokens: int) -> dict:
    env = os.environ.copy()
    env.update(config)
    # Section 6: .env on this machine sets split_brain, which a default install
    # never does. Measuring through it makes the numbers non-representative.
    env.setdefault("PMA_LANCEDB_MODE", "portable")
    # Block buffering hides all progress when stdout is a pipe.
    env["PYTHONUNBUFFERED"] = "1"

    with tempfile.TemporaryDirectory(prefix="pma_ar_") as tmp:
        out_json = Path(tmp) / "result.json"
        argv = [
            sys.executable,
            str(EVAL),
            "-k",
            str(k),
            "--json-out",
            str(out_json),
            "--gen-max-tokens",
            str(gen_max_tokens),
        ]
        if gen_models:
            argv += ["--gen-models", gen_models]
        started = time.perf_counter()
        proc = subprocess.run(  # nosec B603 - fixed argv, shell=False
            argv, cwd=str(REPO), env=env, capture_output=True, text=True
        )
        elapsed = round(time.perf_counter() - started, 1)
        if proc.returncode != 0 or not out_json.exists():
            return {
                "failed": True,
                "returncode": proc.returncode,
                "seconds": elapsed,
                "stderr_tail": proc.stderr[-2000:],
            }
        payload = json.loads(out_json.read_text(encoding="utf-8"))
    return {
        "failed": False,
        "seconds": elapsed,
        "provenance": payload.get("provenance", {}),
        "summary": _summarise(payload),
    }


def _leaderboard(journal: Path) -> str:
    """Rank configurations by the FLOOR of generation recall across builds.

    The floor, not the mean, and 8.7d is why: a fusion weight whose mean was
    better had a floor equal to doing nothing at all, so on one build in three it
    bought nothing. A configuration is only as good as its worst build.
    """
    rows = []
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("result", {}).get("failed"):
            continue
        grouped.setdefault(json.dumps(row["config"], sort_keys=True), []).append(row)

    lines = [f"{'config':<34} {'n':>2} {'model':<20} {'recall min':>11} {'mean':>7}", "-" * 80]
    for cfg, group in sorted(grouped.items()):
        tags: dict[str, list[float]] = {}
        for row in group:
            for arm in row["result"]["summary"].values():
                for tag, g in (arm.get("generation") or {}).items():
                    if g.get("recall") is not None:
                        tags.setdefault(tag, []).append(g["recall"])
        if not tags:
            lines.append(f"{cfg:<34} {len(group):>2} {'(no generation arm)':<20}")
            continue
        for tag, vals in sorted(tags.items()):
            lines.append(
                f"{cfg:<34} {len(group):>2} {tag:<20} "
                f"{min(vals):>11.3f} {sum(vals) / len(vals):>7.3f}"
            )
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Sweep PMA settings and journal the results.")
    p.add_argument(
        "--sweep",
        action="append",
        default=[],
        metavar="PMA_NAME=v1,v2",
        help="a setting and the values to try; repeat for a grid",
    )
    p.add_argument(
        "--builds", type=int, default=3, help="independent builds per config (default 3)"
    )
    p.add_argument("-k", type=int, default=10)
    p.add_argument("--gen-models", default="", help="passed through to eval_chunking.py")
    p.add_argument("--gen-max-tokens", type=int, default=4096)
    p.add_argument("--journal", type=Path, default=REPO / "research" / "journal.jsonl")
    p.add_argument(
        "--resume", action="store_true", help="skip config/build pairs already journalled"
    )
    p.add_argument("--leaderboard", action="store_true", help="print the journal ranking and exit")
    args = p.parse_args()

    args.journal.parent.mkdir(parents=True, exist_ok=True)

    if args.leaderboard:
        if not args.journal.exists():
            print(f"no journal at {args.journal}")
            return 1
        print(_leaderboard(args.journal))
        return 0

    configs = _configs(_parse_sweep(args.sweep))
    done = _done(args.journal) if args.resume else set()
    planned = [(c, b) for c in configs for b in range(args.builds)]
    todo = [(c, b) for c, b in planned if _key(c, b, args.gen_models) not in done]
    print(
        f"{len(configs)} config(s) x {args.builds} build(s) = {len(planned)}; {len(todo)} to run\n"
    )

    for i, (config, build) in enumerate(todo, 1):
        label = ", ".join(f"{k}={v}" for k, v in sorted(config.items())) or "(defaults)"
        print(f"[{i}/{len(todo)}] {label} build {build} ...", flush=True)
        result = _run_one(config, args.k, args.gen_models, args.gen_max_tokens)
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "config": config,
            "build": build,
            "gen": args.gen_models,
            "result": result,
        }
        with args.journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        if result["failed"]:
            print(f"    FAILED rc={result['returncode']} in {result['seconds']}s")
            print("    " + result["stderr_tail"].replace("\n", "\n    ")[-800:])
        else:
            print(f"    ok in {result['seconds']}s")

    if args.journal.exists():
        print()
        print(_leaderboard(args.journal))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
