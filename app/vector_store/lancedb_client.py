import asyncio
import logging
import math
import threading
from collections.abc import Sequence
from functools import partial
from typing import Any

import lancedb  # type: ignore
import numpy as np
import pyarrow as pa  # type: ignore

logger = logging.getLogger(__name__)


def _normalize_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []

    all_keys = sorted({key for row in rows for key in row})

    normalized = []
    for row in rows:
        new_row = {key: row.get(key) for key in all_keys}
        normalized.append(new_row)
    return normalized


def _clean_value(val: Any) -> Any:
    if isinstance(val, (float, np.floating)):
        if math.isnan(val) or math.isinf(val):
            return 0.0
        return float(val)
    return val


def _empty_search_result() -> dict[str, Any]:
    return {"ids": [[]], "distances": [[]], "metadatas": [[]]}


def _arrow_table_to_search_result(table: Any) -> dict[str, Any]:
    if table is None:
        return _empty_search_result()

    if isinstance(table, pa.Table):
        if table.num_rows == 0:
            return _empty_search_result()
        rows = table.to_pylist()
    else:
        rows = table.to_pandas().to_dict("records")
        if not rows:
            return _empty_search_result()

    ids_res = [row.get("id") for row in rows]
    dist_res = [_clean_value(row.get("_distance", 0.0)) for row in rows]

    metadatas_res = []
    for row in rows:
        meta = {k: v for k, v in row.items() if k not in ("id", "vector", "_distance")}
        metadatas_res.append(meta)

    return {
        "ids": [ids_res],
        "distances": [dist_res],
        "metadatas": [metadatas_res],
    }


