# Personal Memory Assistant (PMA)
### Professional-Grade, Local-First Knowledge Retrieval Engine

[![Version](https://img.shields.io/badge/Version-0.0.69-blue?style=flat-square)](https://github.com/Binitpyro/Personal_Memory_Assistant)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Runtime](https://img.shields.io/badge/Tauri-v2-FFC107?style=flat-square&logo=tauri&logoColor=black)](https://tauri.app/)
[![Database](https://img.shields.io/badge/LanceDB-Vector--Cache-000000?style=flat-square)](https://lancedb.com/)
[![License](https://img.shields.io/badge/License-MIT-success?style=flat-square)](https://opensource.org/licenses/MIT)

## 1. System Overview

Personal Memory Assistant (PMA) is a high-performance, local-first RAG (Retrieval-Augmented Generation) system designed for absolute data sovereignty. It transforms unindexed local folders into a semantically aware knowledge graph without data ever leaving your hardware.

---

## 2. Architecture & Data Flow

### 2.1 Ingestion (Zero-Loss Pipeline)

```mermaid
graph TD
    A[File System] -->|MFT / rust_jwalk| B[Streaming Extractor]
    B -->|Message: Header| C[(SQLite Metadata)]
    B -->|128KB Fragments| D[Stream Chunker]
    D -->|Sentence Snap| E[Batch Embedder]
    E -->|float16 Vectors| F[(LanceDB Cache)]
    B -->|Message: Footer| C
```

### 2.2 Query Engine (Profile-First)

```mermaid
graph TD
    G[User Query] -->|Query Planner| H[Hybrid Retrieval]
    H -->|Lexical FTS5| I[Context Builder]
    H -->|Semantic Vector| I
    H -->|Architecture Profile| I
    I -->|Partitioned XML| J[LLM: Gemini / Qwen]
    J -->|Answer| K[Professional UI]
```

---

## 3. Technology Stack

| Layer | Implementation |
| :--- | :--- |
| **Backend Core** | Python 3.12, FastAPI, Pydantic v2 (Strict Typing) |
| **Native Shell** | Tauri v2 (Rust), PyInstaller Sidecar |
| **High-Speed I/O** | Rust Core (PyO3), jwalk, Rayon Parallelism |
| **Vector Engine** | LanceDB (Differential Boot Sync) |
| **Metadata & FTS** | SQLite (WAL Mode), FTS5 |
| **Neural Search** | Sentence-Transformers (BGE-Small-v1.5) |
| **3D Visualization** | WebGPU (Compute Shader Culling, Adler32 Hashing) |

---

## 4. Getting Started

### 4.1 Prerequisites

- **Python 3.12+**
- **Node.js 20+**
- **Rust Toolchain**
- **LM Studio** (Optional: For local LLM execution)

### 4.2 Installation (Foolproof)

1. **Clone Repository**
   ```bash
   git clone https://github.com/Binitpyro/Personal_Memory_Assistant.git
   cd Personal_Memory_Assistant
   ```

2. **Environment Setup** (Using `uv` for 10x faster dependency resolution)
   ```bash
   uv venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   uv sync
   ```

3. **Frontend & Native Synthesis**
   ```bash
   cd frontend && npm install && npm run build && cd ..
   cd app/scanner/rust_core && maturin develop --release && cd ../../..
   ```

---

## 5. Usage

### 5.1 Data Ingestion (CLI)

Index any local path using the pipelined streamer:

```bash
uv run pma --index "D:/Documents/Research"
```

### 5.2 Intelligent Retrieval (CLI)

Query the system with architectural awareness:

```bash
uv run pma --query "Explain the security model of the streaming pipeline"
```

### 5.3 Desktop Application (Production)

Launch the professional Tauri interface:

```bash
cd frontend
npm run tauri dev
```

---

## 6. License & Contact

Distributed under the **MIT License**.

**Binit Varghese**  
[GitHub Profile](https://github.com/Binitpyro)  
Project: [Personal Memory Assistant](https://github.com/Binitpyro/Personal_Memory_Assistant) 
