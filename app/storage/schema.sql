-- Files table for storing metadata
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    size INTEGER NOT NULL,
    modified_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    type TEXT NOT NULL,
    folder_tag TEXT,
    usage_count INTEGER DEFAULT 0,
    summary TEXT DEFAULT '',
    sha256 TEXT DEFAULT '',
    extract_status TEXT DEFAULT '',
    root_path TEXT DEFAULT ''
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_files_folder_tag ON files(folder_tag);
CREATE INDEX IF NOT EXISTS idx_files_modified_at ON files(modified_at);
CREATE INDEX IF NOT EXISTS idx_files_type ON files(type);
CREATE INDEX IF NOT EXISTS idx_files_size ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_change_detection ON files(path, modified_at, sha256);

-- Chunks table for storing text segments
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    sentence_offsets TEXT,
    segmenter_version TEXT,
    text_preview TEXT NOT NULL,
    -- Provenance. NULL/'' = extracted natively, 'ocr' = produced by the OCR
    -- worker. Re-running OCR on a mixed PDF must replace only its own chunks
    -- and leave the natively-extracted body text alone.
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);

-- FK index for efficient joins / cascading deletes
CREATE INDEX IF NOT EXISTS idx_chunks_file_id ON chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_text_lookup ON chunks(id, text_preview);

-- Chunk embeddings table for storing vector math as binary BLOBs
-- This strictly isolates heavy binary data from normal metadata queries
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id INTEGER PRIMARY KEY,
    embedding BLOB NOT NULL,
    FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);

-- FTS5 virtual table for keyword search
-- Note: SQLite FTS5 should be enabled in the environment
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    chunks_text,
    content="",
    tokenize="trigram",
    detail=full -- Updated to full to fix trigram phrase search
);

-- Trigger to keep FTS index in sync with chunks table
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunk_fts(rowid, chunks_text) VALUES (new.id, zlib_decompress(new.text_preview));
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text) VALUES('delete', old.id, zlib_decompress(old.text_preview));
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, chunks_text) VALUES('delete', old.id, zlib_decompress(old.text_preview));
  INSERT INTO chunk_fts(rowid, chunks_text) VALUES (new.id, zlib_decompress(new.text_preview));
END;

-- Query history table for tracking user searches
CREATE TABLE IF NOT EXISTS query_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT DEFAULT '',
    source_count INTEGER DEFAULT 0,
    latency_ms REAL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_query_history_created ON query_history(created_at DESC);

-- Folder profiles for project-level understanding
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
);

CREATE INDEX IF NOT EXISTS idx_folder_profiles_tag ON folder_profiles(folder_tag);
CREATE INDEX IF NOT EXISTS idx_folder_profiles_type ON folder_profiles(project_type);



-- NOTE: The covering index idx_chunks_covering was dropped in Phase 9 
-- because it duplicated the entire text corpus and caused massive bloat.

-- NOTE: idx_files_change_detection is created in db.py migrations
-- because it references the sha256 column added via ALTER TABLE.

-- PMA Metrics for Telemetry (Local-Only)
CREATE TABLE IF NOT EXISTS pma_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id INTEGER,
    time_to_first_token_ms REAL,
    response_abandoned BOOLEAN DEFAULT 0,
    query_retry_within_60s BOOLEAN DEFAULT 0,
    deep_analysis_toggled BOOLEAN DEFAULT 0,
    mode_selected TEXT,
    force_include_count INTEGER DEFAULT 0,
    feature_thumbs TEXT,
    model_class TEXT,
    context_tokens_budget INTEGER,
    context_tokens_used INTEGER,
    chunks_included INTEGER,
    chunks_dropped INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(query_id) REFERENCES query_history(id) ON DELETE SET NULL
);

-- System state table for persisting internal flags (e.g. crash recovery)
CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- OCR work queue. One row per file that has scanned pages awaiting OCR.
-- Keyed by path (not file_id) so a row survives the file being re-indexed.
CREATE TABLE IF NOT EXISTS ocr_queue (
    file_path   TEXT PRIMARY KEY,
    pages_json  TEXT NOT NULL DEFAULT '[]',   -- JSON array of 0-based page indices
    page_count  INTEGER NOT NULL DEFAULT 0,
    pages_done  INTEGER NOT NULL DEFAULT 0,
    tier        TEXT NOT NULL DEFAULT 'cpu',
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed|skipped
    force_ocr   INTEGER NOT NULL DEFAULT 0,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT NOT NULL DEFAULT '',
    enqueued_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ocr_queue_status ON ocr_queue(status, enqueued_at);

-- OCR page cache, keyed on file *content* rather than path or file id.
-- Deliberately excluded from clear_all(): re-indexing the same bytes should
-- hit this cache instead of paying for OCR again.
CREATE TABLE IF NOT EXISTS ocr_cache (
    content_key   TEXT NOT NULL,
    page_num      INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    preproc_hash  TEXT NOT NULL,
    text          TEXT NOT NULL DEFAULT '',   -- JSON: {"lines":[{t,c,l}],"mean_conf":..}
    mean_conf     REAL NOT NULL DEFAULT 0.0,
    bytes         INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (content_key, page_num, model_version, preproc_hash)
);

CREATE INDEX IF NOT EXISTS idx_ocr_cache_lru ON ocr_cache(last_used_at);
