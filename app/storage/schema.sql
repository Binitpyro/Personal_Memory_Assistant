-- SQLite Configuration Pragmas
PRAGMA auto_vacuum = INCREMENTAL;
PRAGMA journal_mode = WAL;

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
    sha256 TEXT DEFAULT ''
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_files_folder_tag ON files(folder_tag);
CREATE INDEX IF NOT EXISTS idx_files_modified_at ON files(modified_at);
CREATE INDEX IF NOT EXISTS idx_files_type ON files(type);
CREATE INDEX IF NOT EXISTS idx_files_change_detection ON files(path, modified_at, sha256);

-- Chunks table for storing text segments
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    text_preview TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);

-- FK index for efficient joins / cascading deletes
CREATE INDEX IF NOT EXISTS idx_chunks_file_id ON chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_text_lookup ON chunks(id, text_preview);

-- FTS5 virtual table for keyword search
-- Note: SQLite FTS5 should be enabled in the environment
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    chunks_text,
    content=chunks,
    content_rowid=id,
    detail=column -- Phase 9.2: strip byte-offset index (saves ~40% space)
);

-- Trigger to keep FTS index in sync with chunks table
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

-- Structured Unreal project facts imported from metadata export
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
);

CREATE INDEX IF NOT EXISTS idx_unreal_facts_folder_tag ON unreal_project_facts(folder_tag);

-- NOTE: The covering index idx_chunks_covering was dropped in Phase 9 
-- because it duplicated the entire text corpus and caused massive bloat.

-- NOTE: idx_files_change_detection is created in db.py migrations
-- because it references the sha256 column added via ALTER TABLE.
