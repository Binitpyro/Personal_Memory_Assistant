"""Build the query-type fixture: code-identifier, navigation and structured-lookup queries.

Research_Paper_1's central claim is that optimal RRF k varies ~15x across five
query *types* (section 4.1, Table 4). Every fusion measurement in this repo is on
one type - semantic prose (SQuAD, SciFact) - so the claim has never been
testable. CLAUDE.md 8.3a pre-registers the test this fixture feeds, and its
PASS/FAIL rule, before any run.

Three classes, every label true by construction and then re-verified from disk:

* **code_identifier** - a function or class name defined in exactly ONE file of
  a pinned snapshot of this repo's own Python (MIT, so no licence exposure).
  Relevant = the defining file. Callers and tests stay in as distractors.
* **navigation** - a known document by path fragment (code modules) or by title
  (the SQuAD articles). Unique across every filename of the corpora it is
  composed with in the pre-registered run, not only its own.
* **structured_lookup** - seeded synthetic invoices and calendar events, one JSON
  per file, fictional vendors and people. Kept only if the query's fields match
  exactly one record.

hybrid_intent is not built: the paper itself predicts it flat (Table 7).

**Synthetic register, stated rather than discovered.** Templates wrote these
queries, not people. A class whose optimal k is really its template's is the
first thing to rule out, so every query carries its template in `note` and
`analyze_fusion_k.py` reports per-template curves.

Explicit opt-in, run by hand; nothing here runs at test time (section 11).

    .venv\\Scripts\\python.exe scripts/build_query_type_fixture.py
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import random
import re
import shutil
import subprocess  # nosec B404 - fixed argv, no shell, no user input on the path
import sys
import tarfile
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
EVAL = REPO / "tests" / "eval"
MARKER = ".qtype_fixture"
CODE_DOMAIN = "qt_code"
RECORDS_DOMAIN = "qt_records"
# The pre-registered run composes the fixture with these, so a navigation query
# has to be unique across their filenames as well as the fixture's own.
DEFAULT_UNIVERSE = ("corpus_squad", "corpus", "corpus_scifact")

# Fictional. Generic enough to collide with no one in particular.
VENDORS = (
    "Brightwater Supply",
    "Kestrel Ridge Analytics",
    "Mossgrove Print Works",
    "Tallpine Office Goods",
    "Harrowgate Cleaning",
    "Quillfeather Stationers",
    "Emberline Logistics",
    "Saltmarsh Catering",
    "Ironbark Hardware",
    "Bluefen Software",
    "Copperleaf Design",
    "Northwick Couriers",
    "Fernhollow Nursery",
    "Silverbirch Legal",
    "Oakhaven Plumbing",
    "Driftwood Media",
    "Larkspur Consulting",
    "Greystone Security",
    "Wrenfield Electrical",
    "Hazelmere Travel",
)
PEOPLE = (
    "Asha Menon",
    "Tomas Reyes",
    "Mei Lin Chen",
    "Olu Adeyemi",
    "Sofia Marchetti",
    "Jonah Whitfield",
    "Priya Raman",
    "Erik Lindqvist",
    "Nadia Haddad",
    "Kenji Watanabe",
    "Clara Dubois",
    "Mateo Alvarez",
    "Hana Novak",
    "Rafael Costa",
    "Leila Farahani",
    "Owen Gallagher",
    "Ines Moreau",
    "Dev Patel",
    "Yara Nasser",
    "Lukas Brenner",
)
ITEMS = (
    "printer paper",
    "consulting hours",
    "server hosting",
    "office chairs",
    "cleaning service",
    "courier delivery",
    "software licence",
    "catering",
    "security audit",
    "travel booking",
)
LOCATIONS = ("Room 2B", "Main office", "Video call", "Cafe downstairs", "Client site")
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_STOP = frozenset(
    {"module", "article", "py", "txt", "json", "md", "the", "of", "and", "a", "an", "in", "on"}
)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", text.lower())) - _STOP


def _query(
    prefix: str,
    n: int,
    text: str,
    qclass: str,
    relevant: str,
    domain: str,
    template: str,
    match: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": f"{prefix}-{n:03d}",
        "query": text,
        "type": qclass,
        "relevant_files": [relevant],
        "expected_domains": [domain],
        "note": template,
        # Not read by the harness. It is what `verify` re-checks from disk.
        "match": match,
    }


# ---------------------------------------------------------------------------
# code_identifier
# ---------------------------------------------------------------------------


def snapshot_code(ref: str) -> tuple[str, dict[str, str]]:
    """Tracked .py under app/, scripts/ and tests/ (minus tests/eval/) at `ref`."""
    git = shutil.which("git")
    if not git:
        raise SystemExit("git not found on PATH")

    def run(*argv: str) -> bytes:
        proc = subprocess.run(  # nosec B603 - resolved path, fixed argv, shell=False
            [git, *argv], cwd=str(REPO), capture_output=True, timeout=120
        )
        if proc.returncode != 0:
            raise SystemExit(f"git {argv[0]} failed: {proc.stderr.decode(errors='replace')}")
        return proc.stdout

    sha = run("rev-parse", ref).decode().strip()
    archive = run("archive", "--format=tar", sha, "app", "scripts", "tests")
    files: dict[str, str] = {}
    # extractfile reads members into memory; nothing is extracted to a path, so
    # a hostile member name cannot escape (and this is our own archive).
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            name = member.name
            if member.isfile() and name.endswith(".py") and not name.startswith("tests/eval/"):
                handle = tar.extractfile(member)
                if handle is not None:
                    files[name] = handle.read().decode("utf-8", errors="replace")
    return sha, files


def definitions(files: dict[str, str]) -> dict[str, dict[str, str]]:
    """name -> {file: "def" | "class"}, nested definitions included."""
    defs: dict[str, dict[str, str]] = defaultdict(dict)
    for path, source in files.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                defs[node.name][path] = "def"
            elif isinstance(node, ast.ClassDef):
                defs[node.name][path] = "class"
    return defs


def code_queries(
    files: dict[str, str], per_template: int, rng: random.Random, unique_only: bool = True
) -> list[dict[str, Any]]:
    defs = definitions(files)
    names = sorted(
        n
        for n, where in defs.items()
        if len(n) >= 8
        and not n.startswith("__")
        and not n.startswith("test_")
        and (len(where) == 1 or not unique_only)
    )
    rng.shuffle(names)
    templates = ("id_bare", "id_def", "id_call")
    out: list[dict[str, Any]] = []
    for name in names:
        if len(out) == per_template * len(templates):
            break
        path = sorted(defs[name])[0]
        template = templates[len(out) % len(templates)]
        text = {
            "id_bare": name,
            "id_def": f"{defs[name][path]} {name}",
            "id_call": f"{name}(",
        }[template]
        # The paper's code-identifier is a token "expected to match verbatim in
        # code" (4.1). `Name(` is absent from a file whose class is declared
        # `class Name:`, so that symbol is skipped rather than mislabelled.
        if text not in files[path]:
            continue
        out.append(
            _query(
                "qtcode",
                len(out),
                text,
                "code_identifier",
                f"{CODE_DOMAIN}/{path}",
                CODE_DOMAIN,
                template,
                {"symbol": name},
            )
        )
    return out


# ---------------------------------------------------------------------------
# navigation
# ---------------------------------------------------------------------------


def navigation_queries(
    code_paths: Iterable[str],
    squad_titles: Iterable[str],
    universe: Sequence[str],
    per_side: int,
    rng: random.Random,
    unique_only: bool = True,
) -> list[dict[str, Any]]:
    token_sets = [_tokens(u) for u in universe]

    def unique(tokens: frozenset[str]) -> bool:
        return bool(tokens) and sum(1 for t in token_sets if tokens <= t) == 1

    modules: list[tuple[str, str, frozenset[str]]] = []
    for path in sorted(code_paths):
        p = Path(path)
        if p.stem == "__init__" or len(p.parts) < 2:
            continue
        text = f"{p.parts[-2].replace('_', ' ')} {p.stem.replace('_', ' ')} module"
        tokens = _tokens(text)
        if unique(tokens) or not unique_only:
            modules.append((text, f"{CODE_DOMAIN}/{path}", tokens))
    rng.shuffle(modules)

    titles: list[tuple[str, str, str, frozenset[str]]] = []
    ordered = sorted(squad_titles)
    rng.shuffle(ordered)
    for i, stem in enumerate(ordered):
        words = stem.replace("_", " ").split()
        # Alternate full and partial titles; a partial title needs 3+ words so
        # dropping one still leaves a phrase.
        if i % 2 and len(words) >= 3:
            text = " ".join(words[: len(words) // 2] + words[len(words) // 2 + 1 :])
            template = "nav_title_partial"
        else:
            text = f"{' '.join(words)} article"
            template = "nav_title_full"
        tokens = _tokens(text)
        if unique(tokens) or not unique_only:
            titles.append((text, f"squad/{stem}.txt", template, tokens))

    out: list[dict[str, Any]] = []
    for text, rel, tokens in modules[:per_side]:
        match = {"tokens": sorted(tokens)}
        out.append(
            _query(
                "qtnav", len(out), text, "navigation", rel, CODE_DOMAIN, "nav_code_module", match
            )
        )
    for text, rel, template, tokens in titles[:per_side]:
        match = {"tokens": sorted(tokens)}
        out.append(_query("qtnav", len(out), text, "navigation", rel, "squad", template, match))
    return out


# ---------------------------------------------------------------------------
# structured_lookup
# ---------------------------------------------------------------------------


def build_records(n: int, rng: random.Random) -> list[tuple[str, dict[str, Any]]]:
    start = date(2024, 1, 1)
    used_ids: set[str] = set()
    records: list[tuple[str, dict[str, Any]]] = []
    for i in range(n):
        day = start + timedelta(days=rng.randrange(731))
        shown = f"{day.day} {MONTHS[day.month - 1]} {day.year}"
        rec: dict[str, Any]
        if rng.random() < 0.7:
            # The id is random rather than i, so no token of it appears in the
            # record_NNNN filename and an id lookup cannot win on the path.
            invoice_id = f"INV-{rng.randrange(100000, 1000000)}"
            while invoice_id in used_ids:
                invoice_id = f"INV-{rng.randrange(100000, 1000000)}"
            used_ids.add(invoice_id)
            rec = {
                "record_type": "invoice",
                "invoice_id": invoice_id,
                "vendor": rng.choice(VENDORS),
                "date": day.isoformat(),
                "date_display": shown,
                "amount": f"{rng.randrange(5_000, 500_000) / 100:,.2f}",
                "currency": "USD",
                "status": rng.choice(("paid", "due", "overdue")),
                "items": [
                    {"description": rng.choice(ITEMS), "quantity": rng.randrange(1, 20)}
                    for _ in range(rng.randrange(1, 4))
                ],
            }
        else:
            person = rng.choice(PEOPLE)
            rec = {
                "record_type": "calendar_event",
                "title": f"Meeting with {person}",
                "person": person,
                "date": day.isoformat(),
                "date_display": shown,
                "start": f"{rng.randrange(8, 18):02d}:{rng.choice(('00', '30'))}",
                "location": rng.choice(LOCATIONS),
            }
        records.append((f"{RECORDS_DOMAIN}/record_{i:04d}.json", rec))
    return records


def matches(record: dict[str, Any], match: dict[str, Any]) -> bool:
    return all(
        (str(record.get("date", ""))[:7] == value)
        if field == "month"
        else record.get(field) == value
        for field, value in match.items()
    )


def _record_candidate(template: str, rec: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if template == "rec_meeting":
        if rec["record_type"] != "calendar_event":
            return None
        return (
            f"meeting with {rec['person']} on {rec['date']}",
            {"record_type": "calendar_event", "person": rec["person"], "date": rec["date"]},
        )
    if rec["record_type"] != "invoice":
        return None
    if template == "rec_vendor_month":
        month = MONTHS[int(rec["date"][5:7]) - 1]
        return (
            f"invoice from {rec['vendor']} {month} {rec['date'][:4]}",
            {"record_type": "invoice", "vendor": rec["vendor"], "month": rec["date"][:7]},
        )
    if template == "rec_invoice_id":
        return (
            f"invoice {rec['invoice_id']}",
            {"record_type": "invoice", "invoice_id": rec["invoice_id"]},
        )
    return (
        f"{rec['amount']} {rec['vendor']}",
        {"record_type": "invoice", "vendor": rec["vendor"], "amount": rec["amount"]},
    )


def record_queries(
    records: Sequence[tuple[str, dict[str, Any]]],
    per_template: int,
    rng: random.Random,
    unique_only: bool = True,
) -> list[dict[str, Any]]:
    order = list(range(len(records)))
    rng.shuffle(order)
    out: list[dict[str, Any]] = []
    for template in ("rec_vendor_month", "rec_invoice_id", "rec_amount_vendor", "rec_meeting"):
        taken = 0
        for i in order:
            if taken == per_template:
                break
            path, rec = records[i]
            got = _record_candidate(template, rec)
            if got is None:
                continue
            text, match = got
            if unique_only and sum(1 for _, r in records if matches(r, match)) != 1:
                continue
            out.append(
                _query(
                    "qtrec",
                    len(out),
                    text,
                    "structured_lookup",
                    path,
                    RECORDS_DOMAIN,
                    template,
                    match,
                )
            )
            taken += 1
    return out


# ---------------------------------------------------------------------------
# Build and verify
# ---------------------------------------------------------------------------


def build(
    out_dir: Path,
    code_files: dict[str, str],
    squad_titles: Sequence[str],
    universe_extra: Sequence[str],
    per_class: int,
    n_records: int,
    seed: int,
    unique_only: bool = True,
) -> list[dict[str, Any]]:
    """Write the fixture's files under `out_dir` and return its queries."""
    rng = random.Random(seed)  # noqa: S311 - reproducible fixture, not security
    records = build_records(n_records, rng)
    universe = (
        [f"{CODE_DOMAIN}/{p}" for p in code_files] + [p for p, _ in records] + list(universe_extra)
    )
    queries = (
        code_queries(code_files, per_class // 3, rng, unique_only)
        + navigation_queries(code_files, squad_titles, universe, per_class // 2, rng, unique_only)
        + record_queries(records, per_class // 4, rng, unique_only)
    )

    out_dir.mkdir(parents=True)
    (out_dir / MARKER).write_text("written by scripts/build_query_type_fixture.py\n", "utf-8")
    for path, source in code_files.items():
        target = out_dir / CODE_DOMAIN / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    (out_dir / RECORDS_DOMAIN).mkdir()
    for path, rec in records:
        (out_dir / path).write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return queries


def verify(
    out_dir: Path, queries: Sequence[dict[str, Any]], universe_extra: Sequence[str]
) -> list[str]:
    """Re-derive every label from what is on disk. Empty list = every label holds.

    Deliberately does not reuse the generator's in-memory state: a filter that
    was skipped or wrong while generating shows up here as a label that matches
    zero files or several.
    """
    code_root = out_dir / CODE_DOMAIN
    files = {
        p.relative_to(code_root).as_posix(): p.read_text(encoding="utf-8")
        for p in code_root.rglob("*.py")
    }
    defs = definitions(files)
    records = {
        p.relative_to(out_dir).as_posix(): json.loads(p.read_text(encoding="utf-8"))
        for p in (out_dir / RECORDS_DOMAIN).glob("*.json")
    }
    universe = [f"{CODE_DOMAIN}/{p}" for p in files] + list(records) + list(universe_extra)
    token_sets = {u: _tokens(u) for u in universe}

    errors: list[str] = []
    for q in queries:
        match = q["match"]
        if q["type"] == "code_identifier":
            found = sorted(f"{CODE_DOMAIN}/{p}" for p in defs.get(match["symbol"], {}))
            source = files.get(q["relevant_files"][0].removeprefix(f"{CODE_DOMAIN}/"), "")
            if q["query"] not in source:
                errors.append(f"{q['id']} {q['query']!r}: not verbatim in its relevant file")
        elif q["type"] == "navigation":
            want = frozenset(match["tokens"])
            found = sorted(u for u, t in token_sets.items() if want <= t)
        else:
            found = sorted(p for p, rec in records.items() if matches(rec, match))
        if found != q["relevant_files"]:
            errors.append(
                f"{q['id']} {q['query']!r}: labelled {q['relevant_files']}, matches {len(found)}"
                f" file(s) {found[:3]}"
            )
    return errors


def _universe_files() -> list[str]:
    out: list[str] = []
    for name in DEFAULT_UNIVERSE:
        root = EVAL / name
        if root.is_dir():
            out += [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Build the query-type fixture for adaptive-k RRF.")
    p.add_argument("--ref", default="HEAD", help="git ref to snapshot the code corpus at")
    p.add_argument("--out", default=str(EVAL / "corpus_qtype"))
    p.add_argument("--queries-out", default=str(EVAL / "queries_qtype.json"))
    p.add_argument("--per-class", type=int, default=60)
    p.add_argument(
        "--records",
        type=int,
        default=800,
        help="structured records; the saturation rule may double this once (CLAUDE.md 8.3a)",
    )
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--force", action="store_true", help="rebuild an existing fixture dir")
    args = p.parse_args()

    out = Path(args.out)
    if out.exists():
        if not args.force:
            raise SystemExit(f"{out} exists; pass --force to rebuild it")
        # Only ever delete a directory this script wrote (CLAUDE.md 11).
        if not (out / MARKER).is_file():
            raise SystemExit(f"{out} has no {MARKER} marker; refusing to delete it")
        shutil.rmtree(out)

    squad_dir = EVAL / "corpus_squad" / "squad"
    if not squad_dir.is_dir():
        raise SystemExit("tests/eval/corpus_squad is missing: run scripts/fetch_squad_corpus.py")
    sha, code_files = snapshot_code(args.ref)
    squad_titles = sorted(t.stem for t in squad_dir.glob("*.txt"))
    universe_extra = _universe_files()

    queries = build(
        out, code_files, squad_titles, universe_extra, args.per_class, args.records, args.seed
    )
    errors = verify(out, queries, universe_extra)
    counts: dict[str, int] = defaultdict(int)
    for q in queries:
        counts[f"{q['type']}/{q['note']}"] += 1
    print(f"snapshot {sha[:12]}: {len(code_files)} code files, {args.records} records")
    for key in sorted(counts):
        print(f"  {key:<40} {counts[key]:>4}")
    if errors:
        print(f"{len(errors)} label(s) failed verification:")
        for e in errors[:20]:
            print(f"  {e}")
        return 1

    payload = {
        "_readme": [
            "Ground truth for tests/eval/corpus_qtype, composed with corpus_squad for the",
            "navigation titles. Generated by scripts/build_query_type_fixture.py - never",
            "hand-edit. `note` is the query template; `match` is what verify() re-checks.",
        ],
        "_provenance": {
            "generator": "scripts/build_query_type_fixture.py",
            "git_sha": sha,
            "seed": args.seed,
            "per_class": args.per_class,
            "records": args.records,
            "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "counts": dict(sorted(counts.items())),
        },
        "queries": queries,
    }
    Path(args.queries_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {args.queries_out} ({len(queries)} queries, 0 label errors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
