"""Stage-resolved wall-clock profile of a full ingestion run.

    set PMA_LANCEDB_MODE=portable
    .venv/Scripts/python.exe scripts/profile_ingest_throughput.py --corpus tests/fixtures/perf_corpus

Answers one question: which stage does an index run spend its time in. The
pipeline is concurrent (extractor, embedder, storer run side by side), so a
stage's cost here is the *union* of the intervals during which at least one call
of it was running - the time it was busy - not a sum of call durations, which
would count 16 overlapping extracts sixteen times.

Wraps existing methods on the instances; nothing in app/ is edited. Runs the
index twice on one throwaway index: cold, then again with nothing changed (the
mtime early-out path). Reads the corpus, writes nothing to it.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

Interval = tuple[float, float]


def _union(intervals: list[Interval], lo: float, hi: float) -> float:
    """Seconds covered by at least one interval, clipped to [lo, hi]."""
    total, end = 0.0, lo
    for a, b in sorted((max(a, lo), min(b, hi)) for a, b in intervals):
        if b <= end or b <= a:
            continue
        total += b - max(a, end)
        end = b
    return total


def _wrap(obj, name: str, label: str, log: dict[str, list[Interval]]) -> None:
    fn = getattr(obj, name)

    @functools.wraps(fn)
    async def timed(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return await fn(*args, **kwargs)
        finally:
            log[label].append((t0, time.perf_counter()))

    setattr(obj, name, timed)


# (attribute owner key, method, label). Order is report order.
_STAGES = (
    ("service", "_detect_changes", "change detection"),
    ("service", "_rust_pre_extract", "rust pre-extract"),
    ("service", "_stream_extract_and_prepare", "extract+chunk (any file)"),
    ("emb", "embed_texts", "embed_texts"),
    ("service", "_delete_existing_chunks", "delete old chunks"),
    ("service", "_flush_pending_chunks_sqlite", "sqlite chunk flush"),
    ("lancedb", "add_documents", "lancedb add"),
    ("service", "_flush_file_summaries", "tail: file summaries"),
    ("service", "_generate_folder_profiles", "tail: folder profiles"),
    ("lancedb", "create_hnsw_index", "tail: hnsw index"),
    ("db", "fts_optimize", "tail: fts optimize"),
)


def _emulate_4core() -> None:
    """M4c's emulation, exactly: affinity 0xFF (4 P-cores + HT), embedder intra 4.

    ORT ignores the affinity mask when sizing its pool (MEASURE_A9-08_4core.md),
    so the 4-core default is modelled explicitly as intra=4, by wrapping the
    session constructor - no app/ change. Call before app is imported.
    """
    # A literal sys.platform check, so mypy on Linux (CI) treats the WinDLL
    # code below as unreachable rather than an attribute error.
    if sys.platform != "win32":
        raise SystemExit("--emulate-4core needs Windows (SetProcessAffinityMask)")

    import ctypes
    from ctypes import wintypes

    import onnxruntime as ort

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
    kernel32.SetProcessAffinityMask.restype = wintypes.BOOL
    if not kernel32.SetProcessAffinityMask(kernel32.GetCurrentProcess(), 0xFF):
        raise OSError(ctypes.get_last_error(), "SetProcessAffinityMask failed")

    real = ort.InferenceSession

    def session(path, sess_options=None, *args, **kwargs):
        opts = sess_options or ort.SessionOptions()
        opts.intra_op_num_threads = 4
        opts.inter_op_num_threads = 2
        return real(path, opts, *args, **kwargs)

    ort.InferenceSession = session  # type: ignore[misc]


def _count_batches(stats: dict[str, int]) -> None:
    """Count the ONNX batches embed_texts actually runs (rows, padded width)."""
    import app.embeddings.service as es

    real = es._length_sorted_batches

    def counted(texts, batch_size, char_budget=0):
        batches = real(texts, batch_size, char_budget)
        for b in batches:
            stats["batches"] += 1
            stats["rows"] += len(b)
            stats["width_chars"] += max(len(texts[i]) for i in b)
        return batches

    es._length_sorted_batches = counted


async def _one_run(service, db, folders, log) -> tuple[float, float, float, float]:
    log.clear()
    t0 = time.perf_counter()
    await service.index_folders(folders)
    # The API route runs this after index_folders (app/api/indexing.py), so it
    # is part of what a user waits for.
    await db.fts_optimize()
    t1 = time.perf_counter()
    pipe = log.get("pipeline", [])
    p0, p1 = pipe[0] if pipe else (t0, t0)
    return t0, t1, p0, p1


def _report(title, t0, t1, p0, p1, log, chunks_added, batch_stats) -> None:
    wall = t1 - t0
    pipe = p1 - p0
    print(f"--- {title}")
    print(f"wall clock               : {wall:8.1f} s")
    print(f"pipeline (extract..store): {pipe:8.1f} s")
    if pipe > 0:
        print(f"chunks added             : {chunks_added} ({chunks_added / pipe:.1f} chunks/s)")
    n = batch_stats["batches"]
    if n:
        print(
            f"onnx batches             : {n}, mean rows {batch_stats['rows'] / n:.1f}, "
            f"mean padded width {batch_stats['width_chars'] / n:.0f} chars"
        )
    print(f"{'stage':<28}{'calls':>7}{'busy s':>9}{'% pipeline':>12}{'% wall':>9}")
    print("-" * 65)
    for _owner, _name, label in _STAGES:
        iv = log.get(label, [])
        busy_all = _union(iv, t0, t1)
        busy_pipe = _union(iv, p0, p1)
        pct_pipe = 100 * busy_pipe / pipe if pipe > 0 else 0.0
        pct_wall = 100 * busy_all / wall if wall > 0 else 0.0
        print(f"{label:<28}{len(iv):>7}{busy_all:>9.1f}{pct_pipe:>11.1f}%{pct_wall:>8.1f}%")
    print()


async def profile(corpus: Path, split_domains: bool) -> int:
    from app.config import settings
    from app.embeddings.service import EmbeddingService
    from app.indexing.service import IndexingService, shutdown_executors
    from app.storage.db import DatabaseManager
    from app.vector_store.lancedb_client import LanceDBClient

    batch_stats: dict[str, int] = defaultdict(int)
    _count_batches(batch_stats)

    print(f"corpus      : {corpus}")
    print(f"lancedb_mode: {settings.lancedb_mode}")
    print(f"char budget : {settings.embedding_batch_char_budget}")

    workdir = Path(tempfile.mkdtemp(prefix="pma_tputprof_"))
    settings.db_path = str(workdir / "prof.db")
    settings.lancedb_persist_dir = str(workdir / "lancedb")

    db = DatabaseManager(settings.db_path)
    await db.connect()
    await db.init_db(schema_path="app/storage/schema.sql")
    lancedb = LanceDBClient(persist_directory=settings.lancedb_persist_dir)
    lancedb.connect()
    emb = EmbeddingService()
    emb.load_model()
    opts = emb._session.get_session_options()
    print(f"embedder    : intra {opts.intra_op_num_threads} / inter {opts.inter_op_num_threads}")

    folders = (
        sorted(str(p) for p in corpus.iterdir() if p.is_dir()) if split_domains else [str(corpus)]
    )
    service = IndexingService(db, emb, lancedb)

    log: dict[str, list[Interval]] = defaultdict(list)
    owners = {"service": service, "emb": emb, "lancedb": lancedb, "db": db}
    for owner, name, label in _STAGES:
        _wrap(owners[owner], name, label, log)
    _wrap(service, "_batch_index_pipeline", "pipeline", log)

    async def chunk_count() -> int:
        return int((await db.execute_query("SELECT COUNT(*) FROM chunks"))[0][0])

    for title in ("cold index", "re-index, nothing changed"):
        before = await chunk_count()
        batch_stats.clear()
        t0, t1, p0, p1 = await _one_run(service, db, folders, log)
        _report(title, t0, t1, p0, p1, log, await chunk_count() - before, batch_stats)

    files = (await db.execute_query("SELECT COUNT(*) FROM files"))[0][0]
    print(f"files / chunks in index: {files} / {await chunk_count()}")

    await db.close()
    shutdown_executors()
    from app.api.deps import close_all

    await close_all()
    leftovers: list[str] = []
    shutil.rmtree(workdir, onexc=lambda _fn, path, exc: leftovers.append(f"{path}: {exc!r}"))
    if leftovers:
        print(f"workdir not fully removed ({len(leftovers)})", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--corpus", required=True, help="Folder to index. Read only.")
    parser.add_argument(
        "--split-domains",
        action="store_true",
        help="Index each immediate subdirectory as its own folder (as profile_ingest_memory.py).",
    )
    parser.add_argument(
        "--emulate-4core",
        action="store_true",
        help="M4c emulation: process affinity 0xFF and embedder intra_op 4 / inter_op 2.",
    )
    args = parser.parse_args()
    corpus = Path(args.corpus).resolve()
    if not corpus.is_dir():
        print(f"not a directory: {corpus}", file=sys.stderr)
        return 2
    if args.emulate_4core:
        _emulate_4core()
        print("emulation   : affinity 0xFF, embedder intra 4 / inter 2")
    return asyncio.run(profile(corpus, args.split_domains))


if __name__ == "__main__":
    raise SystemExit(main())
