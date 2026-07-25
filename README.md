# Personal Memory Assistant (PMA)
### Local-First Semantic Search & Intelligence Engine

[![Version](https://img.shields.io/badge/Version-0.0.71-blue?style=flat-square)](https://github.com/Binitpyro/Personal_Memory_Assistant)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Rust](https://img.shields.io/badge/Rust-1.75+-brown?style=flat-square&logo=rust&logoColor=white)](https://www.rust-lang.org/)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev/)
[![Tauri](https://img.shields.io/badge/Tauri-v2-FFC107?style=flat-square&logo=tauri&logoColor=black)](https://tauri.app/)

Personal Memory Assistant (PMA) is a high-performance, local-first search and retrieval system. It indexes your documents and projects with near-zero latency, allowing you to perform semantic queries and gain structural insights without your data ever leaving your machine.

---

## 1. Project Structure

```text
├── app/                # Python Backend (FastAPI, Extraction, RAG)
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
    - **Vector Store**: LanceDB (High-performance O(1) semantic retrieval)
- **Extraction**: Rust-powered parallel file walker & stream extractor
- **Models**: ONNX Runtime and tokenizers for local embedding generation
- **AI Providers**: Gemini, OpenAI, Anthropic, Groq, OpenRouter, Ollama, LM Studio, and OpenAI-compatible endpoints
- **Spatial Engine**: WebGPU-powered GPGPU compute for real-time indexing & Volumetric Visualization

---

## 3. Getting Started

### 3.1 Prerequisites
- **Python 3.12+** (Managed via `uv` recommended)
- **Node.js 20+**
- **Rust Toolchain** (Latest stable)

### 3.2 Quick Start (Windows)
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

### 3.3 Manual Installation (Cross-Platform)
1. **Initialize Backend**:
   ```bash
   uv sync --all-extras
   cd app/scanner/rust_core && maturin develop --release
   ```
2. **Initialize Frontend**:
   ```bash
   cd frontend && npm install && npm run build
   ```

### 3.4 Credentials

PMA reads configuration from `.env` and checks the operating system keyring for
provider API keys. Enter API keys during onboarding or in **Settings → Providers**;
PMA stores those keys in the OS keyring by default. A provider API key set in `.env`
takes precedence and is managed outside the UI, so it cannot be changed or deleted
from Settings.

---

## 4. Development & Automation Scripts

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

To run the frontend test suite directly:

```powershell
cd frontend
npm run test
```

---

## 5. Configuration

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

See [`.env.example`](.env.example) for all supported provider and indexing settings.

---

## 6. Packaging

Tauri is the supported desktop distribution path. Build the Windows MSI installer with:

```powershell
cd frontend
npm run tauri build
```

`Build-Exe.bat` still creates a PyInstaller sidecar executable, but that standalone
EXE workflow is temporarily on hold and should not be used as a current release artifact.

---

## 7. Architecture

### 7.1 Ingestion Pipeline
The system reads files in streams to ensure scalability without exceeding memory limits.

```mermaid
graph LR
    subgraph "Scanning"
        A[File System] --> B[File Scanner]
    end
    
    subgraph "Processing"
        B --> C[Text Extractor]
        C --> D[SQLite Database]
        C --> E[Chunker]
    end
    
    subgraph "Indexing"
        E --> F[AI Embedder]
        F --> G[LanceDB Vector Store]
        E --> H[FTS5 Keyword Index]
    end
```

### 7.2 Unified Search Flow
The system uses **Reciprocal Rank Fusion (RRF)** to combine traditional keyword matching with deep semantic search.

```mermaid
graph LR
    A[Query] --> B[Unified Search]
    B -->|Keywords| C[SQLite FTS5]
    B -->|Meaning| D[LanceDB Vector]
    C --> E[RRF Ranker]
    D --> E
    E --> F[Contextual Results]
```

---

## 8. License
Distributed under the **MIT License**.

---

**P.S.** The Nested Volumetric Crystal Graph serves as a high-performance **3D Treemap alternative**, providing recursive spatial depth for project structure visualization without the occlusion and scaling limits of traditional 2D/3D tiling methods.

**Binit Varghese**  
[GitHub Profile](https://github.com/Binitpyro)         
Project: [Personal Memory Assistant](https://github.com/Binitpyro/Personal_Memory_Assistant) 
