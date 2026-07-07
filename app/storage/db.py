"""
Database manager module for Personal Memory Assistant.
Handles interactions with SQLite using aiosqlite for metadata storage.
"""

import asyncio
import contextlib
import logging
import os
import sys
import uuid
import zlib
from pathlib import Path
from typing import Any

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


import functools

def serialize_write(func):
    @functools.wraps(func)
    async def wrapper(self: "DatabaseManager", *args, **kwargs):
        async with self._write_lock:
            return await func(self, *args, **kwargs)
    return wrapper


class DatabaseManager:
    """Manages the SQLite database connection and operations with a read-connection pool."""

    def __init__(self, db_path: str = "pma_metadata.db", pool_size: int = 4):
        """Initializes the DatabaseManager."""
        self.db_path = db_path
        self.pool_size = pool_size
        self._write_conn: aiosqlite.Connection | None = None
        self._read_pool: asyncio.Queue[aiosqlite.Connection] | None = None
        self._pool_initialized = False
        self._pool_lock: asyncio.Lock | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Establish connection pool to the SQLite database."""
        if self._pool_lock is None:
            self._pool_lock = asyncio.Lock()

        if self._pool_initialized:
            return

        async with self._pool_lock:
            if self._pool_initialized:
                return

            if self._read_pool is None:
                self._read_pool = asyncio.Queue()

            # 1. Primary write connection
            self._write_conn = await aiosqlite.connect(self.db_path)
            await self._configure_conn(self._write_conn)

            # 2. Read connection pool
            for _ in range(self.pool_size):
                conn = await aiosqlite.connect(self.db_path)
                await self._configure_conn(conn)
                await self._read_pool.put(conn)

            self._pool_initialized = True

    async def _configure_conn(self, conn: aiosqlite.Connection) -> None:
        """Apply performance pragmas and custom functions to a connection."""
        conn.row_factory = aiosqlite.Row
        await conn.create_function("zlib_decompress", 1, _zlib_decompress_fn)

        await conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")
        await conn.execute("PRAGMA journal_mode = WAL;")
        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.execute("PRAGMA synchronous = NORMAL;")
        await conn.execute("PRAGMA busy_timeout = 5000;")
        # ── Performance PRAGMAs ──────────────────────────────────
        await conn.execute("PRAGMA cache_size = -16384;")  # 16 MB page cache for memory constraint
        await conn.execute(
            "PRAGMA mmap_size = 268435456;"
        )  # 256 MB memory-mapped I/O (OS managed, doesn't consume app RAM directly)
        await conn.execute("PRAGMA temp_store = MEMORY;")  # temp tables in RAM
        # NOTE: page_size only affects new databases. Existing ones ignore this until VACUUM.
        await conn.execute("PRAGMA page_size = 32768;")
        await conn.execute("PRAGMA threads = 4;")
        # NOTE: read_uncommitted only has an effect in shared-cache mode (aiosqlite uses private).
        await conn.execute("PRAGMA read_uncommitted = ON;")
        await conn.execute("PRAGMA wal_autocheckpoint = 1000;")

    @property
    def conn(self) -> aiosqlite.Connection | None:
        """Compatibility property: returns the write connection."""
        return self._write_conn

    @contextlib.asynccontextmanager
    async def _get_read_conn(self):
        """Borrow a connection from the read pool."""
        if not self._pool_initialized:
            await self.connect()

        if self.db_path == ":memory:":
            yield self._write_conn
            return

        assert self._read_pool is not None
        conn = await self._read_pool.get()
        try:
            yield conn
        finally:
            await self._read_pool.put(conn)

    def _get_conn(self) -> aiosqlite.Connection:
        """Return the active write connection, raising if not connected."""
        if self._write_conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._write_conn

    async def close(self):
        """Close all connections in the pool."""
        if self._pool_lock is None:
            return

        async with self._pool_lock:
            if self._write_conn:
                await self._write_conn.close()
                self._write_conn = None

            if self._read_pool:
                while not self._read_pool.empty():
                    conn = self._read_pool.get_nowait()
                    await conn.close()

            self._pool_initialized = False

    async def init_db(self, schema_path: str = "app/storage/schema.sql") -> None:
        """Initialize the database with the schema."""
        await self.connect()
        conn = self._get_conn()
        try:
            # Support PyInstaller frozen path resolution
            if getattr(sys, "frozen", False):
                import __main__

                resolved_schema = __main__._get_resource_path(schema_path)
            else:
                resolved_schema = schema_path

            schema = Path(resolved_schema).read_text(encoding="utf-8")
            await conn.executescript(schema)
            await conn.commit()
            logger.info("Database initialized successfully.")
        except Exception as e:
            logger.error("Error initializing database: %s", e)
            raise
        await self._migrate(conn)

        # H-15: Auto-VACUUM locks DB. Check fragmentation on startup.
        # If there are a large number of free pages, do an incremental vacuum.
        try:
            async with conn.execute("PRAGMA freelist_count;") as cur:
                row = await cur.fetchone()
                if row and row[0] > 10000:
                    logger.info(
                        "Database heavily fragmented (%d free pages). Running incremental vacuum.",
                        row[0],
                    )
                    await conn.execute("PRAGMA incremental_vacuum(5000);")
                    await conn.commit()
        except Exception as e:
            logger.warning("Failed to run startup incremental vacuum: %s", e)

    async def _apply_column_migrations(self, conn: "aiosqlite.Connection") -> None:
        async def _already_applied(name: str) -> bool:
            async with conn.execute(
                "SELECT 1 FROM schema_migrations WHERE migration_name = ?", (name,)
            ) as cur:
                return await cur.fetchone() is not None

        async def _mark_applied(name: str) -> None:
            await conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)", (name,)
            )
            await conn.commit()

        migrations = [
            ("summary", "ALTER TABLE files ADD COLUMN summary TEXT DEFAULT ''"),
            ("sha256", "ALTER TABLE files ADD COLUMN sha256 TEXT DEFAULT ''"),
            (
                "files_created_at",
                "ALTER TABLE files ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
            ),
            (
                "chunks_created_at",
                "ALTER TABLE chunks ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
            ),
            (
                "chunks_sentence_offsets",
                "ALTER TABLE chunks ADD COLUMN sentence_offsets TEXT",
            ),
            (
                "chunks_segmenter_version",
                "ALTER TABLE chunks ADD COLUMN segmenter_version TEXT",
            ),
        ]
        for col_name, ddl in migrations:
            if await _already_applied(col_name):
                continue
            try:
                await conn.execute(ddl)
                await conn.commit()
                await _mark_applied(col_name)
                logger.info("Migration applied: '%s'.", col_name)
            except Exception as exc:
                if "duplicate column" in str(exc).lower():
                    await _mark_applied(col_name)
                else:
                    logger.error("Migration failed for '%s': %s", col_name, exc)
                    raise

    async def _migrate(self, conn: "aiosqlite.Connection") -> None:
        """Apply safe, idempotent schema migrations with audit tracking."""
        # ── Migration tracking table (5.3) ──────────────────────────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                migration_name TEXT UNIQUE NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await conn.commit()

        await self._apply_column_migrations(conn)

        # Phase 6.2: Covering index for change detection (depends on sha256 column above)
        try:
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_change_detection "
                "ON files(path, modified_at, sha256)"
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_files_size ON files(size)")
            await conn.commit()
        except Exception:
            logger.debug("Failed to create covering index.", exc_info=True)
            pass  # Silently skip if column doesn't exist yet

        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS chunk_embeddings (
                    chunk_id INTEGER PRIMARY KEY,
                    embedding BLOB NOT NULL,
                    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
                )
            """)
            await conn.commit()
            logger.debug("chunk_embeddings table ensured.")
        except Exception as exc:
            logger.debug("chunk_embeddings migration note: %s", exc)

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
                "CREATE INDEX IF NOT EXISTS idx_folder_profiles_tag ON folder_profiles(folder_tag)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_folder_profiles_type "
                "ON folder_profiles(project_type)"
            )
            await conn.commit()
            logger.debug("folder_profiles table ensured.")
        except Exception as exc:
            logger.debug("folder_profiles migration note: %s", exc)

        # GraphRAG nodes
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS kg_nodes (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    properties TEXT DEFAULT '{}',
                    chunk_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
                )
            """)
            await conn.commit()
            logger.debug("kg_nodes table ensured.")
        except Exception as exc:
            logger.debug("kg_nodes migration note: %s", exc)

        # GraphRAG nodes schema upgrade migration (for pre-existing DBs)
        try:
            has_chunk_id = False
            async with conn.execute("PRAGMA table_info(kg_nodes)") as cur:
                async for row in cur:
                    if row[1] == "chunk_id":
                        has_chunk_id = True
                        break
            if not has_chunk_id:
                logger.info("Migrating kg_nodes table to add chunk_id foreign key cascade...")
                await conn.execute("ALTER TABLE kg_nodes RENAME TO kg_nodes_old")
                await conn.execute("""
                    CREATE TABLE kg_nodes (
                        id TEXT PRIMARY KEY,
                        type TEXT NOT NULL,
                        label TEXT NOT NULL,
                        properties TEXT DEFAULT '{}',
                        chunk_id INTEGER,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
                    )
                """)
                await conn.execute("""
                    INSERT INTO kg_nodes (id, type, label, properties, chunk_id, created_at)
                    SELECT id, type, label, properties,
                           CAST(json_extract(properties, '$.chunk_id') AS INTEGER),
                           created_at
                    FROM kg_nodes_old
                """)
                await conn.execute("DROP TABLE kg_nodes_old")
                await conn.commit()
                logger.info("kg_nodes migration completed successfully.")
        except Exception as exc:
            logger.warning("kg_nodes schema migration failed: %s", exc)

        # GraphRAG edges
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS kg_edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    properties TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (source, target, relation),
                    FOREIGN KEY (source) REFERENCES kg_nodes(id) ON DELETE CASCADE,
                    FOREIGN KEY (target) REFERENCES kg_nodes(id) ON DELETE CASCADE
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_target ON kg_edges(target)")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_kg_edges_relation ON kg_edges(relation)"
            )
            await conn.commit()
            logger.debug("kg_edges table ensured.")
        except Exception as exc:
            logger.debug("kg_edges migration note: %s", exc)

        # Phase 9.1: Drop the heavy covering index that duplicates chunk text
        try:
            await conn.execute("DROP INDEX IF EXISTS idx_chunks_covering")
            await conn.commit()
        except Exception as exc:
            logger.warning("Failed to drop idx_chunks_covering: %s", exc)

        # Phase 9.2: Rebuild chunk_fts with detail=column to save ~40% space
        try:
            cur = await conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunk_fts'"
            )
            row = await cur.fetchone()  # type: ignore
            if row and ("detail=full" not in row[0] or "trigram" not in row[0]):
                # We use content="" (contentless) because the actual text is compressed
                # in the source table and decompressed via triggers into the FTS index.
                await conn.executescript("""
                    DROP TRIGGER IF EXISTS chunks_ai;
                    DROP TRIGGER IF EXISTS chunks_ad;
                    DROP TABLE IF EXISTS chunk_fts;
                    CREATE VIRTUAL TABLE chunk_fts USING fts5(
                        chunks_text, content='', tokenize='trigram', detail=full
                    );
                    CREATE TRIGGER chunk_fts_ai AFTER INSERT ON chunks BEGIN
                        INSERT INTO chunk_fts(rowid, chunks_text)
                        VALUES (new.id, zlib_decompress(new.text_preview));
                    END;

                    CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
                      INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text)
                      VALUES('delete', old.id, zlib_decompress(old.text_preview));
                    END;

                    CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
                      INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text)
                      VALUES('delete', old.id, zlib_decompress(old.text_preview));
                      INSERT INTO chunk_fts(rowid, chunks_text)
                      VALUES (new.id, zlib_decompress(new.text_preview));
                    END;

                    INSERT INTO chunk_fts(rowid, chunks_text)
                    SELECT id, zlib_decompress(text_preview) FROM chunks;
                """)
                await conn.commit()
                logger.info("Storage optimization: Optimized chunk_fts schema.")
        except Exception as exc:
            logger.warning("Failed to rebuild FTS table: %s", exc)

    @serialize_write
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

    @serialize_write
    async def vacuum(self) -> None:
        """Compacts the database and optimizes search indexes."""
        conn = self._get_conn()
        logger.info("Starting database maintenance (FTS optimize + VACUUM)...")

        # Optimize FTS before vacuuming to reclaim maximum space
        try:
            logger.info("Optimizing FTS5 index (chunk_fts)...")
            await conn.execute("INSERT INTO chunk_fts(chunk_fts) VALUES('optimize')")
        except Exception as e:
            logger.warning("FTS5 optimization failed during vacuum: %s", e)

        # Commit transaction to allow VACUUM command to run
        await conn.commit()
        await conn.execute("VACUUM")
        await conn.commit()
        logger.info("Database maintenance completed.")

    @serialize_write
    async def incremental_vacuum(self, pages: int = 1000) -> None:
        """Run an incremental vacuum to reclaim space without locking for long periods."""
        conn = self._get_conn()
        try:
            logger.info("Running incremental vacuum (%d pages)...", pages)
            await conn.execute(f"PRAGMA incremental_vacuum({pages});")
            await conn.commit()
        except Exception as e:
            logger.warning("Incremental vacuum failed: %s", e)

    @serialize_write
    async def wal_checkpoint(self) -> None:
        """Force a WAL checkpoint to truncate the WAL file back to zero size.

        Call this after a large indexing run to reclaim disk space.
        Uses TRUNCATE mode which is safe and doesn't block readers.
        """
        conn = self._get_conn()
        try:
            await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await conn.commit()
            logger.info("WAL checkpoint completed - WAL file truncated.")
        except Exception as e:
            logger.warning("WAL checkpoint failed: %s", e)

    @serialize_write
    async def insert_file(
        self,
        file_data: dict[str, Any],
        *,
        auto_commit: bool = True,
    ) -> int:
        """Inserts file metadata and returns the new file id.

        Set ``auto_commit=False`` when batching many writes in a single transaction.
        """
        conn = self._get_conn()
        file_data.setdefault("summary", "")
        file_data.setdefault("sha256", "")
        if "type" in file_data:
            file_data["type"] = file_data["type"].lower()
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

    @serialize_write
    async def batch_insert_files(self, files_data: list[dict[str, Any]]) -> list[int]:
        """Inserts multiple file metadata records in a single transaction."""
        if not files_data:
            return []

        conn = self._get_conn()
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
        file_ids = []
        # Normalise types to lowercase for all batch entries
        for fd in files_data:
            fd.setdefault("summary", "")
            fd.setdefault("sha256", "")
            if "type" in fd:
                fd["type"] = fd["type"].lower()

        savepoint_name = None
        try:
            # Try explicit transaction; if already in one, use savepoint
            try:
                await conn.execute("BEGIN")
            except Exception as e:
                if "cannot start a transaction within a transaction" in str(e):
                    savepoint_name = f"sp_{uuid.uuid4().hex}"
                    await conn.execute(f"SAVEPOINT {savepoint_name}")
                else:
                    raise

            for fd in files_data:
                async with conn.execute(query, fd) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        file_ids.append(row[0])

            # Commit or release savepoint
            if savepoint_name:
                await conn.execute(f"RELEASE {savepoint_name}")
            else:
                await conn.commit()
        except Exception:
            # Rollback or rollback to savepoint
            if savepoint_name:
                with contextlib.suppress(Exception):
                    await conn.execute(f"ROLLBACK TO {savepoint_name}")
            else:
                with contextlib.suppress(Exception):
                    await conn.rollback()
            raise
        return file_ids

    @serialize_write
    async def insert_chunk(self, chunk_data: dict[str, Any]) -> int:
        """Inserts a chunk and returns the new chunk id."""
        conn = self._get_conn()
        compressed_text = (
            zlib.compress(chunk_data["text_preview"].encode("utf-8"))
            if isinstance(chunk_data["text_preview"], str)
            else chunk_data["text_preview"]
        )
        query = """
        INSERT INTO chunks (file_id, start_offset, end_offset, text_preview, sentence_offsets, segmenter_version)
        VALUES (:file_id, :start_offset, :end_offset, :text_preview, :sentence_offsets, :segmenter_version)
        RETURNING id;
        """  # noqa: E501
        data = {
            **chunk_data,
            "text_preview": compressed_text,
            "sentence_offsets": chunk_data.get("sentence_offsets"),
            "segmenter_version": chunk_data.get("segmenter_version"),
        }
        async with conn.execute(query, data) as cursor:
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("INSERT RETURNING id failed for chunk")
            chunk_id: int = row[0]
            return chunk_id

    @serialize_write
    async def insert_chunks_bulk(self, chunks: list[dict[str, Any]]) -> list[int]:
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
                "text_preview": zlib.compress(c["text_preview"].encode("utf-8"))
                if isinstance(c["text_preview"], str)
                else c["text_preview"],
                "sentence_offsets": c.get("sentence_offsets"),
                "segmenter_version": c.get("segmenter_version"),
            }
            for c in chunks
        ]

        # For small batches, the per-row RETURNING approach is fine
        if len(insert_data) <= 20:
            ids: list[int] = []
            for chunk in insert_data:
                async with conn.execute(
                    "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview, sentence_offsets, segmenter_version) "  # noqa: E501
                    "VALUES (:file_id, :start_offset, :end_offset, :text_preview, :sentence_offsets, :segmenter_version) RETURNING id;",  # noqa: E501
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
        # Use savepoint to handle case where transaction already exists
        savepoint_name = None
        try:
            # Try explicit transaction; if already in one, use savepoint
            try:
                await conn.execute("BEGIN IMMEDIATE")
            except Exception as e:
                if "cannot start a transaction within a transaction" in str(e):
                    savepoint_name = f"sp_{uuid.uuid4().hex}"
                    await conn.execute(f"SAVEPOINT {savepoint_name}")
                else:
                    raise

            async with conn.execute("SELECT COALESCE(MAX(id), 0) FROM chunks") as cur:
                row = await cur.fetchone()
                start_id = (row[0] if row else 0) + 1

            # Prevent SQLITE_MAX_VARIABLE_NUMBER crashes by slicing insert_data
            max_rows_per_query = 5000
            for i in range(0, len(insert_data), max_rows_per_query):
                batch = insert_data[i : i + max_rows_per_query]
                await conn.executemany(
                    "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview, sentence_offsets, segmenter_version) "  # noqa: E501
                    "VALUES (:file_id, :start_offset, :end_offset, :text_preview, :sentence_offsets, :segmenter_version);",  # noqa: E501
                    batch,
                )

            # Read back the generated IDs (they are sequential in SQLite)
            async with conn.execute(
                "SELECT id FROM chunks WHERE id >= ? ORDER BY id", (start_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                ids = [r[0] for r in rows]

            # Commit or release savepoint
            if savepoint_name:
                await conn.execute(f"RELEASE {savepoint_name}")
            else:
                await conn.commit()
            return ids
        except Exception:
            # Rollback or rollback to savepoint
            if savepoint_name:
                with contextlib.suppress(Exception):
                    await conn.execute(f"ROLLBACK TO {savepoint_name}")
            else:
                with contextlib.suppress(Exception):
                    await conn.rollback()
            raise

    @serialize_write
    async def insert_chunk_embeddings_bulk(self, data: list[tuple[int, bytes]]) -> None:
        """Insert multiple chunk embeddings in a single transaction."""
        if not data:
            return
        conn = self._get_conn()
        await conn.executemany(
            "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?) "
            "ON CONFLICT(chunk_id) DO UPDATE SET embedding=excluded.embedding",
            data,
        )
        await conn.commit()

    @serialize_write
    async def insert_kg_nodes_bulk(self, data: list[tuple[str, str, str, str, int | None]]) -> None:
        """Insert multiple kg_nodes efficiently.
        data format: list of (id, type, label, properties, chunk_id)
        """
        if not data:
            return
        conn = self._get_conn()
        await conn.executemany(
            "INSERT INTO kg_nodes (id, type, label, properties, chunk_id) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET type=excluded.type, label=excluded.label, properties=excluded.properties, chunk_id=excluded.chunk_id",  # noqa: E501
            data,
        )
        await conn.commit()

    @serialize_write
    async def insert_kg_edges_bulk(self, data: list[tuple[str, str, str, float, str]]) -> None:
        """Insert multiple kg_edges efficiently.
        data format: list of (source, target, relation, weight, properties)
        """
        if not data:
            return
        conn = self._get_conn()
        await conn.execute("PRAGMA foreign_keys = OFF")
        try:
            await conn.executemany(
                "INSERT INTO kg_edges (source, target, relation, weight, properties) VALUES (?, ?, ?, ?, ?) "  # noqa: E501
                "ON CONFLICT(source, target, relation) DO UPDATE SET weight=excluded.weight, properties=excluded.properties",  # noqa: E501
                data,
            )
            await conn.commit()
        finally:
            await conn.execute("PRAGMA foreign_keys = ON")

    @serialize_write
    async def resolve_pending_graph_edges(self) -> None:
        """Resolve PENDING:: edges to actual node IDs based on their name."""
        conn = self._get_conn()

        # 1. Update edges where we can find a matching node
        await conn.execute(
            """
            UPDATE kg_edges
            SET target = (
                SELECT id FROM kg_nodes
                WHERE kg_nodes.label = substr(kg_edges.target, 10)
                LIMIT 1
            )
            WHERE target LIKE 'PENDING::%'
            AND EXISTS (
                SELECT 1 FROM kg_nodes
                WHERE kg_nodes.label = substr(kg_edges.target, 10)
            )
            """
        )

        # 2. Delete unresolved edges to keep the graph clean
        await conn.execute("DELETE FROM kg_edges WHERE target LIKE 'PENDING::%'")

        await conn.commit()

    async def get_chunk_embeddings(self, chunk_ids: list[int]) -> dict[int, bytes]:
        """Fetch embeddings for a list of chunk IDs."""
        if not chunk_ids:
            return {}
        result = {}
        async with self._get_read_conn() as conn:
            batch_size = 900
            for i in range(0, len(chunk_ids), batch_size):
                batch = chunk_ids[i : i + batch_size]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    "SELECT chunk_id, embedding FROM chunk_embeddings "  # noqa: S608
                    f"WHERE chunk_id IN ({placeholders})"
                )
                async with conn.execute(query, batch) as cursor:
                    async for row in cursor:
                        result[row[0]] = row[1]
        return result

    async def get_all_chunk_data_for_sync(
        self, limit: int = 5000, last_id: int = 0
    ) -> list[dict[str, Any]]:
        """Fetch a batch of chunk data required to rebuild LanceDB vector cache."""
        query = """
            SELECT ce.chunk_id, ce.embedding, f.path as file_path, f.folder_tag
            FROM chunk_embeddings ce
            JOIN chunks c ON ce.chunk_id = c.id
            JOIN files f ON c.file_id = f.id
            WHERE ce.chunk_id > ?
            ORDER BY ce.chunk_id ASC
            LIMIT ?
        """
        async with self._get_read_conn() as conn:  # noqa: SIM117
            async with conn.execute(query, (last_id, limit)) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "chunk_id": str(r[0]),
                        "embedding": r[1],
                        "file_path": r[2],
                        "folder_tag": r[3],
                    }
                    for r in rows
                ]

    async def bfs_from_chunks(
        self, chunk_ids: list[int], max_depth: int = 3, limit: int = 5
    ) -> list[int]:
        """Perform BFS to find related chunk_ids starting from a set of seed chunk_ids."""
        if not chunk_ids:
            return []

        placeholders = ",".join("?" for _ in chunk_ids)
        # We query for edges traversed from the starting nodes
        query = f"""
        WITH RECURSIVE
        bfs_nodes(id, depth) AS (
            SELECT id, 0
            FROM kg_nodes
            WHERE json_extract(properties, '$.chunk_id') IN ({placeholders})

            UNION ALL

            SELECT e.target, b.depth + 1
            FROM kg_edges e
            JOIN bfs_nodes b ON e.source = b.id
            WHERE b.depth < ?

            UNION ALL

            SELECT e.source, b.depth + 1
            FROM kg_edges e
            JOIN bfs_nodes b ON e.target = b.id
            WHERE b.depth < ?
        )
        SELECT DISTINCT CAST(json_extract(n.properties, '$.chunk_id') AS INTEGER) as chunk_id
        FROM bfs_nodes b
        JOIN kg_nodes n ON b.id = n.id
        WHERE json_extract(n.properties, '$.chunk_id') IS NOT NULL
        LIMIT ?
        """  # noqa: S608
        params = [*chunk_ids, max_depth, max_depth, limit]

        async with self._get_read_conn() as conn, conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows if r[0] is not None]

    async def get_relational_paths(
        self, src_chunk_ids: list[int], max_depth: int = 3, limit: int = 5
    ) -> list[str]:
        """Extract path strings starting from source chunks for LLM context."""
        if not src_chunk_ids:
            return []

        placeholders = ",".join("?" for _ in src_chunk_ids)
        query = f"""
        WITH RECURSIVE
        paths(id, path_str, depth) AS (
            SELECT id, label || ' ' || id, 0
            FROM kg_nodes
            WHERE json_extract(properties, '$.chunk_id') IN ({placeholders})

            UNION ALL

            SELECT e.target, p.path_str || ' -[' || e.relation || ']-> ' || (SELECT label || ' ' || id FROM kg_nodes WHERE id = e.target), p.depth + 1
            FROM kg_edges e
            JOIN paths p ON e.source = p.id
            WHERE p.depth < ?
        )
        SELECT path_str FROM paths
        WHERE depth > 0
        LIMIT ?
        """  # noqa: E501, S608
        params = [*src_chunk_ids, max_depth, limit]

        async with self._get_read_conn() as conn, conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    @serialize_write
    async def commit(self) -> None:
        """Explicitly commits the current transaction."""
        if self.conn:
            await self.conn.commit()

    @serialize_write
    async def delete_file_chunks(self, file_id: int, *, auto_commit: bool = True) -> None:
        """Deletes all chunks associated with a file.

        Set ``auto_commit=False`` when called from a larger batch transaction.
        """
        conn = self._get_conn()
        await conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        if auto_commit:
            await conn.commit()

    async def get_file_chunks(self, file_id: int) -> list[aiosqlite.Row]:
        """Returns all chunks for a given file id, decompressing text_preview."""
        async with (
            self._get_read_conn() as conn,
            conn.execute(
                "SELECT id, file_id, start_offset, end_offset, created_at, "
                "zlib_decompress(text_preview) as text_preview FROM chunks WHERE file_id = ?",
                (file_id,),
            ) as cursor,
        ):
            return list(await cursor.fetchall())

    async def get_file_by_path(self, path: str) -> aiosqlite.Row | None:
        """Returns file metadata by path."""
        async with self._get_read_conn() as conn:  # noqa: SIM117
            async with conn.execute("SELECT * FROM files WHERE path = ?", (path,)) as cursor:
                return await cursor.fetchone()  # type: ignore

    async def get_existing_file_ids(self, paths: list[str]) -> dict[str, int]:
        """Return {path: file_id} for every path that already exists in the DB.

        Used by the indexing pipeline to avoid per-file existence lookups.
        """
        result: dict[str, int] = {}
        async with self._get_read_conn() as conn:
            batch_size = 900
            for i in range(0, len(paths), batch_size):
                batch = paths[i : i + batch_size]
                placeholders = ",".join("?" for _ in batch)
                query = f"SELECT path, id FROM files WHERE path IN ({placeholders})"  # noqa: S608
                async with conn.execute(query, batch) as cursor:
                    async for row in cursor:
                        result[row[0]] = row[1]
        return result

    async def get_files_modified_map(self, paths: list[str]) -> dict[str, str]:
        """Return {path: modified_at} for every path that already exists in the DB."""
        result: dict[str, str] = {}
        async with self._get_read_conn() as conn:
            batch_size = 900
            for i in range(0, len(paths), batch_size):
                batch = paths[i : i + batch_size]
                placeholders = ",".join("?" for _ in batch)
                query = f"SELECT path, modified_at FROM files WHERE path IN ({placeholders})"  # noqa: S608
                async with conn.execute(query, batch) as cursor:
                    async for row in cursor:
                        result[row[0]] = row[1]
        return result

    async def get_files_sha256_map(self, paths: list[str]) -> dict[str, str]:
        """Return {path: sha256} for every path that already exists in the DB."""
        result: dict[str, str] = {}
        async with self._get_read_conn() as conn:
            batch_size = 900
            for i in range(0, len(paths), batch_size):
                batch = paths[i : i + batch_size]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    f"SELECT path, COALESCE(sha256, '') FROM files WHERE path IN ({placeholders})"  # noqa: S608
                )
                async with conn.execute(query, batch) as cursor:
                    async for row in cursor:
                        result[row[0]] = row[1]
        return result

    async def get_files_change_map(self, paths: list[str]) -> dict[str, tuple[str, str]]:
        """Return {path: (modified_at, sha256)} in a SINGLE query.

        Replaces separate calls to get_files_modified_map + get_files_sha256_map
        to halve the number of DB round-trips during change detection.
        """
        result: dict[str, tuple[str, str]] = {}
        async with self._get_read_conn() as conn:
            batch_size = 900
            for i in range(0, len(paths), batch_size):
                batch = paths[i : i + batch_size]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    f"SELECT path, modified_at, COALESCE(sha256, '') FROM files "  # noqa: S608
                    f"WHERE path IN ({placeholders})"
                )
                async with conn.execute(query, batch) as cursor:
                    async for row in cursor:
                        result[row[0]] = (row[1], row[2])
        return result

    @serialize_write
    async def increment_usage_count(self, file_path: str) -> None:
        """Increments the usage_count for a given file path."""
        conn = self._get_conn()
        await conn.execute(
            "UPDATE files SET usage_count = usage_count + 1 WHERE path = ?",
            (file_path,),
        )
        await conn.commit()

    @serialize_write
    async def batch_increment_usage(self, file_paths: list[str]) -> None:
        """Increment usage_count for multiple file paths in a single transaction."""
        if not file_paths:
            return
        conn = self._get_conn()
        counts: dict[str, int] = {}
        for path in file_paths:
            counts[path] = counts.get(path, 0) + 1

        when_clauses = []
        case_params: list[Any] = []
        for path, increment in counts.items():
            when_clauses.append("WHEN ? THEN usage_count + ?")
            case_params.extend([path, increment])

        in_params = list(counts.keys())
        placeholders = ",".join("?" for _ in in_params)
        sql = (
            "UPDATE files SET usage_count = CASE path "  # noqa: S608
            + " ".join(when_clauses)
            + " ELSE usage_count END WHERE path IN ("
            + placeholders
            + ")"
        )
        await conn.execute(sql, tuple(case_params + in_params))
        await conn.commit()

    async def get_all_files(self) -> list[aiosqlite.Row]:
        """Returns all indexed files ordered by folder and path."""
        async with (
            self._get_read_conn() as conn,
            conn.execute(
                "SELECT id, path, size, type, folder_tag, usage_count FROM files ORDER BY folder_tag, path"  # noqa: E501
            ) as cursor,
        ):
            return list(await cursor.fetchall())

    async def stream_all_nodes(self):
        """Asynchronous generator to yield all folders and files for scalable visualization."""
        async with self._get_read_conn() as conn:
            # First stream all folder profiles
            async with conn.execute(
                "SELECT folder_path, project_type, file_count, total_size_bytes FROM folder_profiles"  # noqa: E501
            ) as cursor:
                async for row in cursor:
                    yield {
                        "is_folder": True,
                        "path": row["folder_path"],
                        "project_type": row["project_type"],
                        "file_count": row["file_count"],
                        "size": row["total_size_bytes"],
                    }

            # Then stream all files
            async with conn.execute("SELECT path, size, type, folder_tag FROM files") as cursor:
                async for row in cursor:
                    yield {
                        "is_folder": False,
                        "path": row["path"],
                        "size": row["size"],
                        "type": row["type"],
                        "folder_tag": row["folder_tag"],
                    }

    async def get_file_stats_summary(self) -> dict[str, Any]:
        """Return aggregate file statistics grouped by type and folder_tag.

        Uses a single-pass CTE to avoid scanning the files table twice.
        """
        async with self._get_read_conn() as conn:
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

        type_rows = [(r[1], r[2], r[3]) for r in rows if r[0] == "T"]
        folder_rows = [(r[1], r[2]) for r in rows if r[0] == "F"]

        total_files = sum(r[1] for r in type_rows)
        total_bytes = sum(r[2] or 0 for r in type_rows)

        return {
            "total_files": total_files,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "by_type": [
                {"ext": r[0], "count": r[1], "size_mb": round((r[2] or 0) / (1024 * 1024), 2)}
                for r in type_rows
            ],
            "by_folder": [{"folder": r[0] or "Unknown", "count": r[1]} for r in folder_rows],
            "database_size_bytes": os.path.getsize(self.db_path)
            if os.path.exists(self.db_path)
            else 0,
        }

    async def get_counts(self) -> tuple[int, int]:
        """Return (file_count, chunk_count) in a single public call."""
        async with (
            self._get_read_conn() as conn,
            conn.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM files) AS file_count, "
                "(SELECT COUNT(*) FROM chunks) AS chunk_count"
            ) as cursor,
        ):
            row = await cursor.fetchone()
            if not row:
                return 0, 0
            return row[0], row[1]

    async def execute_query(self, sql: str, params: tuple = ()) -> list[Any]:
        """Execute a read-only SQL query via the read-pool and return all rows."""
        async with self._get_read_conn() as conn, conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    @serialize_write
    async def execute_write(self, sql: str, params: tuple = ()) -> None:
        """Execute a write SQL statement via the write connection and commit."""
        conn = self._get_conn()
        await conn.execute(sql, params)
        await conn.commit()

    @serialize_write
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

    @serialize_write
    async def save_telemetry(
        self,
        query_id: int | None,
        time_to_first_token_ms: float,
        mode_selected: str | None,
        model_class: str | None,
        context_tokens_budget: int | None,
        context_tokens_used: int | None,
        chunks_included: int | None,
        chunks_dropped: int | None,
        response_abandoned: bool = False,
        query_retry_within_60s: bool = False,
        deep_analysis_toggled: bool = False,
        force_include_count: int = 0,
        feature_thumbs: str | None = None,
    ) -> None:
        """Save local-only telemetry for Rich Output tracking."""
        conn = self._get_conn()
        await conn.execute(
            """
            INSERT INTO pma_metrics (
                query_id, time_to_first_token_ms, response_abandoned, query_retry_within_60s,
                deep_analysis_toggled, mode_selected, force_include_count, feature_thumbs,
                model_class, context_tokens_budget, context_tokens_used, chunks_included, chunks_dropped
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,  # noqa: E501
            (
                query_id,
                time_to_first_token_ms,
                response_abandoned,
                query_retry_within_60s,
                deep_analysis_toggled,
                mode_selected,
                force_include_count,
                feature_thumbs,
                model_class,
                context_tokens_budget,
                context_tokens_used,
                chunks_included,
                chunks_dropped,
            ),
        )
        await conn.commit()

    async def get_query_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent queries from history."""
        async with (
            self._get_read_conn() as conn,
            conn.execute(
                "SELECT id, question, answer, source_count, latency_ms, created_at "
                "FROM query_history ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ) as cursor,
        ):
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

    @serialize_write
    async def clear_query_history(self) -> dict[str, str]:
        """Delete all entries from the query_history table."""
        conn = self._get_conn()
        await conn.execute("DELETE FROM query_history")
        await conn.commit()
        return {"message": "Query history cleared successfully."}

    @serialize_write
    async def cleanup_stale_files(self) -> list[str]:
        """Remove index entries for files that no longer exist on disk.

        Returns list of paths that were cleaned up.
        """
        cleaned: list[str] = []
        stale_ids: list[int] = []
        async with self._get_read_conn() as conn:  # noqa: SIM117
            async with conn.execute("SELECT id, path FROM files") as cursor:
                rows = list(await cursor.fetchall())
        for row in rows:
            file_id, path = row[0], row[1]
            if not os.path.exists(path):
                stale_ids.append(file_id)
                cleaned.append(path)
                logger.info("Cleaned stale file: %s", path)
        if stale_ids:
            conn = self._get_conn()
            savepoint_name = None
            try:
                try:
                    await conn.execute("BEGIN IMMEDIATE")
                except Exception as e:
                    if "cannot start a transaction within a transaction" in str(e):
                        savepoint_name = f"sp_{uuid.uuid4().hex}"
                        await conn.execute(f"SAVEPOINT {savepoint_name}")
                    else:
                        raise

                batch_size = 900
                for i in range(0, len(stale_ids), batch_size):
                    batch = stale_ids[i : i + batch_size]
                    placeholders = ",".join("?" for _ in batch)
                    await conn.execute(
                        f"DELETE FROM files WHERE id IN ({placeholders})",  # noqa: S608
                        tuple(batch),
                    )

                if savepoint_name:
                    await conn.execute(f"RELEASE {savepoint_name}")
                else:
                    await conn.commit()
            except Exception:
                if savepoint_name:
                    with contextlib.suppress(Exception):
                        await conn.execute(f"ROLLBACK TO {savepoint_name}")
                else:
                    with contextlib.suppress(Exception):
                        await conn.rollback()
                raise
        return cleaned

    @serialize_write
    async def clear_all(self) -> dict[str, int]:
        """Delete ALL indexed data: files, chunks, FTS, and query history.

        Returns counts of removed files and chunks.
        """
        async with self._get_read_conn() as read_pool_conn:
            cur = await read_pool_conn.execute("SELECT COUNT(*) FROM files")
            row = await cur.fetchone()
            files_count = row[0] if row else 0
            await cur.close()

            cur = await read_pool_conn.execute("SELECT COUNT(*) FROM chunks")
            row = await cur.fetchone()
            chunks_count = row[0] if row else 0
            await cur.close()

        conn = self._get_conn()
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
            DROP TABLE IF EXISTS unreal_project_facts;

            -- Recreate FTS table with optimized detail=column schema and contentless mode.
            -- text_preview is stored zlib-compressed so triggers decompress on the fly.
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                chunks_text, content='', detail=column
            );
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
              INSERT INTO chunk_fts(rowid, chunks_text)
              VALUES (new.id, zlib_decompress(new.text_preview));
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
              INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text)
              VALUES('delete', old.id, zlib_decompress(old.text_preview));
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
              INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text)
              VALUES('delete', old.id, zlib_decompress(old.text_preview));
              INSERT INTO chunk_fts(rowid, chunks_text)
              VALUES (new.id, zlib_decompress(new.text_preview));
            END;
        """)

        logger.info("Cleared all data: %d files, %d chunks", files_count, chunks_count)
        return {"files_removed": files_count, "chunks_removed": chunks_count}

    async def get_files_by_filter(
        self,
        file_type: str | None = None,
        folder_tag: str | None = None,
    ) -> list[aiosqlite.Row]:
        """Return files matching optional type/folder filters."""
        conditions: list[str] = []
        params: list[Any] = []
        if file_type:
            conditions.append("type = ?")
            params.append(file_type)
        if folder_tag:
            conditions.append("folder_tag = ?")
            params.append(folder_tag)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = (
            "SELECT path, size, type, folder_tag, usage_count "  # noqa: S608
            f"FROM files{where} ORDER BY path"
        )
        async with self._get_read_conn() as conn, conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    @serialize_write
    async def delete_files_by_folder_prefix(self, folder: str) -> None:
        """Delete all files (and cascading chunks) whose path starts with *folder*."""
        conn = self._get_conn()
        await conn.execute(
            "DELETE FROM files WHERE path LIKE ? || '%'",
            (folder,),
        )
        await conn.commit()

    async def is_healthy(self) -> bool:
        """Quick DB health check - runs a trivial query."""
        if self._write_conn is None:
            return False
        try:
            async with self._get_read_conn() as conn:  # noqa: SIM117
                async with conn.execute("SELECT 1") as cursor:
                    row = await cursor.fetchone()
                    return row is not None and row[0] == 1
        except Exception:
            return False

    async def get_all_summaries(self) -> list[dict[str, Any]]:
        """Return (id, path, summary) for every file that has a non-empty summary."""
        async with (
            self._get_read_conn() as conn,
            conn.execute(
                "SELECT id, path, summary FROM files WHERE summary != '' ORDER BY id"
            ) as cursor,
        ):
            rows = await cursor.fetchall()
            return [{"id": r[0], "path": r[1], "summary": r[2]} for r in rows]

    # ── Folder profiles ───────────────────────────────────────────────

    @serialize_write
    async def upsert_folder_profile(
        self, profile: dict[str, Any], *, auto_commit: bool = True
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

    async def get_all_folder_profiles(self) -> list[dict[str, Any]]:
        """Return every stored folder profile."""
        async with (
            self._get_read_conn() as conn,
            conn.execute(
                "SELECT folder_path, folder_tag, profile_text, project_type, "
                "file_count, total_size_bytes, top_extensions, key_files "
                "FROM folder_profiles ORDER BY folder_path"
            ) as cursor,
        ):
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

    async def get_graph_edges(self, node_id: str, max_depth: int = 2) -> list[dict[str, Any]]:
        """Retrieve 1-hop and 2-hop edges for a given node using recursive CTE."""
        query = """
            WITH RECURSIVE
                connected_nodes(id, depth) AS (
                    SELECT ? AS id, 0 AS depth
                    UNION ALL
                    SELECT CASE
                        WHEN ke.source = cn.id THEN ke.target
                        ELSE ke.source
                    END AS id, cn.depth + 1 AS depth
                    FROM kg_edges ke
                    JOIN connected_nodes cn ON ke.source = cn.id OR ke.target = cn.id
                    WHERE cn.depth < ?
                )
            SELECT DISTINCT e.source, e.target, e.relation, e.weight, e.properties
            FROM kg_edges e
            JOIN connected_nodes n1 ON e.source = n1.id
            JOIN connected_nodes n2 ON e.target = n2.id
        """
        async with self._get_read_conn() as conn:  # noqa: SIM117
            async with conn.execute(query, (node_id, max_depth)) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "source": r[0],
                        "target": r[1],
                        "relation": r[2],
                        "weight": r[3],
                        "properties": r[4],
                    }
                    for r in rows
                ]

    async def get_graph_nodes(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """Retrieve node details for a list of node IDs."""
        if not node_ids:
            return []

        result = []
        async with self._get_read_conn() as conn:
            batch_size = 900
            for i in range(0, len(node_ids), batch_size):
                batch = node_ids[i : i + batch_size]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    f"SELECT id, type, label, properties FROM kg_nodes WHERE id IN ({placeholders})"  # noqa: S608
                )
                async with conn.execute(query, batch) as cursor:
                    rows = await cursor.fetchall()
                    result.extend(
                        [
                            {
                                "id": r[0],
                                "type": r[1],
                                "label": r[2],
                                "properties": r[3],
                            }
                            for r in rows
                        ]
                    )
        return result
