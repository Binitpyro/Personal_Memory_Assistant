<div align="center">

# 🧠 Personal Memory Assistant (PMA)

## Your files. Your knowledge. Instantly searchable.

A **local-first AI-powered assistant** that indexes your personal and project files, then answers natural-language questions with full source attribution — all without sending your data to the cloud.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-FF6F00?style=for-the-badge&logo=databricks&logoColor=white)](https://www.trychroma.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

---

**[Features](#-features) · [Quick Start](#-quick-start) · [Architecture](#-architecture) · [API Reference](#-api-reference) · [Configuration](#-configuration) · [Contributing](#-contributing)**

</div>

---

## 🎯 Problem Statement

We create and accumulate **hundreds of files** — code, notes, documents, game assets — across different projects and folders. Finding that one snippet, that one design decision, or understanding what's inside a massive project folder becomes a time sink. Traditional search tools match keywords but don't *understand* your content.

**PMA bridges that gap.** It builds a semantic memory layer over your local files, letting you ask questions in plain English and get precise, source-backed answers in milliseconds.

---

## ✨ Features

<table>
<tr>
<td width="50%">

### 🔍 Hybrid Retrieval (RAG)
Combines **FTS5 keyword search** + **vector semantic search** + **Reciprocal Rank Fusion** for best-of-both-worlds accuracy.

### ⚡ Deterministic Fast Path
Inventory and project-overview questions are answered instantly from metadata — no LLM round-trip needed.

### 🖥️ Dual Interface
Run as a **web app** in your browser or as a **native desktop app** via pywebview — same backend, your choice.

</td>
<td width="50%">

### 📁 Smart Indexing Pipeline
Incremental change detection, batched embedding, folder-profile synthesis, and automatic project-type inference.

### 📊 Storage Insights
Visual analytics on your indexed files: largest files, cold/unused files, type breakdown, and storage distribution.

### 🎮 Unreal Engine Support
First-class import of UE4/UE5 metadata for rich game-project understanding (maps, characters, materials, Niagara systems).

</td>
</tr>
</table>

---

## 🏗️ Architecture

```text
┌──────────────────────────────────────────────────────────────────┐
│                        Client Layer                              │
│            Browser  ·  pywebview Desktop Window                  │
└──────────────────────┬───────────────────────────────────────────┘
                       │  REST + SSE
┌──────────────────────▼───────────────────────────────────────────┐
│                     FastAPI Application                           │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐   │
│  │ Indexing │  │ Retrieval│  │ Insights │  │ Unreal Import  │   │
│  │ Pipeline │  │  + RAG   │  │ Analytics│  │  (UE4/UE5)     │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬────────┘   │
│       │              │             │                │            │
│  ┌────▼──────────────▼─────────────▼────────────────▼────────┐  │
│  │                   Service Layer                            │  │
│  │  Scanner · Embeddings · LLM Client · Reranker · Context   │  │
│  └────┬──────────────┬───────────────────────────────────────┘  │
└───────┼──────────────┼──────────────────────────────────────────┘
        │              │
┌───────▼──────┐ ┌─────▼──────────┐ ┌─────────────────────────┐
│   SQLite     │ │   ChromaDB     │ │   External LLM          │
│ Metadata+FTS │ │ Vector Store   │ │ Gemini · Ollama         │
└──────────────┘ └────────────────┘ └─────────────────────────┘
```

### Retrieval Pipeline

```text
Query ──► Intent Classification
              │
         ┌────┴─────────────────────┐
         ▼                          ▼
   Metadata Intent           Semantic Intent
   (fast path)               (full RAG)
         │                          │
         │               ┌─────────┬┴──────────┐
         │               ▼         ▼            ▼
         │            FTS5     Semantic     Summary
         │           Search    Search       Search
         │               └─────────┴────────────┘
         │                         │
         │                    RRF Fusion
         │                         │
         │                  Optional Rerank
         │                         │
         │                  Context Assembly
         │                         │
         │                    LLM Answer
         │                         │
         └────────┬────────────────┘
                  ▼
          Response + Sources
```

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.11+**
- **8 GB+ RAM** recommended (for embedding model)
- **Gemini API key** or local **Ollama** instance for LLM answers

### Installation

```bash
# Clone the repository
git clone https://github.com/Binitpyro/Sandisk-Hackathon.git
cd Sandisk-Hackathon

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

### Set up your environment

```bash
# Create .env file
copy .env.example .env        # Windows
# cp .env.example .env        # Linux/macOS
```

Add your Gemini API key (get one at [Google AI Studio](https://makersuite.google.com/app/apikey)):

```env
PMA_GEMINI_API_KEY=your_key_here
```

### Run

```bash
# Web mode (default)
python . --mode server --reload

# Desktop mode (native window)
python . --mode desktop
```

Open **http://127.0.0.1:8000** in your browser (web mode).

---

## 📖 Usage

### Typical Workflow

```text
1. Launch PMA  ──►  2. Index Folders  ──►  3. Ask Questions  ──►  4. View Insights
                         via UI or API         natural language        analytics dashboard
```

### Example Queries

| Query | Type | Latency |
|-------|------|---------|
| *"How does the indexing pipeline work?"* | Semantic RAG | ~400 ms |
| *"What are my largest indexed files?"* | Fast path (metadata) | ~40 ms |
| *"How many maps are in my Unreal project?"* | Fast path (project facts) | ~110 ms |
| *"Summarize the authentication module"* | Semantic RAG | ~500 ms |

### Sample Response

```json
{
  "answer": "The indexing pipeline scans files, chunks content, embeds chunks, then stores metadata in SQLite and vectors in Chroma.",
  "sources": [
    { "file_path": "app/indexing/service.py" },
    { "file_path": "app/storage/db.py" }
  ],
  "retrieved_count": 6,
  "latency_ms": 420
}
```

---

## 📡 API Reference

### Core Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Service, database, model, and indexing status |
| `POST` | `/query` | Run a RAG query with optional filters |
| `GET` | `/query/history` | Recent query history |

### Indexing

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/index/start` | Start background indexing for given folders |
| `GET` | `/index/status` | Current indexing state + progress snapshot |
| `GET` | `/index/progress-stream` | SSE real-time progress stream |
| `POST` | `/index/reindex` | Force reindex (bypass change detection) |
| `POST` | `/index/cleanup` | Remove stale DB entries for deleted files |
| `POST` | `/index/clear` | Clear all indexed data + vectors + history |
| `GET` | `/index/export` | Export indexed file metadata as JSON |

### Insights & Utilities

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/files/tree` | Indexed files grouped by folder tag |
| `GET` | `/insights` | Storage analytics dashboard data |
| `GET` | `/pick/folder` | Native OS folder picker dialog |
| `GET` | `/pick/file` | Native OS file picker (multi-select) |
| `POST` | `/demo/seed` | Generate and index demo files |
| `POST` | `/unreal/import` | Import Unreal Engine project metadata |

<details>
<summary><b>Expand: Request/Response Schemas</b></summary>

#### `POST /query`

```json
// Request
{ "question": "string (1..2000)", "file_type": ".py", "folder_tag": "MyProject" }

// Response 200
{ "answer": "string", "sources": [{"file_path": "string"}], "retrieved_count": 6, "latency_ms": 420 }
```

#### `POST /index/start`

```json
// Request
{ "folders": ["C:/path/one", "C:/path/two"] }

// Response 202
{ "message": "Indexing started" }
```

#### `GET /index/status`

```json
// Response 200
{
  "status": "idle|running|done|error",
  "files_indexed": 0, "chunks_indexed": 0, "progress_percent": 0,
  "scan_method": "scandir|ntfs_mft", "scan_duration_ms": 0.0,
  "skipped_files": 0, "new_files": 0, "changed_files": 0
}
```

#### `POST /unreal/import`

```json
// Request
{ "json_path": "C:/path/to/metadata.json", "folder_tag": "MyProject" }

// Response 200
{
  "message": "Unreal metadata imported successfully.",
  "project": { "name": "string", "engine_version": "5.3.2", "folder_tag": "string" },
  "stats": { "total_assets": 0, "map_count": 0, "character_blueprints": 0, "material_count": 0 }
}
```

</details>

---

## ⚙️ Configuration

All settings are loaded from a `.env` file using the `PMA_` prefix. See [app/config.py](app/config.py) for full details.

<details>
<summary><b>Expand: Full Configuration Reference</b></summary>

| Variable | Default | Description |
|----------|---------|-------------|
| `PMA_HOST` | `127.0.0.1` | Server bind address |
| `PMA_PORT` | `8000` | Server port |
| `PMA_DEV_MODE` | `true` | Enable development mode |
| `PMA_LOG_LEVEL` | `INFO` | Logging level |
| `PMA_DB_PATH` | `pma_metadata.db` | SQLite database path |
| `PMA_CHROMA_PERSIST_DIR` | `chroma_db` | ChromaDB persistence directory |
| `PMA_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformer model name |
| `PMA_EMBEDDING_BATCH_SIZE` | `128` | Embedding batch size |
| `PMA_CHUNK_SIZE` | `512` | Text chunk size (tokens) |
| `PMA_CHUNK_OVERLAP` | `50` | Overlap between chunks |
| `PMA_MAX_FILE_SIZE_MB` | `50` | Max file size to index |
| `PMA_INDEX_CONCURRENCY` | `8` | Concurrent indexing workers |
| `PMA_GEMINI_API_KEY` | — | Google Gemini API key |
| `PMA_GEMINI_MODEL` | `gemini-flash-latest` | Gemini model variant |
| `PMA_OLLAMA_URL` | `http://localhost:11434/api/generate` | Ollama endpoint |
| `PMA_OLLAMA_MODEL` | `llama3` | Ollama model name |
| `PMA_RETRIEVAL_TOP_K` | `6` | Number of retrieval results |
| `PMA_RRF_FTS_WEIGHT` | `0.4` | RRF weight for keyword search |
| `PMA_RRF_SEMANTIC_WEIGHT` | `0.6` | RRF weight for semantic search |
| `PMA_SUMMARY_BOOST_FACTOR` | `1.25` | Summary embedding boost |

</details>

### LLM Provider Setup

<details>
<summary><b>Gemini (Primary)</b></summary>

1. Go to [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Sign in and create an API key
3. Add to `.env`: `PMA_GEMINI_API_KEY=your_key_here`

</details>

<details>
<summary><b>Ollama (Local Fallback)</b></summary>

1. Install from [ollama.com](https://ollama.com/download)
2. Pull a model: `ollama pull llama3`
3. Ensure it's running at `http://localhost:11434`
4. Update `.env` if using non-default settings

</details>

---

## 🗂️ Project Structure

```text
Sandisk-Hackathon/
├── __main__.py              # CLI entrypoint
├── desktop.py               # Native desktop launcher (pywebview)
├── launcher.py              # Packaged executable entrypoint
├── pyproject.toml           # Project metadata & tool config
├── requirements.txt         # Python dependencies
│
├── app/
│   ├── main.py              # FastAPI app, routes, middleware, lifespan
│   ├── config.py            # Settings via PMA_ env vars
│   ├── embeddings/          # Sentence-transformer model loading
│   ├── indexing/            # Scan → chunk → embed → store pipeline
│   ├── scanner/             # OS scandir + NTFS MFT fast scanner
│   ├── search/              # Retrieval, reranking, context, LLM client
│   ├── storage/             # SQLite manager + FTS5 schema
│   ├── vector_store/        # ChromaDB client wrapper
│   ├── insights/            # Storage analytics + Unreal metadata
│   └── utils/               # Shared utilities & metrics
│
├── templates/index.html     # Single-page web UI
├── static/                  # Frontend CSS, JS, chart libraries
├── tests/                   # pytest test suite
└── chroma_db/               # Persistent vector store (gitignored)
```

---

## 🛠️ Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Backend** | FastAPI + Uvicorn | Async API server |
| **Database** | SQLite + FTS5 | Metadata, full-text search |
| **Vector Store** | ChromaDB | Semantic embeddings storage |
| **Embeddings** | sentence-transformers (`all-MiniLM-L6-v2`) | Local embedding generation |
| **LLM** | Google Gemini / Ollama | Answer generation |
| **Frontend** | Jinja2 + Vanilla JS + Chart.js | Web UI with analytics |
| **Desktop** | pywebview | Native window wrapper |
| **File Scanning** | `os.scandir` / NTFS MFT | High-speed file enumeration |

---

## 🧪 Testing

```bash
# Run tests
pytest -q

# Run with coverage report
pytest --cov=app --cov-report=term-missing --cov-report=xml
```

---

## 📦 Building Executable

```bash
pip install pyinstaller
pyinstaller PMA.spec
```

Output is produced under `dist/PMA/`. On Windows, a standalone `PMA.exe` is also generated.

---

## 🔒 Security Notes

- **Local-first by default** — binds to `127.0.0.1`, not exposed to network.
- **No built-in auth** — designed for single-user local use.
- **API keys stay local** — `.env` is gitignored; keys are never committed.
- **LLM context** — outbound calls to Gemini/Ollama include query context; use Ollama for fully offline operation.

---

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| LLM answers unavailable | Set `PMA_GEMINI_API_KEY` in `.env` or start a local Ollama instance |
| Slow first query / index | Expected — initial model load and embedding warm-up takes a few seconds |
| Windows scan method shows `scandir` | Run as administrator to enable faster NTFS MFT scanning |
| Tkinter errors on Linux | Install `python3-tk` via your system package manager |

---

## 🤝 Contributing

Contributions are welcome! When opening a PR, ensure your branch contains at least one unique commit so the pull request has a non-empty diff.

```bash
# Development setup
pip install -e ".[dev]"

# Run linter
ruff check app/ tests/

# Run type checker
mypy app/
```

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

<div align="center">

**Built for the SanDisk Hackathon** · Made with ❤️ by [Binitpyro](https://github.com/Binitpyro)

</div>
