"""
Database manager module for Personal Memory Assistant.
Handles interactions with SQLite using aiosqlite for metadata storage.
"""

import logging
import os
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

logger = logging.getLogger(__name__)

def _zlib_decompress_fn(blob: Any) -> str:
    """Safe SQLite function to decompress zlib blobs, falling back to string if uncompressed."""
    if not blob:
        return ""
    if isinstance(blob, str):
        return blob
    try:
        return zlib.decompress(blob).decode("utf-8")
    except Exception:
        return str(blob)

class DatabaseManager:
    """Manages the SQLite database connection and operations."""

    def __init__(self, db_path: str = "pma_metadata.db"):
        """Initializes the DatabaseManager."""
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Establish connection to the SQLite database."""
        if not self.conn:
            self.conn = await aiosqlite.connect(self.db_path)
            self.conn.row_factory = aiosqlite.Row
            
            # Register Zlib Decompression for FTS5 queries and triggers
            await self.conn.create_function("zlib_decompress", 1, _zlib_decompress_fn)
            
            await self.conn.execute("PRAGMA journal_mode = WAL;")
            await self.conn.execute("PRAGMA foreign_keys = ON;")
            await self.conn.execute("PRAGMA synchronous = NORMAL;")
            await self.conn.execute("PRAGMA busy_timeout = 5000;")
            # ── Performance PRAGMAs ──────────────────────────────────
            await self.conn.execute("PRAGMA cache_size = -2000000;")   # 2 GB page cache
            await self.conn.execute("PRAGMA mmap_size = 30000000000;") # 30 GB memory-mapped I/O
            await self.conn.execute("PRAGMA temp_store = MEMORY;")   # temp tables in RAM
            await self.conn.execute("PRAGMA page_size = 32768;")     # maximum page size for deep trees
            await self.conn.execute("PRAGMA threads = 4;")           # allow background sorting threads
            await self.conn.execute("PRAGMA read_uncommitted = ON;") # readers skip WAL frames
            await self.conn.execute("PRAGMA wal_autocheckpoint = 1000;") # explicit WAL checkpoint control

    def _get_conn(self) -> aiosqlite.Connection:
        """Return the active connection, raising if not connected."""
        if self.conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self.conn

    async def close(self):
        """Close the active database connection if open."""
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def init_db(self, schema_path: str = "app/storage/schema.sql") -> None:
        """Initialize the database with the schema."""
        await self.connect()
        conn = self._get_conn()
        try:
            schema = Path(schema_path).read_text(encoding="utf-8")
            await conn.executescript(schema)
            await conn.commit()
            logger.info("Database initialized successfully.")
        except Exception as e:
            logger.error("Error initializing database: %s", e)
            raise
        await self._migrate(conn)

    async def _migrate(self, conn: "aiosqlite.Connection") -> None:
        """Apply safe, idempotent schema migrations."""
        migrations = [
            ("summary", "ALTER TABLE files ADD COLUMN summary TEXT DEFAULT ''"),
            ("sha256", "ALTER TABLE files ADD COLUMN sha256 TEXT DEFAULT ''"),
            ("files_created_at",
             "ALTER TABLE files ADD COLUMN created_at TEXT NOT NULL DEFAULT ''"),
            ("chunks_created_at",
             "ALTER TABLE chunks ADD COLUMN created_at TEXT NOT NULL DEFAULT ''"),
        ]
        for col_name, ddl in migrations:
            try:
                await conn.execute(ddl)
                await conn.commit()
                logger.info("Migration applied: added column '%s'.", col_name)
            except Exception as exc:
                if "duplicate column" in str(exc).lower():
                    logger.debug("Column '%s' already exists — skipping.", col_name)
                else:
                    logger.error("Migration failed for column '%s': %s", col_name, exc)
                    raise

        # Phase 6.2: Covering index for change detection (depends on sha256 column above)
        try:
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_change_detection "
                "ON files(path, modified_at, sha256)"
            )
            await conn.commit()
        except Exception:
            pass  # Silently skip if sha256 column doesn't exist yet

        # Table-level migration: folder_profiles
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS folder_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder_path TEXT UNIQUE NOT NULL,
                    folder_tag TEXT NOT NULL,
                    profile_text TEXT NOT NULL DEFAULT '',
                    project_type TEXT NOT NULL DEFAULT 'unknown',
                    file_count INTEGER NOT NULL DEFAULT 0,
                    total_size_bytes INTEGER NOT NULL DEFAULT 0,
                    top_extensions TEXT NOT NULL DEFAULT '',
                    key_files TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_folder_profiles_tag "
                "ON folder_profiles(folder_tag)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_folder_profiles_type "
                "ON folder_profiles(project_type)"
            )
            await conn.commit()
            logger.debug("folder_profiles table ensured.")
        except Exception as exc:
            logger.debug("folder_profiles migration note: %s", exc)

        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS unreal_project_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder_path TEXT UNIQUE NOT NULL,
                    folder_tag TEXT NOT NULL,
                    project_name TEXT NOT NULL DEFAULT '',
                    engine_version TEXT NOT NULL DEFAULT 'unknown',
                    total_assets INTEGER NOT NULL DEFAULT 0,
                    map_count INTEGER NOT NULL DEFAULT 0,
                    character_blueprints INTEGER NOT NULL DEFAULT 0,
                    pawn_blueprints INTEGER NOT NULL DEFAULT 0,
                    skeletal_meshes INTEGER NOT NULL DEFAULT 0,
                    material_count INTEGER NOT NULL DEFAULT 0,
                    niagara_systems INTEGER NOT NULL DEFAULT 0,
                    environment_assets INTEGER NOT NULL DEFAULT 0,
                    metadata_source TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_unreal_facts_folder_tag "
                "ON unreal_project_facts(folder_tag)"
            )
            await conn.commit()
            logger.debug("unreal_project_facts table ensured.")
        except Exception as exc:
            logger.debug("unreal_project_facts migration note: %s", exc)

        # Phase 9.1: Drop the heavy covering index that duplicates chunk text
        try:
            await conn.execute("DROP INDEX IF EXISTS idx_chunks_covering")
            await conn.commit()
        except Exception as exc:
            logger.warning("Failed to drop idx_chunks_covering: %s", exc)

        # Phase 9.2: Rebuild chunk_fts with detail=column to save ~40% space
        try:
            cur = await conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='chunk_fts'")
            row = await cur.fetchone()
            if row and "detail=column" not in row[0]:
                # We use content="" (contentless) because the actual text is compressed
                # in the source table and decompressed via triggers into the FTS index.
                await conn.executescript("""
                    DROP TRIGGER IF EXISTS chunks_ai;
                    DROP TRIGGER IF EXISTS chunks_ad;
                    DROP TRIGGER IF EXISTS chunks_au;
                    DROP TABLE IF EXISTS chunk_fts;
                    
                    CREATE VIRTUAL TABLE chunk_fts USING fts5(
                        chunks_text, content='', detail=column
                    );
                    
                    CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
                      INSERT INTO chunk_fts(rowid, chunks_text) VALUES (new.id, zlib_decompress(new.text_preview));
                    END;
                    
                    CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
                      INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text) VALUES('delete', old.id, zlib_decompress(old.text_preview));
                    END;
                    
                    CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
                      INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text) VALUES('delete', old.id, zlib_decompress(old.text_preview));
                      INSERT INTO chunk_fts(rowid, chunks_text) VALUES (new.id, zlib_decompress(new.text_preview));
                    END;
                """)
                await conn.commit()
                logger.info("Storage optimization: Optimized chunk_fts schema.")
        except Exception as exc:
            logger.warning("Failed to rebuild FTS table: %s", exc)

    async def fts_optimize(self) -> None:
        """Optimizes the FTS5 index to reduce fragmentation and improve search speed."""
        conn = self._get_conn()
        try:
            logger.info("Optimizing FTS5 index (chunk_fts)...")
            await conn.execute("INSERT INTO chunk_fts(chunk_fts) VALUES('optimize')")
            await conn.commit()
            logger.info("FTS5 index optimization complete.")
        except Exception as e:
            logger.warning("FTS5 optimization failed: %s", e)

    async def vacuum(self) -> None:
        """Compacts the database and optimizes search indexes."""
        conn = self._get_conn()
        logger.info("Starting database maintenance (FTS optimize + VACUUM)...")
        
        # Optimize FTS before vacuuming to reclaim maximum space
        await self.fts_optimize()
        
        await conn.execute("VACUUM")
        await conn.commit()
        logger.info("Database maintenance completed.")

    async def insert_file(
        self,
        file_data: Dict[str, Any],
        *,
        auto_commit: bool = True,
    ) -> int:
        """Inserts file metadata and returns the new file id.

        Set ``auto_commit=False`` when batching many writes in a single transaction.
        """
        conn = self._get_conn()
        file_data.setdefault("summary", "")
        file_data.setdefault("sha256", "")
        query = """
        INSERT INTO files (path, size, modified_at, type, folder_tag, summary, sha256)
        VALUES (:path, :size, :modified_at, :type, :folder_tag, :summary, :sha256)
        ON CONFLICT(path) DO UPDATE SET
            size=excluded.size,
            modified_at=excluded.modified_at,
            type=excluded.type,
            folder_tag=excluded.folder_tag,
            summary=excluded.summary,
            sha256=excluded.sha256
        RETURNING id;
        """
        async with conn.execute(query, file_data) as cursor:
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError(f"INSERT RETURNING id failed for {file_data.get('path')}")
            file_id: int = row[0]
            if auto_commit:
                await conn.commit()
            return file_id

    async def insert_chunk(self, chunk_data: Dict[str, Any]) -> int:
        """Inserts a chunk and returns the new chunk id."""
        conn = self._get_conn()
        compressed_text = zlib.compress(chunk_data["text_preview"].encode("utf-8")) if isinstance(chunk_data["text_preview"], str) else chunk_data["text_preview"]
        query = """
        INSERT INTO chunks (file_id, start_offset, end_offset, text_preview)
        VALUES (:file_id, :start_offset, :end_offset, :text_preview)
        RETURNING id;
        """
        async with conn.execute(query, {**chunk_data, "text_preview": compressed_text}) as cursor:
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("INSERT RETURNING id failed for chunk")
            chunk_id: int = row[0]
            return chunk_id

    async def insert_chunks_bulk(self, chunks: List[Dict[str, Any]]) -> List[int]:
        """Insert multiple chunks efficiently in a single transaction.

        Uses a batch INSERT approach: inserts all rows first,
        then reads back the generated IDs.  This is significantly
        faster than individual INSERT RETURNING for large batches.
        """
        if not chunks:
            return []
        conn = self._get_conn()

        # Safely compress text without mutating the caller's dictionaries
        insert_data = [
            {
                "file_id": c["file_id"],
                "start_offset": c["start_offset"],
                "end_offset": c["end_offset"],
                "text_preview": zlib.compress(c["text_preview"].encode("utf-8")) if isinstance(c["text_preview"], str) else c["text_preview"]
            } for c in chunks
        ]

        # For small batches, the per-row RETURNING approach is fine
        if len(insert_data) <= 20:
            ids: List[int] = []
            for chunk in insert_data:
                async with conn.execute(
                    "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview) "
                    "VALUES (:file_id, :start_offset, :end_offset, :text_preview) RETURNING id;",
                    chunk,
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        ids.append(row[0])
            await conn.commit()
            return ids

        # For larger batches, use executemany + read back IDs
        # Wrap in an explicit transaction to prevent race conditions
        # with concurrent inserts between MAX(id) and the bulk insert.
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute("SELECT COALESCE(MAX(id), 0) FROM chunks") as cur:
                row = await cur.fetchone()
                start_id = (row[0] if row else 0) + 1

            # Prevent SQLITE_MAX_VARIABLE_NUMBER crashes by slicing insert_data
            MAX_ROWS_PER_QUERY = 5000
            for i in range(0, len(insert_data), MAX_ROWS_PER_QUERY):
                batch = insert_data[i:i + MAX_ROWS_PER_QUERY]
                await conn.executemany(
                    "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview) "
                    "VALUES (:file_id, :start_offset, :end_offset, :text_preview);",
                    batch,
                )

            # Read back the generated IDs (they are sequential in SQLite)
            async with conn.execute(
                "SELECT id FROM chunks WHERE id >= ? ORDER BY id", (start_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                ids = [r[0] for r in rows]

            await conn.commit()
            return ids
        except Exception:
            await conn.rollback()
            raise

    async def commit(self) -> None:
        """Explicitly commits the current transaction."""
        if self.conn:
            await self.conn.commit()

    async def delete_file_chunks(self, file_id: int, *, auto_commit: bool = True) -> None:
        """Deletes all chunks associated with a file.

        Set ``auto_commit=False`` when called from a larger batch transaction.
        """
        conn = self._get_conn()
        await conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        if auto_commit:
            await conn.commit()

    async def get_file_chunks(self, file_id: int) -> List[aiosqlite.Row]:
        """Returns all chunks for a given file id, decompressing text_preview."""
        conn = self._get_conn()
        async with conn.execute("SELECT id, file_id, start_offset, end_offset, created_at, zlib_decompress(text_preview) as text_preview FROM chunks WHERE file_id = ?", (file_id,)) as cursor:
            return list(await cursor.fetchall())

    async def get_file_by_path(self, path: str) -> Optional[aiosqlite.Row]:
        """Returns file metadata by path."""
        conn = self._get_conn()
        async with conn.execute("SELECT * FROM files WHERE path = ?", (path,)) as cursor:
            return await cursor.fetchone()

    async def get_existing_file_ids(self, paths: List[str]) -> Dict[str, int]:
        """Return {path: file_id} for every path that already exists in the DB.

        Used by the indexing pipeline to avoid per-file existence lookups.
        """
        result: Dict[str, int] = {}
        conn = self._get_conn()
        batch_size = 900
        for i in range(0, len(paths), batch_size):
            batch = paths[i : i + batch_size]
            placeholders = ",".join("?" for _ in batch)
            query = f"SELECT path, id FROM files WHERE path IN ({placeholders})"
            async with conn.execute(query, batch) as cursor:
                async for row in cursor:
                    result[row[0]] = row[1]
        return result

    async def get_files_modified_map(self, paths: List[str]) -> Dict[str, str]:
        """Return {path: modified_at} for every path that already exists in the DB."""
        result: Dict[str, str] = {}
        conn = self._get_conn()
        batch_size = 900
        for i in range(0, len(paths), batch_size):
            batch = paths[i : i + batch_size]
            placeholders = ",".join("?" for _ in batch)
            query = f"SELECT path, modified_at FROM files WHERE path IN ({placeholders})"
            async with conn.execute(query, batch) as cursor:
                async for row in cursor:
                    result[row[0]] = row[1]
        return result

    async def get_files_sha256_map(self, paths: List[str]) -> Dict[str, str]:
        """Return {path: sha256} for every path that already exists in the DB."""
        result: Dict[str, str] = {}
        conn = self._get_conn()
        batch_size = 900
        for i in range(0, len(paths), batch_size):
            batch = paths[i : i + batch_size]
            placeholders = ",".join("?" for _ in batch)
            query = f"SELECT path, sha256 FROM files WHERE path IN ({placeholders})"
            async with conn.execute(query, batch) as cursor:
                async for row in cursor:
                    result[row[0]] = row[1]
        return result

    async def get_files_change_map(
        self, paths: List[str]
    ) -> Dict[str, Tuple[str, str]]:
        """Return {path: (modified_at, sha256)} in a SINGLE query.

        Replaces separate calls to get_files_modified_map + get_files_sha256_map
        to halve the number of DB round-trips during change detection.
        """
        result: Dict[str, Tuple[str, str]] = {}
        conn = self._get_conn()
        batch_size = 900
        for i in range(0, len(paths), batch_size):
            batch = paths[i : i + batch_size]
            placeholders = ",".join("?" for _ in batch)
            query = f"SELECT path, modified_at, COALESCE(sha256, '') FROM files WHERE path IN ({placeholders})"
            async with conn.execute(query, batch) as cursor:
                async for row in cursor:
                    result[row[0]] = (row[1], row[2])
        return result

    async def increment_usage_count(self, file_path: str) -> None:
        """Increments the usage_count for a given file path."""
        conn = self._get_conn()
        await conn.execute(
            "UPDATE files SET usage_count = usage_count + 1 WHERE path = ?",
            (file_path,),
        )
        await conn.commit()

    async def batch_increment_usage(self, file_paths: List[str]) -> None:
        """Increment usage_count for multiple file paths in a single transaction."""
        if not file_paths:
            return
        conn = self._get_conn()
        counts: Dict[str, int] = {}
        for path in file_paths:
            counts[path] = counts.get(path, 0) + 1

        when_clauses = []
        case_params: List[Any] = []
        for path, increment in counts.items():
            when_clauses.append("WHEN ? THEN usage_count + ?")
            case_params.extend([path, increment])

        in_params = list(counts.keys())
        placeholders = ",".join("?" for _ in in_params)
        sql = (
            "UPDATE files SET usage_count = CASE path "
            + " ".join(when_clauses)
            + " ELSE usage_count END WHERE path IN ("
            + placeholders
            + ")"
        )
        await conn.execute(sql, tuple(case_params + in_params))
        await conn.commit()

    async def get_all_files(self) -> List[aiosqlite.Row]:
        """Returns all indexed files ordered by folder and path."""
        conn = self._get_conn()
        async with conn.execute(
            "SELECT path, size, type, folder_tag, usage_count "
            "FROM files ORDER BY folder_tag, path"
        ) as cursor:
            return list(await cursor.fetchall())

    async def stream_all_nodes(self):
        """Asynchronous generator to yield all folders and files for scalable visualization."""
        conn = self._get_conn()
        # First stream all folder profiles
        async with conn.execute(
            "SELECT folder_path, project_type, file_count, total_size_bytes FROM folder_profiles"
        ) as cursor:
            async for row in cursor:
                yield {
                    "is_folder": True,
                    "path": row["folder_path"],
                    "project_type": row["project_type"],
                    "file_count": row["file_count"],
                    "size": row["total_size_bytes"]
                }
        
        # Then stream all files
        async with conn.execute(
            "SELECT path, size, type, folder_tag FROM files"
        ) as cursor:
            async for row in cursor:
                yield {
                    "is_folder": False,
                    "path": row["path"],
                    "size": row["size"],
                    "type": row["type"],
                    "folder_tag": row["folder_tag"]
                }

    async def get_file_stats_summary(self) -> Dict[str, Any]:
        """Return aggregate file statistics grouped by type and folder_tag.

        Uses a single-pass CTE to avoid scanning the files table twice.
        """
        conn = self._get_conn()

        # Phase 6.3: Single-pass CTE replaces two separate GROUP BY scans
        rows = await (
            await conn.execute(
                "WITH "
                "type_agg AS ("
                "  SELECT type, COUNT(*) AS cnt, SUM(size) AS total_bytes "
                "  FROM files GROUP BY type"
                "), "
                "folder_agg AS ("
                "  SELECT folder_tag, COUNT(*) AS cnt "
                "  FROM files GROUP BY folder_tag"
                ") "
                "SELECT 'T' AS src, type AS key, cnt, total_bytes FROM type_agg "
                "UNION ALL "
                "SELECT 'F' AS src, folder_tag AS key, cnt, 0 FROM folder_agg "
                "ORDER BY src, cnt DESC"
            )
        ).fetchall()

        type_rows = [(r[1], r[2], r[3]) for r in rows if r[0] == 'T']
        folder_rows = [(r[1], r[2]) for r in rows if r[0] == 'F']

        total_files = sum(r[1] for r in type_rows)
        total_bytes = sum(r[2] or 0 for r in type_rows)

        return {
            "total_files": total_files,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "by_type": [
                {"ext": r[0], "count": r[1], "size_mb": round((r[2] or 0) / (1024 * 1024), 2)}
                for r in type_rows
            ],
            "by_folder": [
                {"folder": r[0] or "Unknown", "count": r[1]}
                for r in folder_rows
            ],
            "database_size_bytes": os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0,
        }

    async def get_counts(self) -> Tuple[int, int]:
        """Return (file_count, chunk_count) in a single public call."""
        conn = self._get_conn()
        async with conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM files) AS file_count, "
            "(SELECT COUNT(*) FROM chunks) AS chunk_count"
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return 0, 0
            return row[0], row[1]

    async def execute_query(self, sql: str, params: tuple = ()) -> List[Any]:
        """Execute a read-only SQL query and return all rows."""
        conn = self._get_conn()
        async with conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    async def execute_write(self, sql: str, params: tuple = ()) -> None:
        """Execute a write SQL statement and commit."""
        conn = self._get_conn()
        await conn.execute(sql, params)
        await conn.commit()

    async def save_query(
        self, question: str, answer: str, source_count: int, latency_ms: float
    ) -> int:
        """Save a query to the history table and return its id."""
        conn = self._get_conn()
        async with conn.execute(
            "INSERT INTO query_history (question, answer, source_count, latency_ms) "
            "VALUES (?, ?, ?, ?) RETURNING id",
            (question, answer, source_count, latency_ms),
        ) as cursor:
            row = await cursor.fetchone()
            await conn.commit()
            return row[0] if row else 0

    async def get_query_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent queries from history."""
        conn = self._get_conn()
        async with conn.execute(
            "SELECT id, question, answer, source_count, latency_ms, created_at "
            "FROM query_history ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "question": r[1],
                    "answer": r[2],
                    "source_count": r[3],
                    "latency_ms": r[4],
                    "created_at": r[5],
                }
                for r in rows
            ]

    async def clear_query_history(self) -> Dict[str, str]:
        """Delete all entries from the query_history table."""
        conn = self._get_conn()
        await conn.execute("DELETE FROM query_history")
        await conn.commit()
        return {"message": "Query history cleared successfully."}

    async def cleanup_stale_files(self) -> List[str]:
        """Remove index entries for files that no longer exist on disk.

        Returns list of paths that were cleaned up.
        """
        conn = self._get_conn()
        cleaned: List[str] = []
        stale_ids: List[int] = []
        async with conn.execute("SELECT id, path FROM files") as cursor:
            rows = list(await cursor.fetchall())
        for row in rows:
            file_id, path = row[0], row[1]
            if not os.path.exists(path):
                stale_ids.append(file_id)
                cleaned.append(path)
                logger.info("Cleaned stale file: %s", path)
        if stale_ids:
            placeholders = ",".join("?" for _ in stale_ids)
            await conn.execute(
                f"DELETE FROM files WHERE id IN ({placeholders})",
                tuple(stale_ids),
            )
            await conn.commit()
        return cleaned

    async def clear_all(self) -> Dict[str, int]:
        """Delete ALL indexed data: files, chunks, FTS, and query history.

        Returns counts of removed files and chunks.
        """
        conn = self._get_conn()

        cur = await conn.execute("SELECT COUNT(*) FROM files")
        row = await cur.fetchone()
        files_count = row[0] if row else 0
        await cur.close()

        cur = await conn.execute("SELECT COUNT(*) FROM chunks")
        row = await cur.fetchone()
        chunks_count = row[0] if row else 0
        await cur.close()

        await conn.executescript("""
            -- Remove triggers so chunk deletes don't touch FTS
            DROP TRIGGER IF EXISTS chunks_ai;
            DROP TRIGGER IF EXISTS chunks_ad;
            DROP TRIGGER IF EXISTS chunks_au;

            -- Drop the FTS virtual table entirely
            DROP TABLE IF EXISTS chunk_fts;

            -- Now safe to delete all data
            DELETE FROM chunks;
            DELETE FROM files;
            DELETE FROM query_history;
            DELETE FROM folder_profiles;
            DELETE FROM unreal_project_facts;

            -- Recreate FTS table and triggers
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                chunks_text,
                content=chunks,
                content_rowid=id
            );
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
              INSERT INTO chunk_fts(rowid, chunks_text) VALUES (new.id, new.text_preview);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
              INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text) VALUES('delete', old.id, old.text_preview);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
              INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text) VALUES('delete', old.id, old.text_preview);
              INSERT INTO chunk_fts(rowid, chunks_text) VALUES (new.id, new.text_preview);
            END;
        """)

        logger.info("Cleared all data: %d files, %d chunks", files_count, chunks_count)
        return {"files_removed": files_count, "chunks_removed": chunks_count}

    async def get_files_by_filter(
        self,
        file_type: Optional[str] = None,
        folder_tag: Optional[str] = None,
    ) -> List[aiosqlite.Row]:
        """Return files matching optional type/folder filters."""
        conn = self._get_conn()
        conditions: List[str] = []
        params: List[Any] = []
        if file_type:
            conditions.append("type = ?")
            params.append(file_type)
        if folder_tag:
            conditions.append("folder_tag = ?")
            params.append(folder_tag)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT path, size, type, folder_tag, usage_count FROM files{where} ORDER BY path"
        async with conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    async def delete_files_by_folder_prefix(self, folder: str) -> None:
        """Delete all files (and cascading chunks) whose path starts with *folder*."""
        conn = self._get_conn()
        await conn.execute(
            "DELETE FROM files WHERE path LIKE ? || '%'",
            (folder,),
        )
        await conn.commit()

    async def is_healthy(self) -> bool:
        """Quick DB health check – runs a trivial query."""
        try:
            conn = self._get_conn()
            async with conn.execute("SELECT 1") as cursor:
                row = await cursor.fetchone()
                return row is not None and row[0] == 1
        except Exception:
            return False

    async def get_all_summaries(self) -> List[Dict[str, Any]]:
        """Return (id, path, summary) for every file that has a non-empty summary."""
        conn = self._get_conn()
        async with conn.execute(
            "SELECT id, path, summary FROM files WHERE summary != '' ORDER BY id"
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"id": r[0], "path": r[1], "summary": r[2]} for r in rows]

    # ── Folder profiles ───────────────────────────────────────────────

    async def upsert_folder_profile(
        self, profile: Dict[str, Any], *, auto_commit: bool = True
    ) -> None:
        """Insert or update a folder profile.

        Set ``auto_commit=False`` when batching multiple profiles in
        a single transaction for better performance.
        """
        conn = self._get_conn()
        await conn.execute(
            """
            INSERT INTO folder_profiles
                (folder_path, folder_tag, profile_text, project_type,
                 file_count, total_size_bytes, top_extensions, key_files, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(folder_path) DO UPDATE SET
                folder_tag = excluded.folder_tag,
                profile_text = excluded.profile_text,
                project_type = excluded.project_type,
                file_count = excluded.file_count,
                total_size_bytes = excluded.total_size_bytes,
                top_extensions = excluded.top_extensions,
                key_files = excluded.key_files,
                updated_at = datetime('now')
            """,
            (
                profile["folder_path"],
                profile["folder_tag"],
                profile["profile_text"],
                profile["project_type"],
                profile["file_count"],
                profile["total_size_bytes"],
                profile["top_extensions"],
                profile["key_files"],
            ),
        )
        if auto_commit:
            await conn.commit()

    async def get_all_folder_profiles(self) -> List[Dict[str, Any]]:
        """Return every stored folder profile."""
        conn = self._get_conn()
        async with conn.execute(
            "SELECT folder_path, folder_tag, profile_text, project_type, "
            "file_count, total_size_bytes, top_extensions, key_files "
            "FROM folder_profiles ORDER BY folder_path"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "folder_path": r[0],
                    "folder_tag": r[1],
                    "profile_text": r[2],
                    "project_type": r[3],
                    "file_count": r[4],
                    "total_size_bytes": r[5],
                    "top_extensions": r[6],
                    "key_files": r[7],
                }
                for r in rows
            ]

    async def get_folder_profiles_text(self) -> str:
        """Return a human-readable summary of all folder profiles for LLM context."""
        profiles = await self.get_all_folder_profiles()
        if not profiles:
            return ""
        lines = ["=== Indexed Project/Folder Profiles ==="]
        for p in profiles:
            size_mb = round(p["total_size_bytes"] / (1024 * 1024), 2)
            lines.append(f"\n## {p['folder_tag']} — {p['project_type']} project")
            lines.append(f"   Path: {p['folder_path']}")
            lines.append(f"   Files: {p['file_count']} ({size_mb} MB)")
            lines.append(f"   Top extensions: {p['top_extensions']}")
            if p["key_files"]:
                lines.append(f"   Key files: {p['key_files']}")
            if p["profile_text"]:
                lines.append(f"   Description: {p['profile_text']}")
        lines.append("=" * 50)
        return "\n".join(lines)

    async def upsert_unreal_project_facts(self, facts: Dict[str, Any]) -> None:
        """Insert or update structured Unreal project facts."""
        conn = self._get_conn()
        await conn.execute(
            """
            INSERT INTO unreal_project_facts
                (folder_path, folder_tag, project_name, engine_version,
                 total_assets, map_count, character_blueprints, pawn_blueprints,
                 skeletal_meshes, material_count, niagara_systems,
                 environment_assets, metadata_source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(folder_path) DO UPDATE SET
                folder_tag = excluded.folder_tag,
                project_name = excluded.project_name,
                engine_version = excluded.engine_version,
                total_assets = excluded.total_assets,
                map_count = excluded.map_count,
                character_blueprints = excluded.character_blueprints,
                pawn_blueprints = excluded.pawn_blueprints,
                skeletal_meshes = excluded.skeletal_meshes,
                material_count = excluded.material_count,
                niagara_systems = excluded.niagara_systems,
                environment_assets = excluded.environment_assets,
                metadata_source = excluded.metadata_source,
                updated_at = datetime('now')
            """,
            (
                facts["folder_path"],
                facts["folder_tag"],
                facts["project_name"],
                facts["engine_version"],
                facts["total_assets"],
                facts["map_count"],
                facts["character_blueprints"],
                facts["pawn_blueprints"],
                facts["skeletal_meshes"],
                facts["material_count"],
                facts["niagara_systems"],
                facts["environment_assets"],
                facts["metadata_source"],
            ),
        )
        await conn.commit()

    async def get_all_unreal_project_facts(self) -> List[Dict[str, Any]]:
        """Return all imported Unreal project facts."""
        conn = self._get_conn()
        async with conn.execute(
            "SELECT folder_path, folder_tag, project_name, engine_version, "
            "total_assets, map_count, character_blueprints, pawn_blueprints, "
            "skeletal_meshes, material_count, niagara_systems, environment_assets, metadata_source "
            "FROM unreal_project_facts ORDER BY folder_tag"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "folder_path": r[0],
                    "folder_tag": r[1],
                    "project_name": r[2],
                    "engine_version": r[3],
                    "total_assets": r[4],
                    "map_count": r[5],
                    "character_blueprints": r[6],
                    "pawn_blueprints": r[7],
                    "skeletal_meshes": r[8],
                    "material_count": r[9],
                    "niagara_systems": r[10],
                    "environment_assets": r[11],
                    "metadata_source": r[12],
                }
                for r in rows
            ]
