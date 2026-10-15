"""In-process, interleaved benchmark of EmbeddingService session configurations.

    .venv/Scripts/python.exe scripts/bench_embed.py --intra 0 0      # A-vs-A self-check
    .venv/Scripts/python.exe scripts/bench_embed.py --intra 0 4 8 16 # thread-cap arms

Why in-process: back-to-back ingest runs on the dev laptop drift 26-36% from
throttling (MEASURE_M8.md), so separate runs cannot resolve tens-of-percent
effects. Here every arm's session is resident at once and the arms alternate
in ABBA blocks, so slow drift lands on every arm alike and cancels in the
paired per-block ratio against the first arm (the control).

The sample is real pipeline output: StreamChunker + chunk_embedding_text over
a seeded subset of text files, embedded through the real embed_texts path.
Session options are set by wrapping ort.InferenceSession while each arm loads;
`0` leaves intra_op_num_threads unset, exactly as shipped. No app/ change.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DEFAULT_CORPORA = ("tests/fixtures/perf_corpus", "tests/eval/corpus_squad")


def build_sample(corpora: list[str], n: int, seed: int) -> list[str]:
    from app.config import settings
    from app.indexing.service import StreamChunker
    from app.project_constants import TEXT_EXTENSIONS, build_context_prefix, chunk_embedding_text

    files = sorted(
        p
        for c in corpora
        for p in Path(c).rglob("*")
        if p.is_file() and p.suffix.lower() in TEXT_EXTENSIONS
    )
    random.Random(seed).shuffle(files)  # noqa: S311 - reproducible sampling, not crypto
    overlap = min(settings.chunk_overlap, settings.chunk_size // 2)
    texts: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        chunker = StreamChunker(settings.chunk_size, overlap, build_context_prefix(str(path)))
        for c in chunker.process(text) + chunker.finalize():
            texts.append(
                chunk_embedding_text(c["text_preview"], str(path), settings.embed_chunk_prefix)
            )
        if len(texts) >= n:
            break
    return texts[:n]


def load_arm(intra: int):
    """An EmbeddingService whose session gets intra_op_num_threads=intra (0 = shipped)."""
    import onnxruntime as ort

    from app.embeddings.service import EmbeddingService

    real = ort.InferenceSession

    def session(path, sess_options=None, *args, **kwargs):
        if intra and sess_options is not None:
            sess_options.intra_op_num_threads = intra
        return real(path, sess_options, *args, **kwargs)

    ort.InferenceSession = session  # type: ignore[misc]
    try:
        emb = EmbeddingService()
        emb.load_model()
    finally:
        ort.InferenceSession = real  # type: ignore[misc]
    return emb


def _copy_subset(corpus: str, n: int, seed: int, dest: Path) -> Path:
    """A fixed, seeded n-file copy of `corpus`, so every run ingests identical input."""
    files = sorted(p for p in Path(corpus).rglob("*") if p.is_file())
    random.Random(seed).shuffle(files)  # noqa: S311 - reproducible sampling, not crypto
    for p in files[:n]:
        target = dest / p.relative_to(corpus)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    return dest


async def _ingest_once(emb, subset: Path, workroot: Path) -> tuple[float, int]:
    """One real index_folders run (plus the API's fts_optimize) into a fresh index."""
    from app.config import settings
    from app.indexing.service import IndexingService
    from app.storage.db import DatabaseManager
    from app.vector_store.lancedb_client import LanceDBClient

    run_dir = Path(tempfile.mkdtemp(dir=workroot))
    settings.db_path = str(run_dir / "bench.db")
    settings.lancedb_persist_dir = str(run_dir / "lancedb")
    db = DatabaseManager(settings.db_path)
    await db.connect()
    await db.init_db(schema_path="app/storage/schema.sql")
    lancedb = LanceDBClient(persist_directory=settings.lancedb_persist_dir)
    lancedb.connect()
    try:
        t0 = time.perf_counter()
        await IndexingService(db, emb, lancedb).index_folders([str(subset)])
        await db.fts_optimize()
        seconds = time.perf_counter() - t0
        chunks = (await db.execute_query("SELECT COUNT(*) FROM chunks"))[0][0]
    finally:
        await db.close()
    return seconds, chunks


def _boot_ci(ratios: list[float], seed: int, reps: int = 4000) -> tuple[float, float]:
    rng = random.Random(seed)  # noqa: S311 - bootstrap resampling, not crypto
    meds = sorted(statistics.median(rng.choices(ratios, k=len(ratios))) for _ in range(reps))
    return meds[int(0.025 * reps)], meds[int(0.975 * reps) - 1]


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


async def run(args) -> int:
    sample = build_sample(args.corpus, args.sample, args.seed)
    chars = sum(len(t) for t in sample)
    print(
        f"sample      : {len(sample)} texts, mean {chars / len(sample):.0f} chars, seed {args.seed}"
    )

    labels = [f"{i}:intra{n}" for i, n in enumerate(args.intra)]
    arms = [load_arm(n) for n in args.intra]
    for label, emb in zip(labels, arms, strict=True):
        o = emb._session.get_session_options()
        print(f"arm {label:<12}: intra {o.intra_op_num_threads} / inter {o.inter_op_num_threads}")

    for emb in arms:  # warm-up, untimed
        await emb.embed_texts(sample[:200])

    workroot = Path(tempfile.mkdtemp(prefix="pma_bench_"))
    if args.ingest:
        subset = _copy_subset(args.ingest_corpus, args.ingest, args.seed, workroot / "subset")
        print(f"ingest      : {args.ingest} files from {args.ingest_corpus}, fresh index per run")
        units = "chunks"
    else:
        units = "texts"
    work_per_run: list[int] = []

    async def one_run(emb) -> float:
        if args.ingest:
            seconds, chunks = await _ingest_once(emb, subset, workroot)
            work_per_run.append(chunks)
            return seconds
        t0 = time.perf_counter()
        await emb.embed_texts(sample)
        work_per_run.append(len(sample))
        return time.perf_counter() - t0

    secs: list[list[float]] = [[] for _ in arms]
    for block in range(args.blocks):
        order = list(range(len(arms)))
        if block % 2:
            order.reverse()
        for a in order:
            # ORT's pool threads spin briefly after a run; let the previous
            # arm's go idle so they do not compete with this one.
            await asyncio.sleep(0.5)
            secs[a].append(await one_run(arms[a]))
        print(f"block {block + 1}/{args.blocks}: " + "  ".join(f"{s[-1]:.1f}s" for s in secs))

    # Every run must do identical work, or the ratio compares different jobs.
    units_done = set(work_per_run)
    mismatch = "" if len(units_done) == 1 else "  <- MISMATCH"
    print(f"{units} per run: {sorted(units_done)}{mismatch}")
    per_run = work_per_run[0]

    print()
    print(f"{'arm':<14}{'median ' + units + '/s':>16}{'ratio vs arm 0':>16}{'95% CI':>20}")
    print("-" * 66)
    for a, label in enumerate(labels):
        tput = statistics.median(per_run / s for s in secs[a])
        ratios = [secs[0][b] / secs[a][b] for b in range(args.blocks)]  # >1 = faster
        lo, hi = _boot_ci(ratios, args.seed)
        ci = f"[{lo:.3f}, {hi:.3f}]"
        print(f"{label:<14}{tput:>16.1f}{statistics.median(ratios):>16.3f}{ci:>20}")

    queries = [t[:80] for t in sample[: args.queries]]
    lat: list[list[float]] = [[] for _ in arms]
    for q in queries:
        for a, emb in enumerate(arms):
            t0 = time.perf_counter()
            await emb.embed_texts([q])
            lat[a].append((time.perf_counter() - t0) * 1000)
    print()
    print(f"{'arm':<14}{'query p50 ms':>14}{'query p95 ms':>14}")
    for a, label in enumerate(labels):
        print(f"{label:<14}{statistics.median(lat[a]):>14.1f}{_pct(lat[a], 0.95):>14.1f}")

    _time_split(arms[0], sample)

    if args.ingest:
        from app.api.deps import close_all
        from app.indexing.service import shutdown_executors

        shutdown_executors()
        await close_all()
    shutil.rmtree(workroot, ignore_errors=True)  # scratch only; LanceDB may hold handles
    return 0


def _time_split(emb, sample: list[str]) -> None:
    """Where one control pass goes: tokenize vs session.run vs pool+normalize."""
    import numpy as np

    from app.config import settings
    from app.embeddings.service import _length_sorted_batches

    tok = run_s = pool = 0.0
    for positions in _length_sorted_batches(
        sample, emb.optimal_batch_size, settings.embedding_batch_char_budget
    ):
        t0 = time.perf_counter()
        enc = emb._tokenizer.encode_batch([sample[j] for j in positions])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        types = np.array([e.type_ids for e in enc], dtype=np.int64)
        t1 = time.perf_counter()
        out = emb._session.run(
            None, {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
        )
        t2 = time.perf_counter()
        emb._normalize(emb._mean_pooling(out, mask))
        t3 = time.perf_counter()
        tok += t1 - t0
        run_s += t2 - t1
        pool += t3 - t2
    total = tok + run_s + pool
    parts = (("tokenize", tok), ("session.run", run_s), ("pool+norm", pool))
    print()
    print(
        "control time split: "
        + ", ".join(f"{name} {v:.1f}s ({100 * v / total:.0f}%)" for name, v in parts)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--intra",
        type=int,
        nargs="+",
        required=True,
        help="One arm per value; the first is the control. 0 = shipped (unset).",
    )
    parser.add_argument("--corpus", nargs="+", default=list(_DEFAULT_CORPORA))
    parser.add_argument("--sample", type=int, default=2000)
    parser.add_argument("--blocks", type=int, default=6)
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument(
        "--ingest",
        type=int,
        default=0,
        help="In-pipeline mode: each run is a real ingest of this many seeded files "
        "into a fresh index, so the embedder competes with the pipeline's own threads.",
    )
    parser.add_argument("--ingest-corpus", default="tests/fixtures/perf_corpus")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