class LanceDBClient:
    def __init__(self, persist_directory: str = "lancedb_data"):
        self.persist_directory = persist_directory
        self.db = None
        self._connect_lock = threading.Lock()
        self._write_lock = threading.Lock()

    def connect(self) -> None:
        if self.db is not None:
            return
        with self._connect_lock:
            if self.db is None:
                logger.info("Connecting to LanceDB at: %s", self.persist_directory)
                self.db = lancedb.connect(self.persist_directory)
                logger.info("LanceDB connection established.")

    def _get_table(self, name: str):
        self.connect()
        assert self.db is not None
        if name in self.db.list_tables():
            return self.db.open_table(name)
        return None

    def _create_or_open_table(self, name: str, data: Any):
        self.connect()
        assert self.db is not None
        with self._write_lock:
            if name in self.db.list_tables():
                tbl = self.db.open_table(name)
                tbl.add(data)
                return tbl
            tbl = self.db.create_table(name, data=data)
            # P-06: Explicitly set IVF_HNSW_SQ for better recall/latency balance on local data.
            if name == "pma_chunks":
                try:
                    tbl.create_index(metric="cosine", index_type="IVF_HNSW_SQ")
                except Exception as e:
                    logger.debug("LanceDB index creation skipped or failed: %s", e)
            return tbl

    def get_all_ids(self, table_name: str = "pma_chunks") -> set[str]:
        self.connect()
        assert self.db is not None
        try:
            if table_name in self.db.list_tables():
                tbl = self.db.open_table(table_name)
                # We can grab just the id column
                arrow_col = tbl.to_arrow(columns=["id"]).column("id")
                return set(str(x.as_py()) for x in arrow_col)
        except Exception as exc:
            logger.error("Failed to get all ids from %s: %s", table_name, exc)
        return set()

    def get_max_id(self, table_name: str = "pma_chunks") -> int:
        """Fetch the highest numeric chunk_id currently in LanceDB using Arrow aggregation."""
        self.connect()
        assert self.db is not None
        try:
            if table_name in self.db.list_tables():
                tbl = self.db.open_table(table_name)
                # Efficient aggregation via Arrow
                import pyarrow as pa
                import pyarrow.compute as pc

                arrow_table = tbl.to_arrow(columns=["id"])
                if arrow_table.num_rows == 0:
                    return 0

                # Cast string IDs to int64 and find max
                ids_col = arrow_table.column("id")
                # Handle potential non-numeric stubs gracefully by filtering if needed,
                # but assume production IDs are numeric as per get_max_id contract.
                numeric_ids = pc.cast(ids_col, pa.int64())
                max_val = pc.max(numeric_ids).as_py()
                return int(max_val) if max_val is not None else 0
        except Exception as exc:
            logger.error("Failed to get max id from %s: %s", table_name, exc)
        return 0

    async def add_documents(
        self, ids: list[str], embeddings: list[np.ndarray], metadatas: list[dict[str, Any]]
    ) -> None:
        self.connect()
        data = []
        for i, doc_id in enumerate(ids):
            row = {"id": doc_id, "vector": embeddings[i], **metadatas[i]}
            data.append(row)

        if not data:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, partial(self._create_or_open_table, "pma_chunks", _normalize_rows(data))
        )

    async def add_summaries_batch(self, summaries: list[dict[str, Any]]) -> None:
        """Add a batch of document summaries to the LanceDB summary table."""
        self.connect()
        if not summaries:
            return

        data = []
        for s in summaries:
            row = {"id": s["doc_id"], "vector": s["embedding"], **s["metadata"]}
            data.append(row)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, partial(self._create_or_open_table, "pma_summaries", _normalize_rows(data))
        )

    async def semantic_search(
        self, query_emb: list[float], k: int = 10, where_filter: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.connect()
        tbl = self._get_table("pma_chunks")
        if tbl is None:
            return _empty_search_result()

        loop = asyncio.get_running_loop()

        def _search():
            query = tbl.search(query_emb, vector_column_name="vector").metric("cosine").limit(k)
            if where_filter:
                clauses = []
                for key, val in where_filter.items():
                    if isinstance(val, str):
                        # Use double-quoted identifiers and single-quoted literals.
                        # Sanitize val to prevent injection.
                        safe_val = val.replace("'", "''")
                        clauses.append(f"{key} = '{safe_val}'")
                    elif isinstance(val, (int, float, bool)):
                        clauses.append(f"{key} = {val}")
                query = query.where(" AND ".join(clauses), prefilter=True)
            return query.to_arrow()

        table = await loop.run_in_executor(None, _search)
        return _arrow_table_to_search_result(table)

    async def search_summaries(
        self, query_emb: list[float], k: int = 5, where_filter: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.connect()
        tbl = self._get_table("pma_summaries")
        if tbl is None:
            return _empty_search_result()

        loop = asyncio.get_running_loop()

        def _search():
            query = tbl.search(query_emb, vector_column_name="vector").metric("cosine").limit(k)
            if where_filter:
                clauses = []
                for key, val in where_filter.items():
                    if isinstance(val, str):
                        safe_val = val.replace("'", "''")
                        clauses.append(f"{key} = '{safe_val}'")
                    elif isinstance(val, (int, float, bool)):
                        clauses.append(f"{key} = {val}")
                query = query.where(" AND ".join(clauses), prefilter=True)
            return query.to_arrow()

        table = await loop.run_in_executor(None, _search)
        return _arrow_table_to_search_result(table)

    async def delete_documents(self, ids: list[str]) -> None:
        self.connect()
        tbl = self._get_table("pma_chunks")
        if tbl is None:
            return

        loop = asyncio.get_running_loop()

        def _delete():
            with self._write_lock:
                id_list = ", ".join(f"'{doc_id}'" for doc_id in ids)
                tbl.delete(f"id IN ({id_list})")

        await loop.run_in_executor(None, _delete)

    async def delete_folder(self, folder_tag: str) -> None:
        """Delete all vectors matching a folder tag from both chunks and summaries."""
        self.connect()
        loop = asyncio.get_running_loop()

        def _delete_impl():
            with self._write_lock:
                # 1. Chunks
                tbl_chunks = self._get_table("pma_chunks")
                if tbl_chunks:
                    tbl_chunks.delete(f"folder_tag = '{folder_tag}'")
                # 2. Summaries
                tbl_sums = self._get_table("pma_summaries")
                if tbl_sums:
                    tbl_sums.delete(f"folder_tag = '{folder_tag}'")

        await loop.run_in_executor(None, _delete_impl)

    async def add_query_cache(
        self, query_emb: np.ndarray, query_text: str, response_text: str, timestamp: float
    ) -> None:
        """Add a successful RAG response to the persistent semantic cache."""
        self.connect()
        data = [
            {
                "vector": query_emb,
                "query_text": query_text,
                "response_text": response_text,
                "timestamp": timestamp,
            }
        ]
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, partial(self._create_or_open_table, "query_cache", _normalize_rows(data))
        )

    async def search_cache(self, query_emb: list[float], threshold: float = 0.95) -> dict | None:
        """Search the persistent cache for similar past queries."""
        self.connect()
        tbl = self._get_table("query_cache")
        if tbl is None:
            return None

        loop = asyncio.get_running_loop()

        def _search():
            try:
                # With cosine metric, LanceDB returns cosine_distance in _distance column.
                # cosine_distance = 1 - cosine_similarity, so lower is better.
                # threshold is a similarity floor (e.g. 0.95 -> max distance 0.05).
                max_distance = 1.0 - threshold
                res = tbl.search(query_emb, vector_column_name="vector").metric("cosine").limit(1).to_arrow()

                if isinstance(res, pa.Table) and res.num_rows > 0:
                    rows = res.to_pylist()
                    best = rows[0]
                    if best.get("_distance", 1.0) < max_distance:
                        return best
                return None
            except Exception as e:
                logger.debug("Cache search failed: %s", e)
                return None

        return await loop.run_in_executor(None, _search)

    async def clear_all(self) -> None:
        self.connect()
        assert self.db is not None
        loop = asyncio.get_running_loop()

        def _drop():
            with self._write_lock:
                if "pma_chunks" in self.db.list_tables():
                    self.db.drop_table("pma_chunks")
                if "pma_summaries" in self.db.list_tables():
                    self.db.drop_table("pma_summaries")
                if "query_cache" in self.db.list_tables():
                    self.db.drop_table("query_cache")

        await loop.run_in_executor(None, _drop)
