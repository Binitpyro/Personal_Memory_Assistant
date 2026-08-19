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
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


def _zlib_decompress_fn(blob: Any) -> str:
    """Safe SQLite function to decompress zlib blobs, falling back to string if uncompressed."""
    if not blob:
        return ""
    if isinstance(blob, str):
        # TODO(post-P0-2): app/api/modules.py was the only production writer
        # of uncompressed str into text_preview, and it's gone (see the
        # license-boundary strip). This passthrough exists for rows already
        # written before that fix, plus test fixtures. Confirm no legacy
        # rows remain before removing it - until then it's load-bearing,
        # not dead code.
        return blob
    try:
        return zlib.decompress(blob).decode("utf-8")
    except Exception as e:
        # P1-2: this used to `return str(blob)` - the Python repr of the raw
        # bytes (e.g. "b'x\\x9c...'") - which then got indexed into FTS as
        # if it were real text, indistinguishable from genuine content.
        # A blob that fails to decompress is corrupt; surface that instead
        # of silently fabricating searchable garbage from it.
        logger.error(
            "zlib_decompress failed on a %d-byte blob (SQLite scalar function receives "
            "no chunk id): %s",
            len(blob) if hasattr(blob, "__len__") else -1,
            e,
        )
        return ""


FTS_TABLE_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    chunks_text, content='', tokenize='trigram', detail=full
);
"""

FTS_TRIGGERS_DDL = """
CREATE TRIGGER IF NOT EXISTS chunk_fts_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunk_fts(rowid, chunks_text)
  VALUES (new.id, zlib_decompress(new.text_preview));
END;
CREATE TRIGGER IF NOT EXISTS chunk_fts_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text)
  VALUES('delete', old.id, zlib_decompress(old.text_preview));
END;
CREATE TRIGGER IF NOT EXISTS chunk_fts_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text)
  VALUES('delete', old.id, zlib_decompress(old.text_preview));
  INSERT INTO chunk_fts(rowid, chunks_text)
  VALUES (new.id, zlib_decompress(new.text_preview));
