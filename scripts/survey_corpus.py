"""Survey what PMA actually extracts from a real folder.

    .venv\\Scripts\\python.exe scripts\\survey_corpus.py --corpus "C:\\path\\to\\folder"

The retrieval eval runs on tests/eval/corpus - 16 .md and 8 .py, hand written.
PMA ships extractors for PDF, PPTX, DOCX, EPUB, CSV, XLSX and JSON and *none of
them* is exercised by it, so the only evidence those formats work is unit tests
over small fixtures. This points the real indexing pipeline at a real folder and
reports what came out.

It answers one question: can this corpus support a retrieval metric? A format
that yields nothing, or yields a page number and a slide title, cannot.

Reads the corpus, writes nothing to it. The index is a throwaway temp directory,
removed on exit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.indexing.service import _STUB_PREFIXES
from tests.eval import harness

# sha256 sentinels a file carries when it produced no usable content. Kept in
# sync with _INCOMPLETE_SHA_STATES in app/indexing/service.py; "" is the
# header's placeholder and the pre-migration default.
_EMPTY_STATES = {"", "ERROR", "CANCELLED", "NOCONTENT"}


def _peak_working_set_mb() -> float:
    """Peak working set of this process, or 0.0 off Windows.

    GetProcessMemoryInfo, not tracemalloc: the ONNX arena and numpy buffers are
    invisible to tracemalloc, which is most of the cost during indexing (see the
    RAM budget note in CLAUDE.md section 6).

    The platform check is what makes the "off Windows" half of that promise hold
    for mypy as well as at runtime: typeshed gates ctypes.WinDLL behind
    sys.platform, so checking this file for Linux fails on it without the guard.
    The `except` below would swallow it anyway, which is exactly why it went
    unnoticed on a Windows-only dev box - same guard as
    scripts/profile_ingest_memory.py.
    """
    if sys.platform != "win32":
        return 0.0
    try:
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        # restype/argtypes are load-bearing, not decoration. GetCurrentProcess
        # returns the pseudo-handle (HANDLE)-1; with ctypes' default 32-bit int
        # restype it is truncated, the call fails, and the silent `except` below
        # reports a plausible-looking 0.0 MB.
        kernel32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        if psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            return float(counters.PeakWorkingSetSize) / (1024 * 1024)
    except Exception:
        pass
    return 0.0


def _classify(sha: str, text: str) -> str:
    """Exactly one bucket per file, so the counts add up to the file total."""
    if sha in _EMPTY_STATES:
        return "no content"
    for prefix in _STUB_PREFIXES:
        if text.startswith(prefix):
            return f"stub {prefix.rstrip(':').lstrip('[').lower()}"
    if not text.strip():
        return "empty text"
    return "extracted"


async def survey(corpus: Path) -> dict:
    on_disk: dict[str, int] = defaultdict(int)
    for path in corpus.rglob("*"):
        if path.is_file():
            on_disk[path.suffix.lower() or "(none)"] += 1

    t0 = time.perf_counter()
    index = await harness.EvalIndex(corpus_dir=corpus).build()
    elapsed = time.perf_counter() - t0
    assert index.db is not None, "EvalIndex.build() did not open a database"

    try:
        rows = await index.db.execute_query(
            "SELECT f.id, f.path, f.type, f.size, f.sha256, "
            "       COUNT(c.id) AS chunks, "
            "       COALESCE(SUM(LENGTH(zlib_decompress(c.text_preview))), 0) AS chars "
            "FROM files f LEFT JOIN chunks c ON c.file_id = f.id "
            "GROUP BY f.id ORDER BY f.path"
        )
        # Second pass for the stub markers. rust_core's read-failure stub and the
        # encrypted-document notices sit at the head of the first chunk, which a
        # GROUP BY cannot return alongside the aggregates.
        head_rows = await index.db.execute_query(
            "SELECT file_id, SUBSTR(zlib_decompress(text_preview), 1, 40) FROM chunks "
            "WHERE id IN (SELECT MIN(id) FROM chunks GROUP BY file_id)"
        )
        heads = {fid: (text or "") for fid, text in head_rows}
    finally:
        await index.close()

    per_format: dict[str, dict] = defaultdict(
        lambda: {"indexed": 0, "buckets": defaultdict(int), "chars": [], "chunks": 0}
    )
    files = []
    for fid, path, ftype, size, sha, chunks, chars in rows:
        bucket = _classify(sha or "", heads.get(fid, ""))
        ext = (ftype or Path(path).suffix or "(none)").lower()
        if not ext.startswith("."):
            ext = "." + ext
        slot = per_format[ext]
        slot["indexed"] += 1
        slot["buckets"][bucket] += 1
        slot["chunks"] += chunks
        if bucket == "extracted":
            slot["chars"].append(chars)
        files.append(
            {
                "path": Path(path).name,
                "ext": ext,
                "size": size,
                "bucket": bucket,
                "chunks": chunks,
                "chars": chars,
            }
        )

    return {
        "corpus": str(corpus),
        "on_disk": dict(on_disk),
        "elapsed_s": round(elapsed, 1),
        "peak_working_set_mb": round(_peak_working_set_mb(), 1),
        "per_format": {
            ext: {
                "indexed": v["indexed"],
                "chunks": v["chunks"],
                "buckets": dict(v["buckets"]),
                "median_chars": int(statistics.median(v["chars"])) if v["chars"] else 0,
                "min_chars": min(v["chars"]) if v["chars"] else 0,
            }
            for ext, v in sorted(per_format.items())
        },
        "files": files,
    }


def report(data: dict) -> str:
    lines: list[str] = []
    on_disk_total = sum(data["on_disk"].values())
    indexed_total = sum(v["indexed"] for v in data["per_format"].values())

    lines.append(f"corpus            : {data['corpus']}")
    lines.append(f"files on disk     : {on_disk_total}")
    lines.append(f"files indexed     : {indexed_total}")
    lines.append(f"wall clock        : {data['elapsed_s']} s")
    lines.append(f"peak working set  : {data['peak_working_set_mb']} MB")
    lines.append("")
    lines.append(
        f"{'ext':<8}{'on disk':>8}{'indexed':>9}{'chunks':>8}{'med chars':>11}"
        f"{'min chars':>11}  outcome"
    )
    lines.append("-" * 88)
    for ext, v in data["per_format"].items():
        outcome = ", ".join(f"{n} {b}" for b, n in sorted(v["buckets"].items()))
        lines.append(
            f"{ext:<8}{data['on_disk'].get(ext, 0):>8}{v['indexed']:>9}{v['chunks']:>8}"
            f"{v['median_chars']:>11}{v['min_chars']:>11}  {outcome}"
        )

    skipped = {ext: n for ext, n in data["on_disk"].items() if ext not in data["per_format"]}
    if skipped:
        lines.append("")
        lines.append("on disk but never indexed (unsupported extension):")
        for ext, n in sorted(skipped.items()):
            lines.append(f"  {ext:<8} {n}")

    thin = [f for f in data["files"] if f["bucket"] == "extracted" and f["chars"] < 500]
    if thin:
        lines.append("")
        lines.append(f"extracted but under 500 chars ({len(thin)}) - suspect silent failure:")
        for f in sorted(thin, key=lambda x: x["chars"])[:15]:
            lines.append(f"  {f['chars']:>6} chars  {f['ext']:<6} {f['path'][:60]}")

    empty = [f for f in data["files"] if f["bucket"] != "extracted"]
    if empty:
        lines.append("")
        lines.append(f"produced nothing ({len(empty)}):")
        for f in sorted(empty, key=lambda x: x["path"])[:20]:
            lines.append(f"  {f['bucket']:<16} {f['ext']:<6} {f['path'][:60]}")

    return "\n".join(lines)


def _arm_hang_dumper(interval_s: float) -> None:
    """Print every thread's Python stack every `interval_s`, forever.

    stdlib faulthandler, no new dependency and no profiler to install. The point
    is the *thread* stacks: this pipeline hands work to two thread pools, and a
    worker parked in an untimed blocking call is invisible from the asyncio side
    - the process just stops at zero CPU. dump_traceback_later names the frame.
    """
    import faulthandler

    faulthandler.enable()
    faulthandler.dump_traceback_later(interval_s, repeat=True, exit=False)


async def main_async(args) -> int:
    corpus = Path(args.corpus).resolve()
    if not corpus.is_dir():
        print(f"not a directory: {corpus}", file=sys.stderr)
        return 2

    data = await survey(corpus)
    print(report(data))

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--corpus", required=True, help="Folder to survey. Read only.")
    p.add_argument(
        "--debug-hang",
        type=float,
        metavar="SECONDS",
        help="Dump every thread's stack this often, to diagnose a stall. Try 600.",
    )
    p.add_argument(
        "--json-out",
        type=Path,
        help="Write the full per-file result. Put it under eval-local/ - gitignored.",
    )
    args = p.parse_args()
    if args.debug_hang:
        _arm_hang_dumper(args.debug_hang)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
