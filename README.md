# Personal Memory Assistant (PMA)

Local-first RAG assistant for personal/project files. PMA indexes local folders, combines keyword + semantic retrieval, and answers questions with source context through a FastAPI web app (or native desktop window).

## What the project does

- Indexes local files into:
	- SQLite metadata + FTS5 text index
	- Chroma vector collections (chunk embeddings + summary embeddings)
- Supports common document/code formats, including `.txt`, `.md`, `.pdf`, `.docx`, source files, config files, and Unreal file extensions (`.uasset`, `.umap`, `.uproject`, `.uplugin`).
- Uses hybrid retrieval (FTS + vector + RRF), optional reranking, and LLM answer generation.
- Includes deterministic fast-answer routes for inventory/project-type questions to reduce latency.
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
python -m __main__ --mode server --reload
```

Open: `http://127.0.0.1:8000`

Alternative run command:

```bash
uvicorn app.main:app --reload
```

## Desktop mode

Run native-window app backed by the local FastAPI server:

```bash
python -m __main__ --mode desktop
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

## Unreal metadata import

Use this when you want deeper game-asset understanding than text extraction can provide.

Request:

```bash
curl -X POST http://127.0.0.1:8000/unreal/import \
	-H "Content-Type: application/json" \
	-d '{"json_path":"C:/path/to/unreal_metadata.json","folder_tag":"MyProject"}'
```

Behavior:

- Parses project + asset facts.
- Stores structured facts in `unreal_project_facts`.
- Updates folder profile.
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

## Troubleshooting

- **No PR diff / branch identical to main**: ensure your feature branch has at least one unique commit before opening a PR.
- **LLM answers unavailable**: set `PMA_GEMINI_API_KEY` or run local Ollama endpoint.
- **Slow first query/index**: initial model load and embedding warm-up are expected.
- **Windows scan method**: admin context may enable NTFS MFT scanning; otherwise scanner uses `scandir`.

## License

MIT (as declared in `pyproject.toml`).