END;
"""

FTS_DROP_TRIGGERS_DDL = """
DROP TRIGGER IF EXISTS chunk_fts_ai;
DROP TRIGGER IF EXISTS chunk_fts_ad;
DROP TRIGGER IF EXISTS chunk_fts_au;
DROP TRIGGER IF EXISTS chunks_ai;
DROP TRIGGER IF EXISTS chunks_ad;
DROP TRIGGER IF EXISTS chunks_au;
"""


import functools  # noqa: E402


def serialize_write(func):
    @functools.wraps(func)
    async def wrapper(self: "DatabaseManager", *args, **kwargs):
        async with self._write_lock:
            return await func(self, *args, **kwargs)

    return wrapper


# Module-level so a test can shrink it. Generous rather than tight: legitimate
# contention on a pool_size=4 pool during a bulk ingest is normal, and this is
# meant to catch a leak, not to police slow queries.
_READ_ACQUIRE_TIMEOUT_S = 30.0


class ReadPoolExhaustedError(RuntimeError):
    """No read connection became available within `_READ_ACQUIRE_TIMEOUT_S`.

    Almost always a leaked borrow rather than real contention: the pool is
    returned to in a `finally`, so a missing connection means some path exited
    without running it.
    """


class DatabaseManager:
    """Manages the SQLite database connection and operations with a read-connection pool."""

    def __init__(self, db_path: str = "pma_metadata.db", pool_size: int = 4):
        """Initializes the DatabaseManager."""
        self.db_path = db_path
        self.pool_size = pool_size
        self._write_conn: aiosqlite.Connection | None = None
        self._read_pool: asyncio.Queue[aiosqlite.Connection] | None = None
        # Every read connection ever opened, queued or borrowed. close() walked
        # the queue alone, so a leaked borrow kept its SQLite handle open past
        # shutdown and left the database file locked - which is how a survey run
        # left behind an eval.db that shutil.rmtree could not remove.
        self._read_conns: list[aiosqlite.Connection] = []
        self.conn_factory: Callable[[], aiosqlite.Connection] | None = None
        self._in_ingest_mode = False
        self._pool_initialized = False
        self._pool_lock: asyncio.Lock | None = None
        self._write_lock = asyncio.Lock()
        self._in_external_transaction = False

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
            await self._configure_conn(self._write_conn, is_write_conn=True)

            # 2. Read connection pool
            self._read_conns = []
            for _ in range(self.pool_size):
                conn = await aiosqlite.connect(self.db_path, isolation_level=None)
                await self._configure_conn(conn, is_write_conn=False)
                self._read_conns.append(conn)
                await self._read_pool.put(conn)

            self._pool_initialized = True

    async def _configure_conn(
        self, conn: aiosqlite.Connection, is_write_conn: bool = False
    ) -> None:
        """Apply performance pragmas and custom functions to a connection."""
        conn.row_factory = aiosqlite.Row
        await conn.create_function("zlib_decompress", 1, _zlib_decompress_fn)

        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.execute("PRAGMA busy_timeout = 5000;")

        if is_write_conn:
            await conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")
            await conn.execute("PRAGMA journal_mode = WAL;")
            await conn.execute("PRAGMA synchronous = NORMAL;")

        # ── Performance PRAGMAs ──────────────────────────────────
        await conn.execute("PRAGMA cache_size = -8192;")  # 8 MB page cache
        await conn.execute("PRAGMA mmap_size = 1073741824;")  # 1 GB memory-mapped I/O
        await conn.execute("PRAGMA temp_store = MEMORY;")  # temp tables in RAM
        # NOTE: page_size only affects new databases. Existing ones ignore this until VACUUM.
        await conn.execute("PRAGMA page_size = 32768;")
        await conn.execute("PRAGMA threads = 4;")
        # NOTE: read_uncommitted only has an effect in shared-cache mode (aiosqlite uses private).
        await conn.execute("PRAGMA read_uncommitted = ON;")

        if is_write_conn:
            await conn.execute("PRAGMA wal_autocheckpoint = 10000;")

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

        if self._read_pool is None:
            raise RuntimeError("Database read pool is not initialized")

        # Bounded on purpose. `await queue.get()` on an empty pool parks at zero
        # CPU with no timeout, so a leaked borrow turns every later reader into
        # a silent, permanent hang. Raising instead makes starvation diagnosable
        # the first time it happens rather than after a 61-minute stall.
        try:
            conn = await asyncio.wait_for(self._read_pool.get(), timeout=_READ_ACQUIRE_TIMEOUT_S)
        except TimeoutError as exc:
            raise ReadPoolExhaustedError(
                f"No read connection available after {_READ_ACQUIRE_TIMEOUT_S}s "
                f"(pool_size={self.pool_size}). A borrow was most likely leaked."
            ) from exc

        try:
            yield conn
        finally:
            # The return is unconditional and awaits nothing. `rollback()` used
            # to sit directly in this `finally` under
            # `contextlib.suppress(Exception)`; CancelledError is a
            # BaseException, so a cancellation delivered during the rollback
            # escaped the suppression, skipped the put, and lost the connection
            # for the life of the process. put_nowait cannot raise here either -
            # the queue is unbounded - so there is no await left on this path
            # for a cancellation to land on.
            try:
                with contextlib.suppress(Exception):
                    await conn.rollback()
            finally:
                self._read_pool.put_nowait(conn)

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
                    self._read_pool.get_nowait()

            # Close by ledger, not by queue: a connection borrowed and never
            # returned is absent from the queue but still holds a file handle.
            for conn in self._read_conns:
                with contextlib.suppress(Exception):
                    await conn.close()
            self._read_conns = []

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
            # Provenance for OCR: lets a re-OCR replace only the chunks the OCR
            # worker produced, leaving native text in a mixed PDF intact.
            ("chunks_source", "ALTER TABLE chunks ADD COLUMN source TEXT"),
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

        # Create system_state table if not exists
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await conn.commit()

        # Bug 5 (recreate FTS table and triggers if aborted bulk ingestion)
        try:
            async with conn.execute(
                "SELECT value FROM system_state WHERE key = 'fts_dirty'"
            ) as cur:
                row = await cur.fetchone()
                if row and row[0] == "1":
                    logger.warning("FTS table left dirty from previous crash, rebuilding...")
                    query = f"""
                        DROP TABLE IF EXISTS chunk_fts;
                        {FTS_TABLE_DDL}
                        INSERT INTO chunk_fts(rowid, chunks_text)
                        SELECT id, zlib_decompress(text_preview) FROM chunks;
                        {FTS_TRIGGERS_DDL}
                    """  # nosec B608 # noqa: S608
                    await conn.executescript(query)
                    await conn.execute(
                        "INSERT OR REPLACE INTO system_state (key, value) VALUES ('fts_dirty', '0')"
                    )
                    await conn.commit()
        except Exception as exc:
            logger.debug("Failed to recover FTS from dirty state: %s", exc)

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

        # OCR work queue (deferred: scanned pages are enqueued during indexing
        # and drained afterwards, so a slow engine never stalls extraction).
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ocr_queue (
                    file_path   TEXT PRIMARY KEY,
                    pages_json  TEXT NOT NULL DEFAULT '[]',
                    page_count  INTEGER NOT NULL DEFAULT 0,
                    pages_done  INTEGER NOT NULL DEFAULT 0,
                    tier        TEXT NOT NULL DEFAULT 'cpu',
                    status      TEXT NOT NULL DEFAULT 'pending',
                    force_ocr   INTEGER NOT NULL DEFAULT 0,
                    attempts    INTEGER NOT NULL DEFAULT 0,
                    last_error  TEXT NOT NULL DEFAULT '',
                    enqueued_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ocr_queue_status ON ocr_queue(status, enqueued_at)"
            )
            await conn.commit()
            logger.debug("ocr_queue table ensured.")
        except Exception as exc:
            logger.debug("ocr_queue migration note: %s", exc)

        # OCR page cache, keyed on content hash so it outlives both the file
        # row and a full index reset. See clear_all() for why it is excluded
        # from the broad wipe.
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ocr_cache (
                    content_key   TEXT NOT NULL,
                    page_num      INTEGER NOT NULL,
                    model_version TEXT NOT NULL,
                    preproc_hash  TEXT NOT NULL,
                    text          TEXT NOT NULL DEFAULT '',
                    mean_conf     REAL NOT NULL DEFAULT 0.0,
                    bytes         INTEGER NOT NULL DEFAULT 0,
                    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                    last_used_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (content_key, page_num, model_version, preproc_hash)
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ocr_cache_lru ON ocr_cache(last_used_at)"
            )
            await conn.commit()
            logger.debug("ocr_cache table ensured.")
        except Exception as exc:
            logger.debug("ocr_cache migration note: %s", exc)

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
                query = f"""
                    {FTS_DROP_TRIGGERS_DDL}
                    DROP TABLE IF EXISTS chunk_fts;
                    {FTS_TABLE_DDL}
                    {FTS_TRIGGERS_DDL}
                    INSERT INTO chunk_fts(rowid, chunks_text)
                    SELECT id, zlib_decompress(text_preview) FROM chunks;
                """  # nosec B608 # noqa: S608
                await conn.executescript(query)
                await conn.commit()
                logger.info("Storage optimization: Optimized chunk_fts schema.")
        except Exception as exc:
            logger.warning("Failed to rebuild FTS table: %s", exc)

    @serialize_write
    async def enter_ingest_mode(self) -> None:
        """Temporarily drop FTS triggers and track deltas in temp tables for bulk ingestion."""
        conn = self._get_conn()
        await conn.execute(
            "INSERT OR REPLACE INTO system_state (key, value) VALUES ('fts_dirty', '1')"
        )
        await conn.commit()
        await conn.executescript(FTS_DROP_TRIGGERS_DDL)
        await conn.executescript("""
            CREATE TEMP TABLE IF NOT EXISTS temp_ingest_chunk_inserts(id INTEGER PRIMARY KEY);
            CREATE TEMP TABLE IF NOT EXISTS temp_ingest_chunk_deletes(
                id INTEGER PRIMARY KEY, text_preview BLOB
            );
            DELETE FROM temp_ingest_chunk_inserts;
            DELETE FROM temp_ingest_chunk_deletes;
        """)
        self._in_ingest_mode = True
        logger.info("Entered ingest mode (FTS triggers dropped, tracking temp tables ready).")

    @serialize_write
    async def exit_ingest_mode(self) -> None:
        """Apply FTS deltas, cleanup temp tables, and restore FTS triggers."""
        conn = self._get_conn()

        logger.info("Rebuilding FTS delta after bulk ingest...")
        query = f"""
            -- Deletes MUST run before inserts.
            -- If SQLite reuses a rowid within the same ingest session, the new rowid
            -- could theoretically land in both tracking tables. By processing deletes first,
            -- the subsequent insert will correctly overwrite any stale delete state.
            INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text)
            SELECT 'delete', id, zlib_decompress(text_preview) FROM temp_ingest_chunk_deletes;

            INSERT INTO chunk_fts(rowid, chunks_text)
            SELECT id, zlib_decompress(text_preview) FROM chunks
            WHERE id IN (SELECT id FROM temp_ingest_chunk_inserts);

            DROP TABLE IF EXISTS temp_ingest_chunk_inserts;
            DROP TABLE IF EXISTS temp_ingest_chunk_deletes;

            {FTS_TRIGGERS_DDL}
        """  # nosec B608 # noqa: S608
        await conn.executescript(query)
        await conn.execute(
            "INSERT OR REPLACE INTO system_state (key, value) VALUES ('fts_dirty', '0')"
        )
        await conn.commit()
        self._in_ingest_mode = False
        logger.info("Exited ingest mode (FTS delta applied, triggers restored).")

    async def get_system_state(self, key: str) -> str | None:
        """Retrieves a string value from the system_state table."""
        conn = self._get_conn()
        async with conn.execute("SELECT value FROM system_state WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

    @serialize_write
    async def set_system_state(self, key: str, value: str) -> None:
        """Sets a string value in the system_state table."""
        conn = self._get_conn()
        await conn.execute(
            "INSERT OR REPLACE INTO system_state (key, value) VALUES (?, ?)",
            (key, value),
        )
        await conn.commit()

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
        # _get_conn() belongs inside the try: it raises when the manager is
        # closed, and sitting outside it that RuntimeError escaped a function
        # whose every other failure mode is logged and swallowed.
        try:
            conn = self._get_conn()
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
                await self._maybe_commit(conn)
            return file_id

    @serialize_write
    async def batch_insert_files(
        self, files_data: list[dict[str, Any]], auto_commit: bool = True
    ) -> list[int]:
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
            if auto_commit:
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
            if auto_commit:
                if savepoint_name:
                    await conn.execute(f"RELEASE {savepoint_name}")
                else:
                    await self._maybe_commit(conn)
        except Exception:
            # Rollback or rollback to savepoint
            if auto_commit:
                if savepoint_name:
                    with contextlib.suppress(Exception):
                        await conn.execute(f"ROLLBACK TO {savepoint_name}")
                else:
                    with contextlib.suppress(Exception):
                        await conn.rollback()
            raise
        return file_ids

    @serialize_write
    async def insert_chunks_bulk(
        self, chunks: list[dict[str, Any]], auto_commit: bool = True
    ) -> list[int]:
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
                # NULL for natively-extracted text; 'ocr' for worker output.
                "source": c.get("source"),
            }
            for c in chunks
        ]

        # For small batches, the per-row RETURNING approach is fine
        if len(insert_data) <= 20:
            ids: list[int] = []
            for chunk in insert_data:
                async with conn.execute(
                    "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview, sentence_offsets, segmenter_version, source) "
                    "VALUES (:file_id, :start_offset, :end_offset, :text_preview, :sentence_offsets, :segmenter_version, :source) RETURNING id;",
                    chunk,
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        ids.append(row[0])

            if self._in_ingest_mode and ids:
                await conn.executemany(
                    "INSERT OR IGNORE INTO temp_ingest_chunk_inserts(id) VALUES (?)",
                    [(i,) for i in ids],
                )

            if auto_commit:
                await self._maybe_commit(conn)
            return ids

        # For larger batches, use executemany + read back IDs
        # Wrap in an explicit transaction to prevent race conditions
        # with concurrent inserts between MAX(id) and the bulk insert.
        # Use savepoint to handle case where transaction already exists
        savepoint_name = None
        try:
            # Try explicit transaction; if already in one, use savepoint
            if auto_commit:
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
                    "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview, sentence_offsets, segmenter_version, source) "
                    "VALUES (:file_id, :start_offset, :end_offset, :text_preview, :sentence_offsets, :segmenter_version, :source);",
                    batch,
                )

            # Read back the generated IDs (they are sequential in SQLite)
            async with conn.execute(
                "SELECT id FROM chunks WHERE id >= ? ORDER BY id", (start_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                ids = [r[0] for r in rows]

            if self._in_ingest_mode and ids:
                await conn.executemany(
                    "INSERT OR IGNORE INTO temp_ingest_chunk_inserts(id) VALUES (?)",
                    [(i,) for i in ids],
                )

            # Commit or release savepoint
            if auto_commit:
                if savepoint_name:
                    await conn.execute(f"RELEASE {savepoint_name}")
                else:
                    await self._maybe_commit(conn)
            return ids
        except Exception:
            # Rollback or rollback to savepoint
            if auto_commit:
                if savepoint_name:
                    with contextlib.suppress(Exception):
                        await conn.execute(f"ROLLBACK TO {savepoint_name}")
                else:
                    with contextlib.suppress(Exception):
                        await conn.rollback()
            raise

    @serialize_write
    async def insert_chunk_embeddings_bulk(
        self, data: list[tuple[int, bytes]], auto_commit: bool = True
    ) -> None:
        """Insert multiple chunk embeddings in a single transaction."""
        if not data:
            return
        conn = self._get_conn()
        await conn.executemany(
            "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?) "
            "ON CONFLICT(chunk_id) DO UPDATE SET embedding=excluded.embedding",
            data,
        )
        if auto_commit:
            await self._maybe_commit(conn)

    @serialize_write
    async def insert_kg_nodes_bulk(
        self, data: list[tuple[str, str, str, str, int | None]], auto_commit: bool = True
    ) -> None:
        """Insert multiple kg_nodes efficiently.
        data format: list of (id, type, label, properties, chunk_id)
        """
        if not data:
            return
        conn = self._get_conn()
        await conn.executemany(
            "INSERT INTO kg_nodes (id, type, label, properties, chunk_id) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET type=excluded.type, label=excluded.label, properties=excluded.properties, chunk_id=excluded.chunk_id",
            data,
        )
        if auto_commit:
            await self._maybe_commit(conn)

    @serialize_write
    async def insert_kg_edges_bulk(
        self, data: list[tuple[str, str, str, float, str]], auto_commit: bool = True
    ) -> None:
        """Insert multiple kg_edges efficiently.
        data format: list of (source, target, relation, weight, properties)
        """
        if not data:
            return
        conn = self._get_conn()
        await conn.execute("PRAGMA foreign_keys = OFF")
        try:
            await conn.executemany(
                "INSERT INTO kg_edges (source, target, relation, weight, properties) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(source, target, relation) DO UPDATE SET weight=excluded.weight, properties=excluded.properties",
                data,
            )
            if auto_commit:
                await self._maybe_commit(conn)
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

        await self._maybe_commit(conn)

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
                    "SELECT chunk_id, embedding FROM chunk_embeddings "  # nosec B608 # noqa: S608
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

    async def get_chunk_ids_for_paths(
        self, paths: list[str], per_file_limit: int = 5
    ) -> dict[str, list[int]]:
        """Map file paths to their first ``per_file_limit`` chunk ids, in document order.

        Used by retrieval to turn a ranked list of *documents* (from the summary
        index) into chunk-id candidates that can participate in RRF. Bounded per
        file so a single long document cannot dominate the fused list.
        """
        if not paths or per_file_limit <= 0:
            return {}

        placeholders = ",".join("?" for _ in paths)
        query = f"""
            SELECT f.path, c.id
            FROM chunks c
            JOIN files f ON c.file_id = f.id
            WHERE f.path IN ({placeholders})
            ORDER BY f.path, c.id
        """  # nosec B608 # noqa: S608

        by_path: dict[str, list[int]] = {}
        async with self._get_read_conn() as conn, conn.execute(query, tuple(paths)) as cursor:
            rows = await cursor.fetchall()
        for path, chunk_id in rows:
            bucket = by_path.setdefault(path, [])
            if len(bucket) < per_file_limit:
                bucket.append(chunk_id)
        return by_path

    @staticmethod
    def _bfs_cte(placeholders: str) -> str:
        """The recursive traversal CTE, without a projection.

        Split out from `bfs_from_chunks` so the traversal itself is observable:
        the outer query's `SELECT DISTINCT ... LIMIT` collapses the result set,
        which means a correct traversal and a re-expanding one return identical
        rows. The difference is entirely in how large the working table gets on
        the way there, and that is only measurable against this fragment.

        UNION (not UNION ALL) gives SQLite's working-table dedup, which is the
        visited set this bidirectional traversal needs. With UNION ALL every
        edge ping-pongs A->B->A->B to max_depth. SQLite requires all compound
        operators in one recursive CTE to match, so both terms use UNION.
        """
        return f"""
        WITH RECURSIVE
        bfs_nodes(id, depth) AS (
            SELECT id, 0
            FROM kg_nodes
            WHERE json_extract(properties, '$.chunk_id') IN ({placeholders})

            UNION

            SELECT e.target, b.depth + 1
            FROM kg_edges e
            JOIN bfs_nodes b ON e.source = b.id
            WHERE b.depth < ?

            UNION

            SELECT e.source, b.depth + 1
            FROM kg_edges e
            JOIN bfs_nodes b ON e.target = b.id
            WHERE b.depth < ?
        )
        """  # nosec B608 # noqa: S608

    async def bfs_from_chunks(
        self, chunk_ids: list[int], max_depth: int = 3, limit: int = 5
    ) -> list[int]:
        """Perform BFS to find related chunk_ids starting from a set of seed chunk_ids."""
        if not chunk_ids:
            return []

        placeholders = ",".join("?" for _ in chunk_ids)
        # Only bound placeholders are interpolated; every value is parameterized.
        projection = """
        SELECT DISTINCT CAST(json_extract(n.properties, '$.chunk_id') AS INTEGER) as chunk_id
        FROM bfs_nodes b
        JOIN kg_nodes n ON b.id = n.id
        WHERE json_extract(n.properties, '$.chunk_id') IS NOT NULL
        LIMIT ?
        """
        query = self._bfs_cte(placeholders) + projection  # nosec B608
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
        # H-4: UNION ALL is correct here - unlike bfs_from_chunks this enumerates
        # distinct *paths*, not a visited set, so SQLite's working-table dedup
        # would collapse two genuinely different routes to the same node. That
        # leaves cycles unguarded though: with A->B->A the only brake was
        # p.depth < max_depth, so a 2-cycle emitted "A -> B -> A -> B" as a
        # relational fact. The visited list carries the ids already on this path
        # and excludes an edge that would revisit one. Direction is preserved
        # deliberately: the rendered string asserts "source -[rel]-> target", so
        # traversing an edge backwards would state the relation in reverse.
        query = f"""
        WITH RECURSIVE
        paths(id, path_str, depth, visited) AS (
            SELECT id, label || ' ' || id, 0, ',' || id || ','
            FROM kg_nodes
            WHERE json_extract(properties, '$.chunk_id') IN ({placeholders})

            UNION ALL

            SELECT e.target,
                   p.path_str || ' -[' || e.relation || ']-> ' || (SELECT label || ' ' || id FROM kg_nodes WHERE id = e.target),
                   p.depth + 1,
                   p.visited || e.target || ','
            FROM kg_edges e
            JOIN paths p ON e.source = p.id
            WHERE p.depth < ?
              AND instr(p.visited, ',' || e.target || ',') = 0
        )
        SELECT path_str FROM paths
        WHERE depth > 0
        LIMIT ?
        """  # nosec B608 # noqa: S608
        params = [*src_chunk_ids, max_depth, limit]

        async with self._get_read_conn() as conn, conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    async def _maybe_commit(self, conn: aiosqlite.Connection) -> None:
        """Commit the connection only if not within an external transaction."""
        if not self._in_external_transaction:
            await conn.commit()

    @serialize_write
    async def commit(self) -> None:
        """Explicitly commits the current transaction."""
        if self._write_conn:
            try:
                await self._write_conn.commit()
            finally:
                self._in_external_transaction = False

    @serialize_write
    async def begin_transaction(self) -> None:
        """Begin an external write transaction."""
        conn = self._get_conn()
        if not self._in_external_transaction:
            try:
                await conn.execute("BEGIN IMMEDIATE")
                self._in_external_transaction = True
            except Exception as e:
                if "cannot start a transaction within a transaction" in str(e):
                    self._in_external_transaction = True
                else:
                    raise

    @serialize_write
    async def commit_transaction(self) -> None:
        """Commit the external write transaction."""
        conn = self._get_conn()
        try:
            await conn.commit()
        finally:
            self._in_external_transaction = False

    @serialize_write
    async def rollback_transaction(self) -> None:
        """Rollback the external write transaction."""
        conn = self._get_conn()
        try:
            await conn.rollback()
        finally:
            self._in_external_transaction = False

    @serialize_write
    async def begin_savepoint(self) -> str:
        """Open a nested savepoint and return its name.

        The isolation primitive for a writer that may run while another holds
        an external transaction open on the same connection.
        begin_transaction()/commit() cannot do this: begin_transaction() no-ops
        when `_in_external_transaction` is already set, and the matching
        commit() then commits *the other writer's* transaction and clears the
        flag - while rollback_transaction() discards its uncommitted work.

        SAVEPOINT nests instead. Outside a transaction it behaves like
        BEGIN/COMMIT; inside one it is a marker, so RELEASE and ROLLBACK TO
        leave the enclosing transaction exactly as they found it.

        Unlike write_transaction(), this does not hold `_write_lock` across the
        caller's work - it could not, since every write it would make is
        @serialize_write and asyncio.Lock is not reentrant. The tradeoff is in
        the docstring of the caller.
        """
        conn = self._get_conn()
        name = f"sp_{uuid.uuid4().hex}"
        await conn.execute(f"SAVEPOINT {name}")  # nosec B608 - name is a local uuid
        return name

    @serialize_write
    async def release_savepoint(self, name: str) -> None:
        """Keep the savepoint's work. Commits only if we opened the transaction."""
        conn = self._get_conn()
        await conn.execute(f"RELEASE {name}")  # nosec B608 - name from begin_savepoint
        if not self._in_external_transaction:
            await conn.commit()

    @serialize_write
    async def rollback_savepoint(self, name: str) -> None:
        """Undo back to the savepoint, leaving any enclosing transaction intact."""
        conn = self._get_conn()
        with contextlib.suppress(Exception):
            await conn.execute(f"ROLLBACK TO {name}")  # nosec B608 - internal name
            await conn.execute(f"RELEASE {name}")  # nosec B608 - internal name
        if not self._in_external_transaction:
            with contextlib.suppress(Exception):
                await conn.rollback()

    @contextlib.asynccontextmanager
    async def write_transaction(self):
        """Context manager that acquires the write lock and manages an isolated transaction/savepoint."""
        async with self._write_lock:
            conn = self._get_conn()
            savepoint_name = None
            in_tx = self._in_external_transaction
            if not in_tx:
                self._in_external_transaction = True
                try:
                    await conn.execute("BEGIN IMMEDIATE")
                except Exception as e:
                    if "cannot start a transaction within a transaction" in str(e):
                        savepoint_name = f"sp_{uuid.uuid4().hex}"
                        await conn.execute(f"SAVEPOINT {savepoint_name}")
                    else:
                        self._in_external_transaction = False
                        raise
            else:
                savepoint_name = f"sp_{uuid.uuid4().hex}"
                await conn.execute(f"SAVEPOINT {savepoint_name}")

            try:
                yield conn
                if savepoint_name:
                    await conn.execute(f"RELEASE {savepoint_name}")
                elif not in_tx:
                    await conn.commit()
            except Exception:
                if savepoint_name:
                    with contextlib.suppress(Exception):
                        await conn.execute(f"ROLLBACK TO {savepoint_name}")
                elif not in_tx:
                    with contextlib.suppress(Exception):
                        await conn.rollback()
                raise
            finally:
                if not in_tx:
                    self._in_external_transaction = False

    @serialize_write
    async def delete_file_chunks(self, file_id: int, *, auto_commit: bool = True) -> None:
        """Deletes all chunks associated with a file.

        Set ``auto_commit=False`` when called from a larger batch transaction.
        """
        conn = self._get_conn()
        if self._in_ingest_mode:
            await conn.execute(
                "INSERT OR IGNORE INTO temp_ingest_chunk_deletes(id, text_preview) "
                "SELECT id, text_preview FROM chunks WHERE file_id = ?",
                (file_id,),
            )
        await conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        if auto_commit:
            await self._maybe_commit(conn)

    async def get_file_chunk_ids(self, file_id: int) -> list[int]:
        """Ids of every chunk for a file, without decompressing any of them.

        `get_file_chunks` projects `zlib_decompress(text_preview)`, and
        zlib_decompress is a per-connection Python callback - so using it just to
        collect ids dragged every chunk of every re-indexed file across the
        C-to-Python boundary and threw the text away.
        """
        async with (
            self._get_read_conn() as conn,
            conn.execute("SELECT id FROM chunks WHERE file_id = ?", (file_id,)) as cursor,
        ):
            return [r[0] for r in await cursor.fetchall()]

    async def get_ocr_chunk_ids(self, file_id: int) -> list[int]:
        """Ids of chunks this file got from OCR, so they can be replaced alone."""
        async with (
            self._get_read_conn() as conn,
            conn.execute(
                "SELECT id FROM chunks WHERE file_id = ? AND source = 'ocr'",
                (file_id,),
            ) as cursor,
        ):
            return [r[0] for r in await cursor.fetchall()]

    @serialize_write
    async def delete_ocr_chunks(self, file_id: int, *, auto_commit: bool = True) -> None:
        """Delete only the OCR-sourced chunks of a file.

        The narrow counterpart to :meth:`delete_file_chunks`. Re-running OCR on
        a mixed PDF (native body, scanned appendix) must not take the natively
        extracted text with it.
        """
        conn = self._get_conn()
        if self._in_ingest_mode:
            await conn.execute(
                "INSERT OR IGNORE INTO temp_ingest_chunk_deletes(id, text_preview) "
                "SELECT id, text_preview FROM chunks WHERE file_id = ? AND source = 'ocr'",
                (file_id,),
            )
        await conn.execute("DELETE FROM chunks WHERE file_id = ? AND source = 'ocr'", (file_id,))
        if auto_commit:
            await self._maybe_commit(conn)

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
                query = f"SELECT path, id FROM files WHERE path IN ({placeholders})"  # nosec B608 # noqa: S608
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
                query = f"SELECT path, modified_at FROM files WHERE path IN ({placeholders})"  # nosec B608 # noqa: S608
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
                    f"SELECT path, COALESCE(sha256, '') FROM files WHERE path IN ({placeholders})"  # nosec B608 # noqa: S608
                )
                async with conn.execute(query, batch) as cursor:
                    async for row in cursor:
                        result[row[0]] = row[1]
        return result

    async def get_files_change_map(
        self, paths: list[str], *, conn: aiosqlite.Connection | None = None
    ) -> dict[str, tuple[str, str]]:
        """Return {path: (modified_at, sha256)} in a SINGLE query.

        Replaces separate calls to get_files_modified_map + get_files_sha256_map
        to halve the number of DB round-trips during change detection.
        """
        result: dict[str, tuple[str, str]] = {}
        if conn is not None:
            batch_size = 900
            for i in range(0, len(paths), batch_size):
                batch = paths[i : i + batch_size]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    f"SELECT path, modified_at, COALESCE(sha256, '') FROM files "  # nosec B608 # noqa: S608
                    f"WHERE path IN ({placeholders})"
                )
                async with conn.execute(query, batch) as cursor:
                    async for row in cursor:
                        result[row[0]] = (row[1], row[2])
            return result

        async with self._get_read_conn() as conn:
            batch_size = 900
            for i in range(0, len(paths), batch_size):
                batch = paths[i : i + batch_size]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    f"SELECT path, modified_at, COALESCE(sha256, '') FROM files "  # nosec B608 # noqa: S608
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
            "UPDATE files SET usage_count = CASE path "  # nosec B608 # noqa: S608
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
                "SELECT id, path, size, type, folder_tag, usage_count FROM files ORDER BY folder_tag, path"
            ) as cursor,
        ):
            return list(await cursor.fetchall())

    async def stream_all_nodes(self):
        """Asynchronous generator to yield all folders and files for scalable visualization."""
        async with self._get_read_conn() as conn:
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

    async def get_chunks_by_ids(self, chunk_ids: list[int]) -> list[dict[str, Any]]:
        """Fetch full chunk details for a given list of chunk IDs."""
        if not chunk_ids:
            return []

        placeholders = ",".join("?" for _ in chunk_ids)
        query_sql = (
            f"SELECT c.id, zlib_decompress(c.text_preview) as text_preview, f.path, f.folder_tag, f.modified_at, c.start_offset, c.end_offset, c.sentence_offsets, c.segmenter_version, c.file_id "  # nosec B608 # noqa: S608
            f"FROM chunks c JOIN files f ON c.file_id = f.id "
            f"WHERE c.id IN ({placeholders})"
        )
        rows = await self.execute_query(query_sql, tuple(chunk_ids))

        results = []
        for row in rows:
            results.append(
                {
                    "chunk_id": row[0],
                    "text": row[1],
                    "file_path": row[2],
                    "folder_tag": row[3],
                    "modified_at": row[4],
                    "start_offset": row[5],
                    "end_offset": row[6],
                    "sentence_offsets": row[7],
                    "segmenter_version": row[8],
                    "file_id": row[9],
                    "score": 1.0,
                }
            )
        return results

    async def execute_query(self, sql: str, params: tuple = ()) -> list[Any]:
        """Execute a read-only SQL query via the read-pool and return all rows."""
        async with self._get_read_conn() as conn, conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    @serialize_write
    async def execute_write(self, sql: str, params: tuple = ()) -> None:
        """Execute a write SQL statement via the write connection and commit."""
        conn = self._get_conn()
        await conn.execute(sql, params)
        await self._maybe_commit(conn)

    @serialize_write
    async def execute_write_returning(self, sql: str, params: tuple = ()) -> list[Any]:
        """Write and read back rows in one lock-held step.

        The write lock is released between separate calls, so a SELECT-then-
        UPDATE claim written as two statements could hand the same row to two
        callers. Doing it as one `UPDATE ... RETURNING` under the lock closes
        that window.
        """
        conn = self._get_conn()
        async with conn.execute(sql, params) as cursor:
            rows = list(await cursor.fetchall())
        await self._maybe_commit(conn)
        return rows

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
            """,
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
                        f"DELETE FROM files WHERE id IN ({placeholders})",  # nosec B608 # noqa: S608
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
        conn = self._get_conn()
        cur = await conn.execute("SELECT COUNT(*) FROM files")
        row = await cur.fetchone()
        files_count = row[0] if row else 0
        await cur.close()

        cur = await conn.execute("SELECT COUNT(*) FROM chunks")
        row = await cur.fetchone()
        chunks_count = row[0] if row else 0
        await cur.close()

        query = f"""
            -- Remove triggers so chunk deletes don't touch FTS
            {FTS_DROP_TRIGGERS_DDL}

            -- Drop the FTS virtual table entirely
            DROP TABLE IF EXISTS chunk_fts;

            -- Now safe to delete all data
            DELETE FROM chunks;
            DELETE FROM files;
            DELETE FROM query_history;
            DELETE FROM folder_profiles;

            -- Pending OCR work refers to files that no longer exist.
            DELETE FROM ocr_queue;

            -- ocr_cache is deliberately NOT cleared. It is keyed on content
            -- hash, not on file id, so it stays valid across a wipe and makes
            -- re-indexing the same documents free instead of re-running OCR.
            -- Use DELETE /api/ocr/cache to clear it explicitly.

            -- Recreate FTS table with optimized schema and contentless mode.
            -- text_preview is stored zlib-compressed so triggers decompress on the fly.
            {FTS_TABLE_DDL}
            {FTS_TRIGGERS_DDL}
        """  # nosec B608 # noqa: S608
        await conn.executescript(query)

        logger.info("Cleared all data: %d files, %d chunks", files_count, chunks_count)
        return {"files_removed": files_count, "chunks_removed": chunks_count}

    @serialize_write
    async def clear_vectors_only(self) -> dict[str, int]:
        """Delete embeddings only, leaving files/chunks/FTS/history intact.

        The model-change-safe counterpart to clear_all(). Must not touch
        `chunks` - the ON DELETE CASCADE would take chunk_embeddings with it
        and fire the FTS delete triggers, silently degrading into a full wipe.

        Scope note: this clears the SQLite `chunk_embeddings` table **only**.
        LanceDB (`pma_chunks`, `pma_summaries`, `query_cache`) is untouched -
        callers that need both must also call `LanceDBClient.clear_all()`.
        """
        conn = self._get_conn()
        cur = await conn.execute("SELECT COUNT(*) FROM chunk_embeddings")
        row = await cur.fetchone()
        embeddings_count = row[0] if row else 0
        await cur.close()

        await conn.execute("DELETE FROM chunk_embeddings")
        await conn.commit()

        logger.info("Cleared vectors only: %d embeddings removed", embeddings_count)
        return {"embeddings_removed": embeddings_count}

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
            "SELECT path, size, type, folder_tag, usage_count "  # nosec B608 # noqa: S608
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
            await self._maybe_commit(conn)

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
                    f"SELECT id, type, label, properties FROM kg_nodes WHERE id IN ({placeholders})"  # nosec B608 # noqa: S608
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
