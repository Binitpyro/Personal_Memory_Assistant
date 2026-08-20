# Personal Memory Assistant (PMA)
### Local-First Semantic Search & Intelligence Engine

[![Version](https://img.shields.io/badge/Version-0.0.72-blue?style=flat-square)](https://github.com/Binitpyro/Personal_Memory_Assistant)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Rust](https://img.shields.io/badge/Rust-1.77+-brown?style=flat-square&logo=rust&logoColor=white)](https://www.rust-lang.org/)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev/)
[![Tauri](https://img.shields.io/badge/Tauri-v2-FFC107?style=flat-square&logo=tauri&logoColor=black)](https://tauri.app/)

Personal Memory Assistant (PMA) is a high-performance, local-first search and retrieval system. It indexes your documents and projects with near-zero latency, allowing you to perform semantic queries and gain structural insights without your data ever leaving your machine.

---

## 1. Project Structure

```text
├── app/                # Python Backend (FastAPI, Extraction, RAG, OCR)
├── frontend/           # React 19 Frontend & Tauri Desktop Shell
├── prompts/            # AI System Templates (RAG Logic)
├── scripts/            # Automated Development & Build Tooling (Windows)
```

---

## 2. Technology Stack

- **Frontend**: React 19 (Vite), TypeScript, TailwindCSS v4
- **App Shell**: Tauri v2 (Rust-based native shell)
- **Backend**: FastAPI (Python 3.12), Pydantic v2
- **Storage Layer**:
    - **Metadata**: SQLite (FTS5 for keyword search)
    - **Vector Store**: LanceDB (High-performance O(1) semantic retrieval; separate tables for chunk embeddings and document-summary embeddings)
- **Extraction**: Rust-powered parallel file walker & stream extractor
- **Models**: ONNX Runtime and tokenizers for local embedding generation and cross-encoder reranking
- **OCR**: optional, off by default. Reads scanned PDFs in an isolated subprocess venv — see [§7](#7-ocr-for-scanned-documents-optional)
- **AI Providers**: Gemini, OpenAI, Anthropic, Groq, OpenRouter, NVIDIA NIM, Ollama, LM Studio, and generic OpenAI-compatible endpoints
- **Spatial Engine**: WebGPU-powered GPGPU compute for real-time indexing & Volumetric Visualization

---

## 3. Privacy & Security

Local-first is enforced in the code, not just claimed in the pitch.

- **Nothing leaves your machine by default.** No telemetry, no phone-home, no network access required at first run.
- **Cloud providers are opt-in**, and consent is checked against the *resolved destination* rather than a provider's label — a provider registered as "local" but pointed at a remote host is not exempt. PMA states plainly that free-tier cloud dispatch may use your inputs for model training before it sends anything.
- **API keys live in the OS keyring.** A key set in `.env` overrides the keyring and is managed outside the UI.
- **Every `/api/` route requires a local access token**, sent as the `X-Local-Access-Token` header only and compared in constant time. There is no `?token=` query fallback, so tokens never reach access logs, browser history, or `Referer`.
- **A Content-Security-Policy with a per-request script nonce** is applied to browser-served pages, alongside `X-Content-Type-Options`, `X-Frame-Options` and `Referrer-Policy`.
- **Model output is sanitised before it renders.** PMA searches documents you did not necessarily write, so a maliciously crafted file must not be able to steer the model into emitting working HTML inside the app.
- **Destructive and expensive endpoints are rate-limited** — `/index/clear`, `/index/cleanup` and `/index/folder/remove` at 3/minute, `/index/export` at 10/minute. `/index/status` is deliberately left unlimited so the Library page can poll it.

---

## 4. Getting Started

### 4.1 Prerequisites
- **Python 3.12+** (Managed via `uv` recommended)
- **Node.js 20+**
- **Rust Toolchain** (Latest stable)

**Hardware.** PMA targets budget machines on purpose: a ~4 GB VRAM GPU (GTX 1650 / RX 580 class) and an 8 GB RAM laptop. It holds roughly 196 MB resident while serving queries; bulk indexing peaks a few hundred MB above that and returns.

### 4.2 Quick Start (Windows)
Copy the example configuration, then choose the development workflow you need:

1. **Create local configuration**:
   ```powershell
   Copy-Item .env.example .env
   ```
2. **Launch browser-mode development**:
   ```powershell
   ./scripts/StartPMA.bat       # Starts the FastAPI backend and Vite frontend
   ```
3. **Launch the Tauri desktop app**:
   ```powershell
   cd frontend
   npm run tauri dev
   ```

### 4.3 Manual Installation (Cross-Platform)
1. **Initialize Backend**:
   ```bash
   uv sync --all-extras
   cd app/scanner/rust_core && maturin develop --release
   ```
2. **Initialize Frontend**:
   ```bash
   cd frontend && npm install && npm run build
   ```

### 4.4 Credentials

PMA reads configuration from `.env` and checks the operating system keyring for
provider API keys. Enter API keys during onboarding or in **Settings → Providers**;
PMA stores those keys in the OS keyring by default. A provider API key set in `.env`
takes precedence and is managed outside the UI, so it cannot be changed or deleted
from Settings.

---

## 5. Development & Automation Scripts

The `scripts/` directory contains tools to automate the development lifecycle.

| Script | Purpose |
| :--- | :--- |
| `StartPMA.bat` | Starts the FastAPI backend and Vite frontend for browser-mode development. |
| `Run-Tests.bat` | Executes the full test suite (Python, Rust, and Frontend). |
| `run_ci_checks.bat` | Runs the project's lint, type, test, and security checks. |
| `Build-Exe.bat` | Builds the legacy PyInstaller sidecar executable; this standalone EXE workflow is temporarily on hold and is not a current release artifact. |
| `Reindex-Embeddings.bat` | Resets and regenerates the LanceDB vector store. |
| `dev.bat` | Launches the Vite development server for the frontend. |

### Testing and CI

```powershell
./scripts/Run-Tests.bat       # Full Python, Rust, frontend, and E2E test suite
./scripts/run_ci_checks.bat   # Lint, type, test, and security checks
```

Neither script can run while the development backend is up — the dependency sync cannot
replace the compiled `rust_core` extension while the server holds it.

To run the frontend test suite directly:

```powershell
cd frontend
npm run test
```

---

## 6. Configuration

PMA uses a `.env` file in the root directory for critical configuration. See `.env.example` for details.

```env
# Example .env configuration
PMA_HOST=127.0.0.1
PMA_PORT=8000
PMA_LOG_LEVEL=INFO
PMA_LANCEDB_MODE=portable
PMA_DB_PATH=data/pma_metadata.db
PMA_LANCEDB_PERSIST_DIR=data/lancedb
PMA_GEMINI_MODEL=gemini-2.5-flash-lite

# Optional: an API key here overrides the OS-keyring value for this provider.
# PMA_GEMINI_API_KEY=your_gemini_api_key_here
```

See [`.env.example`](.env.example) for all supported provider and indexing settings. Values in
`.env` override the built-in defaults, so anything you leave commented out stays on the tuned
default rather than falling back to something worse.

---

## 7. OCR for Scanned Documents (optional)

A scanned PDF carries no text layer, so ordinary extraction finds nothing in it. OCR fills
that gap. It is **off by default** and installs into its own isolated virtual environment
from **Settings → OCR**, so leaving it switched off costs nothing — no extra dependencies
reach the main application.

| Option | What it does | Download |
| :--- | :--- | :--- |
| **Standard** | Runs on the processor. Around one page per second. | ~230 MB |
| **High accuracy** | Larger, more accurate models on your graphics card. Windows only. | ~430 MB |
| **Your own AI model** | Sends each page to a vision model you already run in Ollama or LM Studio. | nothing to install |

**High accuracy needs roughly 6 GB of free graphics memory** — measured at 5.2 GB in use — so
a 4 GB card is not enough and will fall back to the processor, ending up slower than
Standard. Pick Standard on a 4 GB card.

**Your own AI model** downloads nothing: you pull the vision model yourself. It handles messy
handwriting and unusual layouts better than the other options, but takes minutes per page
rather than seconds, so it suits a few important documents rather than a whole library.

---

## 8. Packaging

Tauri is the supported desktop distribution path. Build the Windows MSI installer with:

```powershell
cd frontend
npm run tauri build
```

`Build-Exe.bat` still creates a PyInstaller sidecar executable, but that standalone
EXE workflow is temporarily on hold and should not be used as a current release artifact.

---

## 9. Architecture

### 9.1 Ingestion Pipeline
The system reads files in streams to ensure scalability without exceeding memory limits. Each file is chunked for keyword/semantic indexing and independently distilled into a structural summary that gets its own embedding, giving retrieval a document-level signal alongside chunk-level ones. Pages with no text layer are deferred to a durable OCR queue and rejoin the pipeline once recognised.

```mermaid
graph LR
    subgraph "Scanning"
        A[File System] --> B[Rust File Scanner]
    end

    subgraph "Processing"
        B --> C[Text Extractor]
        C --> D[SQLite Database]
        C --> E[Chunker]
        C --> S[Deep Summarizer]
        C -->|no text layer| O[OCR Queue]
        O --> W[OCR Worker]
        W --> E
    end

    subgraph "Indexing"
        E --> F[AI Embedder]
        F --> G[LanceDB Chunk Vectors]
        E --> H[FTS5 Keyword Index]
        S --> G2[LanceDB Summary Vectors]
    end
```

### 9.2 Unified Search Flow
A query planner first classifies intent into one of four modes: fast metadata/project lookups bypass retrieval entirely, graph-intent queries traverse the knowledge graph, and everything else runs the full RAG pipeline. FULL_RAG fuses three signals — keyword matching (SQLite FTS5), chunk-level semantic search, and document-summary semantic search — via **Reciprocal Rank Fusion (RRF)**, then applies a cross-encoder reranker for maximum precision, automatically bypassed when the top result's confidence decisively clears the runner-up.

```mermaid
graph LR
    A[Query] --> P[Query Planner]

    P -->|FAST_METADATA| M[SQLite Stats]
    P -->|FAST_PROJECT| J[Project Metadata Lookup]
    P -->|GRAPH_SEARCH| K[Knowledge Graph Traversal]

    P -->|FULL_RAG| B[Hybrid Retrieval]
    B -->|Keywords| C[SQLite FTS5]
    B -->|Chunk Meaning| D[LanceDB Semantic]
    B -->|Doc Meaning| N[LanceDB Summaries]
    C --> E[RRF Ranker]
    D --> E
    N --> E
    E --> R{Confidence Gap >= 2x?}
    R -->|Yes: bypass| F[Context Builder]
    R -->|No: rerank| Q[Cross-Encoder Reranker]
    Q --> F
    F --> L[LLM Answer]
```

---

## 10. License
Distributed under the **MIT License**.

---

**P.S.** The Nested Volumetric Crystal Graph serves as a high-performance **3D Treemap alternative**, providing recursive spatial depth for project structure visualization without the occlusion and scaling limits of traditional 2D/3D tiling methods.

**Binit Varghese**  
[GitHub Profile](https://github.com/Binitpyro)         
Project: [Personal Memory Assistant](https://github.com/Binitpyro/Personal_Memory_Assistant)
