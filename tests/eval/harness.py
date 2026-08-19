"""Index the evaluation corpus and run queries against it.

Uses the **real** `IndexingService`, `EmbeddingService` and `hybrid_retrieve`.
Mocked embeddings would make semantic search meaningless and every number this
harness produces worthless - the whole point is measuring the shipped pipeline.

Cost of that choice: it needs `BAAI/bge-small-en-v1.5` and
`cross-encoder/ms-marco-MiniLM-L-6-v2` on disk (or a network fetch), and it
takes tens of seconds. Callers gate it behind the `eval` pytest marker.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.eval import metrics

if TYPE_CHECKING:
    # Type-only. The runtime imports stay inside build() so that importing this
    # module does not drag in ONNX and LanceDB at collection time.
    from app.embeddings.service import EmbeddingService
    from app.storage.db import DatabaseManager
    from app.vector_store.lancedb_client import LanceDBClient

logger = logging.getLogger(__name__)

CORPUS_DIR = Path(__file__).parent / "corpus"
QUERIES_FILE = Path(__file__).parent / "queries.json"


@dataclass
class EvalQuery:
    id: str
    query: str
    type: str
    relevant_files: list[str]
    expected_domains: list[str]
    note: str = ""


@dataclass
class QueryResult:
    query: EvalQuery
    results: list[dict[str, Any]]
    ranked: list[str] = field(default_factory=list)

    def scores(self, k: int) -> dict[str, float]:
        return {
            "recall": metrics.recall_at_k(self.ranked, self.query.relevant_files, k),
            "precision": metrics.precision_at_k(self.ranked, self.query.relevant_files, k),
            "ndcg": metrics.ndcg_at_k(self.ranked, self.query.relevant_files, k),
            "mrr": metrics.mrr(self.ranked, self.query.relevant_files),
            "domain_coverage": metrics.domain_coverage(
                self.results, self.query.expected_domains, k
            ),
        }


def load_queries(path: Path = QUERIES_FILE) -> list[EvalQuery]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        EvalQuery(
            id=q["id"],
            query=q["query"],
            type=q["type"],
            relevant_files=q["relevant_files"],
            expected_domains=q["expected_domains"],
            note=q.get("note", ""),
        )
        for q in data["queries"]
    ]


class EvalIndex:
    """A throwaway index over the evaluation corpus.

    Built once per session and reused across ablations: indexing dominates the
    runtime, and the ablations only change *retrieval* settings, so rebuilding
    per configuration would multiply the cost for no added signal.
    """

    def __init__(self, workdir: Path | None = None, corpus_dir: Path = CORPUS_DIR):
        # Deliberately NOT pytest's tmp_path: `--basetemp=.pytest_temp` puts
        # that inside the repo, and LanceDB cannot create a table there - its
        # commit path does a copy the volume rejects with "Incorrect function
        # (os error 1)". tests/test_lancedb_client_real.py uses mkdtemp for the
        # same reason. Do not "simplify" this back to a tmp_path fixture.
        self.workdir = workdir or Path(tempfile.mkdtemp(prefix="pma_eval_"))
        self.corpus_dir = corpus_dir
        # Annotated because scripts/survey_corpus.py imports this module, and an
        # import from outside tests/ drags it past mypy's `^tests/` exclude.
        # Without these, mypy infers the attributes as literally None.
        self.db: DatabaseManager | None = None
        self.lancedb: LanceDBClient | None = None
        self.embeddings: EmbeddingService | None = None
        self._path_prefix = ""

    async def build(self) -> EvalIndex:
        from app.config import settings
        from app.embeddings.service import EmbeddingService
        from app.indexing.service import IndexingService
        from app.storage.db import DatabaseManager
        from app.vector_store.lancedb_client import LanceDBClient

        db_path = str(self.workdir / "eval.db")
        lance_dir = str(self.workdir / "lancedb")

        # The retrieval path reads these off the global settings object.
        settings.db_path = db_path
        settings.lancedb_persist_dir = lance_dir

        self.db = DatabaseManager(db_path)
        await self.db.connect()
        await self.db.init_db(schema_path="app/storage/schema.sql")

        self.lancedb = LanceDBClient(persist_directory=lance_dir)
        self.lancedb.connect()

        self.embeddings = EmbeddingService()
        self.embeddings.load_model()

        service = IndexingService(
            db=self.db, embedding_service=self.embeddings, lancedb_client=self.lancedb
        )
        # Each domain directory becomes its own folder_tag, which is what the
        # source-balanced fusion is allocating across.
        domains = sorted(p for p in self.corpus_dir.iterdir() if p.is_dir())
        await service.index_folders([str(p) for p in domains])

        self._path_prefix = self.corpus_dir.resolve().as_posix()
        return self

    async def close(self) -> None:
        from app.api.deps import close_all
        from app.indexing.service import shutdown_executors

        if self.db is not None:
            await self.db.close()

        # This harness builds its own DatabaseManager, but any code path that
        # touches app.api.deps builds the module-global one too. Leaving it
        # open leaves a non-daemon aiosqlite thread behind, which blocks
        # interpreter exit forever.
        await close_all()

        # The pipeline's thread pools are module-level and outlive this object.
        # concurrent.futures joins their workers at interpreter exit with no
        # timeout, so a caller that only closed the database would still hang.
        shutdown_executors()

        # Deliberately NOT ignore_errors=True. That swallowed precisely the
        # evidence that mattered: a hung survey run left behind an eval.db
        # Windows refused to delete - a read connection was still open - while
        # rmtree removed the lancedb/ directory next to it and reported nothing.
        # The result looked like a clean teardown for an entire investigation.
        leftovers: list[str] = []
        shutil.rmtree(
            self.workdir, onexc=lambda _fn, path, exc: leftovers.append(f"{path}: {exc!r}")
        )
        if leftovers:
            logger.warning(
                "EvalIndex.close() could not remove %d path(s) under %s - something still holds a handle: %s",
                len(leftovers),
                self.workdir,
                "; ".join(leftovers),
            )

    def relativize(self, absolute_path: str) -> str:
        """Corpus-relative path, so ground truth stays machine-independent."""
        normalized = absolute_path.replace("\\", "/")
        prefix = self._path_prefix
        if prefix and normalized.startswith(prefix):
            return normalized[len(prefix) :].lstrip("/")
        return normalized

    async def retrieve(self, query: str, k: int) -> list[dict[str, Any]]:
        from app.search import retrieval

        if self.db is None or self.embeddings is None or self.lancedb is None:
            raise RuntimeError("EvalIndex.build() must run before retrieve()")

        # Ablations change fusion behaviour without changing the index, so the
        # retrieval cache would serve results from the previous configuration.
        retrieval.clear_retrieval_cache()

        # use_reranker=False deliberately. Two reasons, and the first alone
        # would be enough:
        #   1. Every toggle under ablation affects *candidate selection*. The
        #      cross-encoder re-sorts whatever pool it is handed, so leaving it
        #      on adds a downstream confound to an experiment about recall.
        #   2. The ONNX reranker lives under models/ (materialized by a
        #      deployment step), not in the HF cache, so a dev checkout does not
        #      have it. Leaving it enabled meant swallowing a stack of
        #      "ONNX Reranking failed" errors and calling the result a
        #      measurement.
        # The post-rerank rebalance is covered by unit tests instead - see
        # test_rebalance_after_rerank_restores_domain_spread.
        results = await retrieval.hybrid_retrieve(
            query=query,
            db=self.db,
            embedding_service=self.embeddings,
            lancedb_client=self.lancedb,
            k=k,
            use_reranker=False,
        )
        for r in results:
            r["file_path"] = self.relativize(r.get("file_path", ""))
        return results

    async def run(self, queries: list[EvalQuery], k: int) -> list[QueryResult]:
        out: list[QueryResult] = []
        for q in queries:
            results = await self.retrieve(q.query, k)
            out.append(QueryResult(query=q, results=results, ranked=metrics.ranked_files(results)))
        return out


_METRIC_KEYS = ("recall", "precision", "ndcg", "mrr", "domain_coverage")


def aggregate(run: list[QueryResult], k: int, query_type: str | None = None) -> dict[str, float]:
    """Mean metrics over a run, optionally restricted to one query type."""
    rows = [r.scores(k) for r in run if query_type is None or r.query.type == query_type]
    return metrics.summarize(rows, _METRIC_KEYS)


def format_report(run: list[QueryResult], k: int) -> str:
    """Per-query table plus aggregates. Printed on failure so a regression is
    diagnosable from CI output without re-running locally."""
    lines = [
        f"{'query':<28} {'type':<15} {'recall':>7} {'ndcg':>7} {'mrr':>7} {'domains':>8}",
        "-" * 78,
    ]
    for r in run:
        s = r.scores(k)
        lines.append(
            f"{r.query.id:<28} {r.query.type:<15} "
            f"{s['recall']:>7.2f} {s['ndcg']:>7.2f} {s['mrr']:>7.2f} "
            f"{s['domain_coverage']:>8.2f}"
        )
    lines.append("-" * 78)
    overall = aggregate(run, k)
    lines.append(
        f"{'ALL':<28} {'':<15} {overall['recall']:>7.2f} {overall['ndcg']:>7.2f} "
        f"{overall['mrr']:>7.2f} {overall['domain_coverage']:>8.2f}"
    )
    return "\n".join(lines)
