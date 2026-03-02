# Personal Memory Assistant (PMA)

![Build](https://img.shields.io/badge/build-not%20configured-lightgrey)
![Coverage](https://img.shields.io/badge/coverage-see%20coverage.xml-blueviolet)
![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)

Local-first RAG assistant for personal/project files. PMA indexes local folders, combines keyword + semantic retrieval, and answers questions with source context through a FastAPI web app (or native desktop window).

## What the project does

- Indexes local files into:
  - SQLite metadata + FTS5 text index
  - Chroma vector collections (chunk embeddings + summary embeddings)
- Supports common document/code formats, including `.txt`, `.md`, `.pdf`, `.docx`, source files, config files, and Unreal file extensions (`.uasset`, `.umap`, `.uproject`, `.uplugin`).
- Uses hybrid retrieval (FTS + vector + RRF), optional reranking, and LLM answer generation.
- Includes a deterministic fast-answer path in `/query` for inventory/project-style questions to reduce latency.
- Provides storage insights (largest files, cold files, type breakdown).
- Provides Unreal metadata import for richer project understanding (maps, characters, environment assets, etc.).

## Tech stack

- Backend: FastAPI + Uvicorn
- DB: SQLite + FTS5
- Vector store: ChromaDB
- Embeddings: sentence-transformers (`all-MiniLM-L6-v2`)
- LLM providers: Gemini (primary), Ollama fallback
- UI: Jinja2 templates + static assets
- Desktop mode: pywebview

## Project layout

```text
app/
		main.py                 # FastAPI routes, middleware, app lifespan
		config.py               # Settings (PMA_ env vars)
		embeddings/             # Embedding service/model loading
		indexing/service.py     # Scanning, chunking, embedding, upsert pipeline
		scanner/                # scandir + optional NTFS MFT scanner
		search/                 # retrieval, reranking, context builder, LLM client
		storage/                # SQLite manager + schema
		vector_store/           # Chroma client
		insights/               # Storage analytics + Unreal metadata parser
templates/
		index.html              # Web UI
static/
		...                     # Frontend assets
tests/
		test_main.py
		test_scanner.py
```

## Requirements

- Python 3.11+
- OS: Windows/Linux/macOS (Windows has additional NTFS admin-aware path)
- GUI/file picker endpoints (`/pick/folder`, `/pick/file`) require Tkinter:
  - On many Linux distributions, install OS package `python3-tk` in addition to `pip` dependencies.
- Recommended RAM for embeddings/model loading: 8 GB+

Install dependencies:

```bash
pip install -r requirements.txt
```

## Quick start (web)

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
python . --mode server --reload
```

Open: `http://127.0.0.1:8000`

Alternative run command:

```bash
uvicorn app.main:app --reload
```

## Desktop mode

Run native-window app backed by the local FastAPI server:

```bash
python . --mode desktop
```

or:

```bash
python desktop.py
```

## Configuration

Settings are loaded from `.env` using `PMA_` prefix (see `app/config.py`).

Example `.env`:

```env
PMA_HOST=127.0.0.1
PMA_PORT=8000
PMA_DEV_MODE=true
PMA_LOG_LEVEL=INFO

PMA_DB_PATH=pma_metadata.db
PMA_CHROMA_PERSIST_DIR=chroma_db

PMA_EMBEDDING_MODEL=all-MiniLM-L6-v2
PMA_EMBEDDING_BATCH_SIZE=128

PMA_CHUNK_SIZE=512
PMA_CHUNK_OVERLAP=50
PMA_MAX_FILE_SIZE_MB=50
PMA_INDEX_CONCURRENCY=8

PMA_GEMINI_API_KEY=
PMA_GEMINI_MODEL=gemini-flash-latest
PMA_GEMINI_TIMEOUT=30

PMA_OLLAMA_URL=http://localhost:11434/api/generate
PMA_OLLAMA_MODEL=llama3
PMA_OLLAMA_TIMEOUT=60

PMA_RETRIEVAL_TOP_K=6
PMA_CONTEXT_MAX_TOKENS=2200
PMA_RRF_FTS_WEIGHT=0.4
PMA_RRF_SEMANTIC_WEIGHT=0.6
PMA_RRF_K=60
PMA_SUMMARY_BOOST_FACTOR=1.25
```

### Getting API keys

1. Open `https://makersuite.google.com/app/apikey`.
2. Sign in with your Google account.
3. Create an API key.
4. Set it in `.env`:

```env
PMA_GEMINI_API_KEY=your_key_here
```

Optional Ollama fallback:

1. Install Ollama: `https://ollama.com/download`
2. Pull a model: `ollama pull llama3`
3. Ensure Ollama is running and reachable at `http://localhost:11434`
4. Set/update:

```env
PMA_OLLAMA_URL=http://localhost:11434/api/generate
PMA_OLLAMA_MODEL=llama3
```

## Usage examples

### Example 1: Basic code question

Query:

```json
{"question":"How does indexing work in this project?"}
```

Response (example):

```json
{
	"answer": "The indexing pipeline scans files, chunks content, embeds chunks, then stores metadata in SQLite and vectors in Chroma. Key flow is in app/indexing/service.py.",
	"sources": [
		{"file_path": "app/indexing/service.py"},
		{"file_path": "app/storage/db.py"},
		{"file_path": "app/vector_store/chroma_client.py"}
	],
	"retrieved_count": 6,
	"latency_ms": 420
}
```

### Example 2: Storage insights question

Query:

```json
{"question":"What are my largest indexed files?"}
```

Response (example):

```json
{
	"answer": "Top large files include ...",
	"sources": [],
	"retrieved_count": 0,
	"latency_ms": 40
}
```

### Example 3: Unreal project question

Query:

```json
{"question":"How many maps and character assets are in my Unreal project?","folder_tag":"MyProject"}
```

Response (example):

```json
{
	"answer": "Project MyProject has 12 maps and 37 character-related assets.",
	"sources": [{"file_path": "C:/GameProject"}],
	"retrieved_count": 3,
	"latency_ms": 110
}
```

## Typical workflow

1. Start server/app.
2. Index one or more folders (`/index/start` or UI picker).
3. Ask questions in the Ask tab (`/query`).
4. Check analytics in Insights (`/insights`).
5. Optionally import Unreal metadata (`/unreal/import`) for richer game-project answers.

## API overview

### Health & system

- `GET /health` — service/database/model/indexing status
- `GET /system/info` — OS, admin status, scan method, volume info

### Indexing

- `POST /index/start` — start background indexing
- `GET /index/status` — current index + progress snapshot
- `GET /index/progress-stream` — SSE progress stream
- `POST /index/reindex` — force reindex for provided folders
- `POST /index/cleanup` — remove stale DB entries for deleted files
- `POST /index/clear` — clear all indexed metadata + vectors + history
- `GET /index/export` — export indexed file metadata JSON

### Querying & history

- `POST /query` — run RAG query with optional `file_type` and `folder_tag`
- `GET /query/history` — recent query history

### File metadata & insights

- `GET /files/tree` — indexed files grouped by folder tag
- `GET /insights` — total size, top files, cold files, type breakdown

### Utilities

- `GET /pick/folder` — native folder picker
- `GET /pick/file` — native file picker (multi-select)
- `POST /demo/seed` — generate and index demo files

### Unreal metadata

- `POST /unreal/import` — import Unreal project metadata JSON and upsert project facts/profile

### Security considerations

- PMA is intended for local usage (`127.0.0.1` by default).
- No built-in authentication/authorization is enabled by default.
- Avoid binding publicly (`0.0.0.0`) unless protected by a trusted network boundary/reverse proxy.
- Keep `PMA_GEMINI_API_KEY` in `.env` (already ignored by `.gitignore`).
- Sensitive local data is stored in SQLite (`PMA_DB_PATH`, default `pma_metadata.db`) and Chroma (`PMA_CHROMA_PERSIST_DIR`, default `chroma_db/`).

### Endpoint schemas (key routes)

#### `POST /query`

Request body:

```json
{
	"question": "string (required, 1..2000)",
	"file_type": "string (optional, e.g. .py)",
	"folder_tag": "string (optional)"
}
```

Success `200` (example):

```json
{
	"answer": "string",
	"sources": [{"file_path": "string"}],
	"retrieved_count": 0,
	"latency_ms": 0
}
```

Common errors: `400` (`{"error":"Question cannot be empty."}`), `500` (`{"error":"An error occurred while processing your query."}`), `422` (`{"error":"Validation error","details":["..."]}`)

#### `POST /index/start`

Request body:

```json
{
	"folders": ["C:/path/one", "C:/path/two"]
}
```

Success `202`: `{"message":"Indexing started"}`

Common errors: `400` (`{"error":"No valid folder paths provided."}`), `422`

#### `GET /index/status`

Success `200` (example):

```json
{
	"status": "idle|running|done|error",
	"files_indexed": 0,
	"chunks_indexed": 0,
	"progress_percent": 0,
	"scan_method": "scandir|ntfs_mft",
	"scan_duration_ms": 0.0,
	"skipped_files": 0,
	"new_files": 0,
	"changed_files": 0
}
```

Common errors: `500`

#### `GET /insights`

Success `200`: statistics object from `InsightsService.get_stats()` including size/use/type distributions.

Common errors: `500`

#### `POST /index/reindex`

Request body:

```json
{
	"folders": ["C:/path/to/reindex"]
}
```

Success `202`: `{"message":"Re-indexing started (change detection bypassed)"}`

Common errors: `400`, `422`

#### `POST /index/clear`

Request body: none

Success `200`: count summary object returned by database clear operation.

Common errors: `500` (`{"error":"Failed to clear database. Please check server logs."}`)

#### `POST /unreal/import`

Request body:

```json
{
	"json_path": "C:/path/to/unreal_metadata.json",
	"folder_tag": "MyProject (optional)"
}
```

Success `200` (example):

```json
{
	"message": "Unreal metadata imported successfully.",
	"project": {
		"name": "string",
		"engine_version": "string",
		"folder_tag": "string",
		"folder_path": "string"
	},
	"stats": {
		"total_assets": 0,
		"map_count": 0,
		"environment_assets": 0,
		"character_blueprints": 0,
		"pawn_blueprints": 0,
		"skeletal_meshes": 0,
		"material_count": 0,
		"niagara_systems": 0
	}
}
```

Common errors: `400` (`{"error":"Metadata JSON file does not exist."}`), `500` (`{"error":"Failed to import Unreal metadata."}`), `422`

## Unreal metadata import

Use this when you want deeper game-asset understanding than text extraction can provide.

Compatibility:

- Supports metadata exports from Unreal Engine `UE4.x` and `UE5.x` as long as fields map to the accepted keys below.
- `engine_version` is stored as text and used for project facts/profile context (compatibility is best-effort by schema match, not hard-blocked by version).

Path format note:

- In JSON payloads, Windows backslashes must be escaped (`C:\\path\\to\\file.json`).
- Using forward slashes (`C:/path/to/file.json`) is recommended for copy/paste simplicity.

Request:

```bash
curl -X POST http://127.0.0.1:8000/unreal/import \
	-H "Content-Type: application/json" \
	-d '{"json_path":"C:/path/to/unreal_metadata.json","folder_tag":"MyProject"}'
```

Expected JSON schema (accepted aliases shown):

```json
{
	"project_name": "MyProject",        
	"engine_version": "5.3.2",          
	"project_path": "C:/Projects/MyProject",
	"assets": [
		{
			"asset_class": "Blueprint",
			"object_path": "/Game/Characters/BP_Hero",
			"tags": {"Category": "Character"}
		}
	]
}
```

Also accepted key variants include:

- `ProjectName`/`project_name`/`Name`/`name`
- `EngineVersion`/`engine_version`/`EngineAssociation`
- `ProjectPath`/`project_path`/`RootPath`
- `Assets`/`assets`/`AssetData` (including `AssetRegistry.Assets`)

Behavior:

- Parses project + asset facts.
- Stores structured facts in `unreal_project_facts`.
- Updates folder profile (`folder_tag` override supported).
- Tries to embed/store a summary for retrieval boosting.

## Retrieval pipeline summary

For semantic questions:

1. Compute query embedding.
2. Run keyword search (FTS5) + semantic search (Chroma) + summary search.
3. Fuse rankings via RRF.
4. Optional reranking for precision.
5. Build context and generate final answer with LLM.

For inventory/project-style questions, PMA may return deterministic fast answers without LLM call.

## Testing

Run tests:

```bash
pytest -q
```

With coverage:

```bash
pytest --cov=app --cov-report=term-missing --cov-report=xml
```

## Packaging notes

This repo includes helper launchers for desktop/executable workflows:

- `desktop.py` — native window launcher (pywebview)
- `launcher.py` — packaged launcher-friendly entrypoint
- `PMA.spec` — PyInstaller spec

### Building an executable

1. Install PyInstaller:

```bash
pip install pyinstaller
```

2. Build from the spec:

```bash
pyinstaller PMA.spec
```

3. Output location:

- One-folder output is created under `dist/PMA/`.
- Depending on platform/build options, an executable like `dist/PMA.exe` may also be produced on Windows.

Notes:

- First launch can be slower because embedding/model components warm up.
- Packaging with `PMA.spec`/`launcher.py` includes runtime assets needed for desktop launchers (including local model/chroma runtime data paths used by the app).

## Troubleshooting

- **LLM answers unavailable**: set `PMA_GEMINI_API_KEY` or run local Ollama endpoint.
- **Slow first query/index**: initial model load and embedding warm-up are expected.
- **Windows scan method**: admin context may enable NTFS MFT scanning; otherwise scanner uses `scandir`.

## Contributing

- When opening a PR, ensure your branch contains at least one unique commit so the PR has a non-empty diff.

## License

MIT. See `LICENSE`.
