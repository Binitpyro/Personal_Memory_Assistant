"""Phase-resolved working-set profile of a full ingestion run.

    .venv/Scripts/python.exe scripts/profile_ingest_memory.py --corpus tests/fixtures/perf_corpus

Answers one question: where does the resident memory of an index run actually
go, and how does the peak compare to the ceilings in CLAUDE.md section 6.

Method is deliberately the one those ceilings were measured with -
GetProcessMemoryInfo working set, sampled on a thread, **not** tracemalloc.
tracemalloc cannot see the ONNX Runtime arena or the numpy buffers, which is
most of the cost; scripts/memory_profiler.py measures tracemalloc deltas around
embed_texts and is not comparable to anything reported here.

Reports the import ladder, the idle plateau once the embedder is resident, the
peak, when the peak happened, and which pipeline phase was running at the time.
"Peak above idle" is the number section 6 actually bounds, and a peak in the
tail means something very different from a peak during embedding.

Reads the corpus, writes nothing to it. The index is a throwaway temp directory.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import shutil
import statistics
import sys
import tempfile
import threading
import time
from collections import defaultdict
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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


_MB = 1024.0 * 1024.0


def _make_reader():
    """Return a callable giving (current_ws_mb, peak_ws_mb), or None off Windows.

    restype/argtypes are load-bearing, not decoration. GetCurrentProcess returns
    the pseudo handle (HANDLE)-1; with the default 32-bit int restype it is
    truncated, the call fails, and a plausible-looking 0.0 comes back instead.
    """
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32")
    psapi = ctypes.WinDLL("psapi")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_Counters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel32.GetCurrentProcess()

    def read() -> tuple[float, float]:
        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / _MB, counters.PeakWorkingSetSize / _MB
        return 0.0, 0.0

    return read


# progress.current_file is the only phase signal the pipeline already emits.
# Mapped to stable bucket names so the report does not key on wording; "Phase
# 2/3" is what the embedder calls itself (service.py report_progress).
_PHASE_PREFIXES = (
    ("Extracting", "extract"),
    ("Phase 2/3", "embed"),
    ("Pipelined Indexing", "pipeline start"),
    ("Scanning", "scan"),
    ("Resolving", "graph edges"),
    ("Checkpointing", "wal checkpoint"),
)


def _phase() -> str:
    """Coarse pipeline phase, read off the progress object the pipeline maintains."""
    try:
        from app.indexing.service import progress
    except Exception:
        return "startup"
    if progress.status != "running":
        return "idle"
    label = (progress.current_file or "").strip()
    for prefix, bucket in _PHASE_PREFIXES:
        if label.startswith(prefix):
            return bucket
    # Anything else is the storer: _storer_worker calls
    # progress.update(..., current_file=item["path"].name), overwriting the
    # phase label with a bare filename. Bucketed, not passed through, or the
    # report grows one row per file.
    return "store" if label else "running"


class Sampler(threading.Thread):
    """Daemon so a crash in the run cannot leave the process unable to exit."""

    def __init__(self, read, interval_s: float = 0.25):
        super().__init__(daemon=True, name="ws-sampler")
        self._read = read
        self._interval = interval_s
        # NOT self._stop: threading.Thread._stop is a real method that join()
        # calls internally, so shadowing it makes join() raise
        # "TypeError: Event object is not callable".
        self._halt = threading.Event()
        self.samples: list[tuple[float, float, str]] = []
        self.t0 = time.perf_counter()

    def run(self) -> None:
        while not self._halt.is_set():
            current, _peak = self._read()
            self.samples.append((time.perf_counter() - self.t0, current, _phase()))
            self._halt.wait(self._interval)

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=5.0)


def _apply_ablation(mode: str, emb, lancedb) -> None:
    """Replace one pipeline stage with a cheap stub, in-process.

    Ablation, not simulation: everything upstream and downstream still runs on
    the real corpus, so the delta in peak working set is attributable to the
    stage that was removed. Two hypotheses have already died to reasoning
    (chunk volume, index_concurrency); this measures instead.

    `embed` keeps the returned shape at the real 384 dims and returns unit-norm
    vectors, so the storer and the LanceDB schema are unaffected and only the
    tokenizer plus the ONNX session are removed.
    """
    import numpy as np

    if mode in ("embed", "both"):
        rng = np.random.default_rng(0)

        async def _fake_embed(texts, batch_size=None, progress_callback=None):
            if progress_callback:
                progress_callback(1, 1)
            vecs = rng.standard_normal((len(texts), 384)).astype(np.float32)
            return vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9)

        emb.embed_texts = _fake_embed  # type: ignore[method-assign]

    if mode in ("lancedb", "both"):

        async def _noop(*_args, **_kwargs):
            return None

        lancedb.add_documents = _noop  # type: ignore[method-assign]
        lancedb.add_summaries_batch = _noop  # type: ignore[method-assign]
        lancedb.delete_documents = _noop  # type: ignore[method-assign]
        lancedb.create_hnsw_index = _noop  # type: ignore[method-assign]


async def profile(corpus: Path, split_domains: bool, interval_s: float, ablate: str) -> int:
    read = _make_reader()
    if read is None:
        print("This profiler is Windows-only (GetProcessMemoryInfo).", file=sys.stderr)
        return 2

    ladder: list[tuple[str, float]] = []

    def mark(name: str) -> None:
        ladder.append((name, read()[0]))

    mark("interpreter + argparse")
    sampler = Sampler(read, interval_s)
    sampler.start()

    from app.config import settings
    from app.embeddings.service import EmbeddingService
    from app.indexing.service import IndexingService, shutdown_executors
    from app.storage.db import DatabaseManager
    from app.vector_store.lancedb_client import LanceDBClient

    mark("app imports")

    workdir = Path(tempfile.mkdtemp(prefix="pma_memprof_"))
    settings.db_path = str(workdir / "prof.db")
    settings.lancedb_persist_dir = str(workdir / "lancedb")

    db = DatabaseManager(settings.db_path)
    await db.connect()
    await db.init_db(schema_path="app/storage/schema.sql")
    mark("sqlite connected")

    lancedb = LanceDBClient(persist_directory=settings.lancedb_persist_dir)
    lancedb.connect()
    mark("lancedb connected")

    emb = EmbeddingService()
    emb.load_model()
    mark("ONNX embedder resident")

    # After load_model on purpose: the session stays resident either way, so the
    # idle baseline is identical across ablations and the deltas are comparable.
    _apply_ablation(ablate, emb, lancedb)
    idle_ws = read()[0]

    if split_domains:
        folders = sorted(str(p) for p in corpus.iterdir() if p.is_dir())
    else:
        folders = [str(corpus)]
    if not folders:
        print(f"nothing to index under {corpus}", file=sys.stderr)
        return 2

    service = IndexingService(db, emb, lancedb)
    t_index0 = time.perf_counter() - sampler.t0
    await service.index_folders(folders)
    t_index1 = time.perf_counter() - sampler.t0

    sampler.stop()
    peak_total = read()[1]

    files = (await db.execute_query("SELECT COUNT(*) FROM files"))[0][0]
    chunks = (await db.execute_query("SELECT COUNT(*) FROM chunks"))[0][0]

    await db.close()
    shutdown_executors()
    from app.api.deps import close_all

    await close_all()
    # Not ignore_errors=True. LanceDB holds native handles it never releases,
    # and swallowing that is what hid a leaked SQLite connection for an entire
    # investigation (see CLAUDE.md 8.1d). Say what survived.
    leftovers: list[str] = []
    shutil.rmtree(workdir, onexc=lambda _fn, path, exc: leftovers.append(f"{path}: {exc!r}"))
    if leftovers:
        print(f"workdir not fully removed ({len(leftovers)}):", file=sys.stderr)
        for item in leftovers:
            print(f"  {item}", file=sys.stderr)

    _report(
        corpus,
        ladder,
        sampler.samples,
        idle_ws,
        peak_total,
        t_index0,
        t_index1,
        files,
        chunks,
        len(folders),
        ablate,
    )
    return 0


def _report(
    corpus,
    ladder,
    samples,
    idle_ws,
    peak_total,
    t_start,
    t_end,
    files,
    chunks,
    n_folders,
    ablate,
) -> None:
    during = [s for s in samples if t_start <= s[0] <= t_end]
    peak_sampled = max(during, key=lambda s: s[1]) if during else (0.0, 0.0, "-")

    per_phase: dict[str, list[float]] = defaultdict(list)
    for _t, working_set, phase in during:
        per_phase[phase].append(working_set)

    print(f"corpus                : {corpus}")
    print(f"ablation              : {ablate}")
    print(f"indexed as            : {n_folders} folder(s)")
    print(f"files / chunks        : {files} / {chunks}")
    print(f"ingest wall clock     : {t_end - t_start:.1f} s")
    print(f"samples during ingest : {len(during)}")
    print()
    print("import ladder (working set, MB)")
    for name, megabytes in ladder:
        print(f"  {megabytes:8.1f}   {name}")
    print()
    print(f"idle, embedder resident : {idle_ws:8.1f} MB")
    print(
        f"peak sampled            : {peak_sampled[1]:8.1f} MB   at t+{peak_sampled[0]:.1f}s"
        f"  (phase: {peak_sampled[2]})"
    )
    print(
        f"peak above idle         : {peak_sampled[1] - idle_ws:8.1f} MB   <- the section 6 number"
    )
    print(f"PeakWorkingSetSize      : {peak_total:8.1f} MB   (OS high-water, whole process)")
    print()
    print(f"{'phase':<22}{'samples':>9}{'median':>10}{'p95':>10}{'max':>10}")
    print("-" * 61)
    for phase, values in sorted(per_phase.items(), key=lambda kv: -max(kv[1])):
        ordered = sorted(values)
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        print(
            f"{phase:<22}{len(values):>9}{statistics.median(values):>10.1f}"
            f"{p95:>10.1f}{max(values):>10.1f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--corpus", required=True, help="Folder to index. Read only.")
    parser.add_argument(
        "--split-domains",
        action="store_true",
        help="Index each immediate subdirectory as its own folder_tag, the way "
        "tests/eval/harness.py does. The default indexes the corpus root as a single "
        "folder, which is what a flat fixture like perf_corpus needs.",
    )
    parser.add_argument("--interval", type=float, default=0.25, help="Sample period, seconds.")
    parser.add_argument(
        "--ablate",
        choices=("none", "embed", "lancedb", "both"),
        default="none",
        help="Stub out a pipeline stage to attribute the peak. The rest of the run is real.",
    )
    args = parser.parse_args()

    corpus = Path(args.corpus).resolve()
    if not corpus.is_dir():
        print(f"not a directory: {corpus}", file=sys.stderr)
        return 2
    return asyncio.run(profile(corpus, args.split_domains, args.interval, args.ablate))


if __name__ == "__main__":
    raise SystemExit(main())
