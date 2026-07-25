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


def _list_tables(db) -> list[str]:
    res = db.list_tables()
    if hasattr(res, "tables"):
        return res.tables  # type: ignore
    return list(res)


def _arrow_table_to_search_result(table: Any) -> dict[str, Any]:
    if table is None:
        return _empty_search_result()

    if isinstance(table, pa.Table):
        if table.num_rows == 0:
            return _empty_search_result()

        # H-02: Zero-copy ID and distance extraction from PyArrow table
        # Avoids loading entire result set into a Python list of dicts.
        ids_res = table.column("id").to_pylist() if "id" in table.column_names else []

        if "_distance" in table.column_names:
            dist_res = [_clean_value(d) for d in table.column("_distance").to_pylist()]
        else:
            dist_res = [0.0] * table.num_rows

        metadatas_res = []
        meta_cols = [c for c in table.column_names if c not in ("id", "vector", "_distance")]

        # Create metadata dicts column-by-column for efficiency
        for i in range(table.num_rows):
            meta = {}
            for col in meta_cols:
                # Retrieve scalar value efficiently
                meta[col] = table.column(col)[i].as_py()
            metadatas_res.append(meta)

    else:
        # Fallback for pandas (not normally used with LanceDB latest)
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
        from typing import Any

        self._table_cache: dict[str, Any] = {}
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
        if self.db is None:
            raise RuntimeError("LanceDB connection is not initialized")
        if name in self._table_cache:
            return self._table_cache[name]
        with self._write_lock:
            if name in self._table_cache:
                return self._table_cache[name]
            if name in _list_tables(self.db):
                tbl = self.db.open_table(name)
                self._table_cache[name] = tbl
                return tbl
        return None

    def _create_or_open_table(self, name: str, data: Any):
        self.connect()
        if self.db is None:
            raise RuntimeError("LanceDB connection is not initialized")
        with self._write_lock:
            if name in self._table_cache:
                tbl = self._table_cache[name]
                tbl.add(data)
                return tbl
            if name in _list_tables(self.db):
                tbl = self.db.open_table(name)
                self._table_cache[name] = tbl
                tbl.add(data)
                return tbl
            try:
                tbl = self.db.create_table(name, data=data)
                self._table_cache[name] = tbl
            except Exception as e:
                if "already exists" in str(e).lower():
                    logger.debug(
                        "Table %s already exists despite list_tables check. Opening and appending...",
                        name,
                    )
                    tbl = self.db.open_table(name)
                    self._table_cache[name] = tbl
                    tbl.add(data)
                else:
                    raise

            # NOTE: IVF_HNSW_SQ index creation is deferred to the end of ingestion
            return tbl

    def get_all_ids(self, table_name: str = "pma_chunks") -> set[str]:
        self.connect()
        if self.db is None:
            raise RuntimeError("LanceDB connection is not initialized")
        try:
            tbl = self._get_table(table_name)
            if tbl is not None:
                # We can grab just the id column
                arrow_col = tbl.search(None).select(["id"]).to_arrow().column("id")
                return set(str(x.as_py()) for x in arrow_col)
        except Exception as exc:
            logger.error("Failed to get all ids from %s: %s", table_name, exc)
        return set()

    def get_max_id(self, table_name: str = "pma_chunks") -> int:
        """Fetch the highest numeric chunk_id currently in LanceDB using PyArrow computation."""
        self.connect()
        if self.db is None:
            raise RuntimeError("LanceDB connection is not initialized")
        try:
            tbl = self._get_table(table_name)
            if tbl is not None:
                # Use PyArrow on just the 'id' column to avoid loading metadata and vectors, fixing O(1) violation.
                arrow_tbl = tbl.search(None).select(["id"]).to_arrow()
                col = arrow_tbl.column("id")
                if len(col) > 0:
                    import pyarrow as pa
                    import pyarrow.compute as pc  # type: ignore

                    val = pc.max(col.cast(pa.int64())).as_py()
                    return int(val) if val is not None else 0
        except Exception as exc:
            logger.error("Failed to get max id from %s: %s", table_name, exc)
        return 0

    def count_rows(self, table_name: str = "pma_chunks") -> int:
        """Efficiently count total rows in a LanceDB table."""
        self.connect()
        if self.db is None:
            raise RuntimeError("LanceDB connection is not initialized")
        try:
            tbl = self._get_table(table_name)
            if tbl is not None:
                # O(1) metadata read from underlying Lance fragment metadata
                return tbl.count_rows()
        except Exception as exc:
            logger.error("Failed to count rows in %s: %s", table_name, exc)
        return 0

    async def add_documents(
        self,
        ids: list[str],
        embeddings: list[np.ndarray] | np.ndarray,
        metadatas: list[dict[str, Any]],
    ) -> None:
        self.connect()
        if not ids:
            return

        # Ensure embeddings is a 2D numpy array (float32)
        if isinstance(embeddings, list):
            embeddings_np = np.vstack(embeddings).astype(np.float32)
        elif isinstance(embeddings, np.ndarray):
            embeddings_np = embeddings.astype(np.float32)
        else:
            raise TypeError("embeddings must be a list of numpy arrays or a numpy array")

        if embeddings_np.ndim == 1:
            embeddings_np = np.expand_dims(embeddings_np, axis=0)

        normalized_meta = _normalize_rows(metadatas)

        cols = {
            "id": pa.array(ids, type=pa.string()),
        }

        _num_rows, vector_dim = embeddings_np.shape
        flat_vectors = embeddings_np.flatten()
        vector_arr = pa.FixedSizeListArray.from_arrays(
            pa.array(flat_vectors, type=pa.float32()), list_size=vector_dim
        )
        cols["vector"] = vector_arr

        if normalized_meta:
            keys = normalized_meta[0].keys()
            for key in keys:
                col_values = [d[key] for d in normalized_meta]
                cols[key] = pa.array(col_values)

        table = pa.Table.from_pydict(cols)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(self._create_or_open_table, "pma_chunks", table))

    async def add_summaries_batch(self, summaries: list[dict[str, Any]]) -> None:
        """Add a batch of document summaries to the LanceDB summary table."""
        self.connect()
        if not summaries:
            return

        ids = [s["doc_id"] for s in summaries]
        embs = []
        for s in summaries:
            emb = s["embedding"]
            if isinstance(emb, list):
                embs.append(np.array(emb, dtype=np.float32))
            else:
                if emb.ndim == 1:
                    embs.append(np.expand_dims(emb.astype(np.float32), axis=0))
                else:
                    embs.append(emb.astype(np.float32))

        embeddings_np = np.vstack(embs).astype(np.float32)
        if embeddings_np.ndim == 1:
            embeddings_np = np.expand_dims(embeddings_np, axis=0)

        metadatas = [s["metadata"] for s in summaries]
        normalized_meta = _normalize_rows(metadatas)

        cols = {
            "id": pa.array(ids, type=pa.string()),
        }

        _num_rows, vector_dim = embeddings_np.shape
        flat_vectors = embeddings_np.flatten()
        vector_arr = pa.FixedSizeListArray.from_arrays(
            pa.array(flat_vectors, type=pa.float32()), list_size=vector_dim
        )
        cols["vector"] = vector_arr

        if normalized_meta:
            keys = normalized_meta[0].keys()
            for key in keys:
                col_values = [d[key] for d in normalized_meta]
                cols[key] = pa.array(col_values)

        table = pa.Table.from_pydict(cols)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, partial(self._create_or_open_table, "pma_summaries", table)
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
                    if key == "id" and isinstance(val, int):
                        val = str(val)
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
        if not ids:
            return
        tbl = self._get_table("pma_chunks")
        if tbl is None:
            return

        loop = asyncio.get_running_loop()

        def _delete():
            with self._write_lock:
                id_list = ", ".join(
                    f"'{doc_id.replace(chr(39), chr(39) + chr(39))}'" for doc_id in ids
                )
                tbl.delete(f"id IN ({id_list})")

        await loop.run_in_executor(None, _delete)

    async def delete_folder(self, folder_tag: str) -> None:
        """Delete all vectors matching a folder tag from both chunks and summaries."""
        self.connect()
        loop = asyncio.get_running_loop()

        def _delete_impl():
            with self._write_lock:
                # Escape single quotes to prevent query parse errors on paths with apostrophes
                safe_tag = folder_tag.replace("'", "''")
                # 1. Chunks
                tbl_chunks = self._get_table("pma_chunks")
                if tbl_chunks:
                    tbl_chunks.delete(f"folder_tag = '{safe_tag}'")
                # 2. Summaries
                tbl_sums = self._get_table("pma_summaries")
                if tbl_sums:
                    tbl_sums.delete(f"folder_tag = '{safe_tag}'")

        await loop.run_in_executor(None, _delete_impl)

    async def add_query_cache(
        self,
        query_emb: np.ndarray | list[float],
        query_text: str,
        response_text: str,
        timestamp: float,
    ) -> None:
        """Add a successful RAG response to the persistent semantic cache."""
        self.connect()

        if isinstance(query_emb, list):
            query_emb_np = np.array(query_emb, dtype=np.float32)
        else:
            query_emb_np = query_emb.astype(np.float32)

        embeddings_np = (
            np.expand_dims(query_emb_np, axis=0) if query_emb_np.ndim == 1 else query_emb_np
        )

        cols = {
            "query_text": pa.array([query_text], type=pa.string()),
            "response_text": pa.array([response_text], type=pa.string()),
            "timestamp": pa.array([timestamp], type=pa.float64()),
        }

        _num_rows, vector_dim = embeddings_np.shape
        flat_vectors = embeddings_np.flatten()
        vector_arr = pa.FixedSizeListArray.from_arrays(
            pa.array(flat_vectors, type=pa.float32()), list_size=vector_dim
        )
        cols["vector"] = vector_arr

        table = pa.Table.from_pydict(cols)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(self._create_or_open_table, "query_cache", table))

    async def search_cache(self, query_emb: list[float], threshold: float = 0.95) -> dict | None:
        """Search the persistent cache for similar past queries."""
        self.connect()
        tbl = self._get_table("query_cache")
        if tbl is None:
            return None

        loop = asyncio.get_running_loop()

        def _search():
            try:
                # H-01: LanceDB's cosine metric returns cosine distance (1 - cosine_similarity).
                max_distance = 1.0 - threshold
                res = (
                    tbl.search(query_emb, vector_column_name="vector")
                    .metric("cosine")
                    .limit(1)
                    .to_arrow()
                )

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

    async def create_hnsw_index(self, table_name: str = "pma_chunks") -> None:
        """Create HNSW index on the vector column of the specified table."""
        self.connect()
        tbl = self._get_table(table_name)
        if tbl is not None:
            loop = asyncio.get_running_loop()

            def _create():
                with self._write_lock:
                    try:
                        # Attempt to create index with replace=True to overwrite old index
                        tbl.create_index(metric="cosine", index_type="IVF_HNSW_SQ", replace=True)
                        logger.info(
                            "LanceDB HNSW index created/updated successfully on %s", table_name
                        )
                    except Exception as e:
                        try:
                            # Fallback if replace is not supported or fails
                            tbl.create_index(metric="cosine", index_type="IVF_HNSW_SQ")
                            logger.info("LanceDB HNSW index created successfully on %s", table_name)
                        except Exception as e2:
                            logger.error(
                                "LanceDB HNSW index creation failed: %s (fallback %s)", e, e2
                            )

            await loop.run_in_executor(None, _create)

    async def clear_all(self) -> None:
        self.connect()
        if self.db is None:
            raise RuntimeError("LanceDB connection is not initialized")
        loop = asyncio.get_running_loop()

        def _drop():
            with self._write_lock:
                tables = _list_tables(self.db)
                if "pma_chunks" in tables:
                    self.db.drop_table("pma_chunks")
                if "pma_summaries" in tables:
                    self.db.drop_table("pma_summaries")
                if "query_cache" in tables:
                    self.db.drop_table("query_cache")
                self._table_cache.clear()

        await loop.run_in_executor(None, _drop)
