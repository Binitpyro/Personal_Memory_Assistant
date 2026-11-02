# Architecture (Current Implementation)

This document is the source-of-truth architecture description for the current application state. Last checked against source: 2026-10-14, `UI/UX` at `87f7e66`.

---

## 1. System Architecture

PMA (Personal Memory Assistant) is a local-first, high-performance desktop retrieval engine. It operates as a single-user monolith, prioritizing low-latency query resolution and secure, private data handling.

## 1) High-Level Flow

1.  **Extraction (Zero-Loss Streaming)**: The system scans local directories. It uses a **Pipelined Worker Architecture** that extracts, chunks, and embeds data in a continuous stream. Peak memory is bounded by the tunables (`index_concurrency`, `embedding_batch_size`, `embedding_batch_char_budget`), not by file size: 535-632 MB above idle measured on real corpora (see `01_PROJECT_CONTEXT.md` §11).
2.  **Clustering & Storage**: Text is split into semantically relevant chunks. Metadata and **binary embeddings** are stored in **SQLite**, while a high-performance **vector cache** is maintained in **LanceDB**.
3.  **Routing**: The query planner sends whole-collection inventory and project questions to deterministic metadata answers, code relationship questions to the knowledge graph, and everything else to full RAG.
4.  **Retrieval (Hybrid & Profile-First)**: FTS5 (SQLite), semantic chunks and document summaries (LanceDB) fused by RRF, then a cross-encoder reranker, alongside synthesized **Folder Profiles**.
5.  **Synthesis**: Results are context-injected into a local or cloud LLM through the 9-provider abstraction (local-first by default; cloud needs explicit consent). The context budget comes from the model's size class, capped at the model's reported window.

---

## 2. Runtime Topology & Deployment

**Production (Desktop):** Tauri v2 sidecar architecture. The release pipeline produces both a unified desktop app and a standalone sidecar.

### 2.1 Release Artifacts
1. **Tauri MSI (`PMA-v...-windows-x64.msi`)**: Full desktop app. Includes the React frontend natively, the Rust system tray/windowing, and the bundled PyInstaller sidecar executable.
2. **Standalone Zip (`PMA-v...-sidecar.zip`)**: Headless backend release built via PyInstaller (`PMA.exe`) packaged with static frontend assets, suitable for CLI/remote deployment.

### 2.2 Execution Components
1. **Tauri Shell** (`frontend/src-tauri`) â€” Compiled Rust binary. Manages the native OS window and the sidecar lifecycle.
   - **Concurrency Safety**: Background tasks (stdout/stderr draining) are offloaded to dedicated threads to prevent async runtime starvation.
   - **Lifecycle Management**: Securely passes session tokens and manages Python process termination via Windows Job Objects.
2. **Python Sidecar** (`app/main.py` -> `PMA.exe`) â€” FastAPI application bundled via PyInstaller (`--onedir`).
   - **Standalone Support**: Uses PyInstaller resource resolution (`sys._MEIPASS` / absolute pathing) to expose the bundled React frontend if accessed directly without Tauri.
   - **Graceful Shutdown**: All background sync/cleanup tasks are joined before resource closure.
   - **Non-Blocking I/O**: High-latency tasks are offloaded to `asyncio.to_thread`.
3. **SQLite** â€” Source of Truth for metadata + FTS5 full-text index + Permanent vector storage (`chunk_embeddings`).
4. LanceDB â€” High-speed vector search cache (`data/lancedb_data/`).
5. **ONNX Runtime** â€” High-performance CPU-optimized embedding and reranking models (replaced massive PyTorch/SentenceTransformers stack).
6. **Rust Core** (`app/scanner/rust_core`) â€” High-speed I/O, parallel text extraction, 3D Force-Directed Layout simulation.

7. **Unified LLM Client** â€” 9 providers: Ollama, LM Studio, Gemini, Groq, Anthropic, OpenAI, NVIDIA NIM, OpenRouter, OpenAI-compatible.

---

## 3. Security Model

- **Dynamic Session Token**: Verified using timing-attack resistant `compare_digest`.
- **Local Isolation**: a host guard (outermost middleware) rejects any Host outside loopback / `tauri.localhost`; CORS limited to `localhost` and `http://tauri.localhost`.
- **Resource Hardening**: Streaming extractors prevent OOM from ZIP bombs or massive text files.
- **Prompt Isolation**: Uses explicit XML-style delimiters and safety instructions to prevent RAG-based injection.

---

## 4. Major Subsystems

### 4.1 API Layer (app/api/*)
- Separated semantic routers with unified error handling and request-ID telemetry.
- Global security middleware injecting defense-in-depth headers.

### 4.2 Indexing Pipeline (app/indexing/service.py)
1. **Scan:** NTFS MFT (admin) or iterative `rust_jwalk`.
2. **Pipeline:** `Header -> Streaming Extract -> Buffer Chunker -> Batch Embedder -> SQLite/LanceDB Storer`.
3. **Summarization:** `UniversalSummarizer` generates functional blueprints (AST/Regex) instead of text stubs.

### 4.3 Frontend Layer (frontend/src)
Modern React application with Vite and TailwindCSS.
- **Crystal Dreamscape 3D:** Fluid WebGPU navigation utilizing `zlib.adler32` for instant coordinate hashing.
- **Throttled State:** Chat streams are buffered and flushed at 50ms intervals to prevent UI freezes.
- **Virtual DOM Safeguards:** Folder tree rendering is capped at 100 files per directory to prevent DOM bloat.

### 4.4 Retrieval Layer (app/search)
- **Query Planner** (`planner.py`): `FAST_METADATA`, `FAST_PROJECT`, `GRAPH_SEARCH`, `FULL_RAG`; scored against a frozen labelled routing set (`scripts/eval_routing.py`).
- **Profile-First Intelligence:** Parallel retrieval of high-level folder profiles for architectural context.
- **Hybrid RAG:** RRF fusion (k=60) of SQLite FTS5, LanceDB chunk vectors and LanceDB summary vectors, then the ONNX cross-encoder (always on FULL_RAG, 5 s deadline).
- **Categorized Context:** Explicitly separates `PROJECT ARCHITECTURE` from `IMPLEMENTATION DETAILS`.

---

## 5. Performance Characteristics
- **Bounded Indexing RAM:** the old "~60MB peak" figure was never measured and is retracted. Measured: 195.7-248 MB idle, 535-632 MB above idle at ingestion peak (`01_PROJECT_CONTEXT.md` §11).
- **Boot Sync:** Queries latest ID from LanceDB to reduce memory overhead.
- **Zero-Latency Visualization:** optimized binary struct streaming for WebGPU.
- **Background Reranker Preloading:** eliminates cold-start latency for the first user query.

# API Specification

Base URL: `http://127.0.0.1:8000/api`

Standard API calls are prefixed with `/api`. Global middleware enforces security headers and timing-attack resistant token validation.

---

## 1) Health and Application

### GET /api/health
(Also available at `/health`)

Returns runtime health and readiness.

Response 200:
```json
{
  "version": "0.0.72",
  "status": "ok|degraded",
  "db": "connected|disconnected",
  "model_ready": true,
  "indexing": "idle|running",
  "split_brain_sync_status": "done|syncing|error",
  "embedding_signature": {},
  "subsystems": {"ocr": {}, "watcher": {}, "reranker": {}}
}
```

### GET /api/system/config
Returns public app metadata for reactive UI (model name, version, embeddings, local LLM URLs).

---

## 2) Indexing APIs

### POST /api/index/start
Starts background **Pipelined Indexing** for provided folders. Uses streaming extractors for constant memory usage.

### GET /api/index/status
Returns current indexing progress and counts.

### GET /api/index/progress-stream
SSE stream for real-time indexing progress with per-file granularity.

---

## 3) Query and Retrieval

### POST /api/query
Primary QA endpoint. Uses **Profile-First** hierarchical retrieval.
**Body:**
```json
{
  "question": "string",
  "file_type": "string|null",
  "folder_tag": "string|null",
  "history": [
    {"role": "user|assistant", "content": "string"}
  ]
}
```

### POST /api/query/stream
Streaming version (SSE) of the QA endpoint. Frontend implements a 50ms throttle buffer for render performance.

---

## 4) Explorer and Insights

### GET /api/files/tree
Returns hierarchical file data. Folder nodes are optimized for large-scale rendering.

### GET /api/insights
Returns global storage statistics and **Functional Blueprints** (Deep Summaries).

### GET /api/visualizer/stream
Returns binary struct chunks representing files in 3D space. **10x Optimization:** Uses Adler32 hashing for high-speed spatial coordinate generation.

---

## 5) System and Security

### Global Middleware (Request/Response)
- **Authentication:** Timing-attack safe comparison (`compare_digest`).
- **Isolation:** CORS limited to `localhost` and `tauri` origins.
- **Headers:**
    - `X-Content-Type-Options: nosniff`
    - `X-Frame-Options: DENY`
    - `Referrer-Policy: strict-origin-when-cross-origin`

### POST /api/system/compact-db
Triggers a background SQLite `VACUUM`. Monitored via the graceful shutdown join sequence.

---

## 6) Error Contract

### Validation Error (422)
```json
{
  "error": "Validation error",
  "detail": [...]
}
```

### Unauthorized (401)
```json
{
  "error": "Unauthorized local access."
}
```

# Indexing and RAG Internals (Current Implementation)

This document captures concrete implementation behavior and practical tuning guidance for the zero-loss streaming pipeline.

---

## 1) Zero-Loss Streaming Pipeline

Entry point: `IndexingService.index_folders`

The indexing core has been re-architected into a **High-Throughput Worker Pipeline** that maintains O(1) memory usage by streaming data fragments instead of loading whole files.

### Pipelined Workflow:
1.  **Header Generation**: The `Extractor` identifies the file, calculates its SHA-256, and emits a `header` message to create the database record immediately.
2.  **Streaming Extraction**: Specialized generators (`extract_stream`) read the file in 128KB fragments.
3.  **Buffer Chunking**: The `StreamChunker` aggregates fragments and snaps to sentence boundaries, yielding properly sized vector chunks.
4.  **Global Batch Embedding**: The `Embedder` worker collects chunks from *all* active extraction tasks into length-sorted batches capped at 64 rows and at `embedding_batch_char_budget` (10240) for `rows × widest row`, which is what bounds peak memory.
5.  **Transactional Storage**: The `Storer` worker performs atomic bulk inserts into SQLite and LanceDB, finalizing with a `footer` message that updates the file's summary and stats.

---

## 2) Text Extraction Layer

Extraction operates in a memory-safe streaming mode:

- **Tier 1 (Parallel Rust)**: Primary layer for standard text, code, and logs. Uses `rayon` for massive I/O parallelism.
- **Tier 2 (Streaming Python)**: Used for complex formats.
    - **PDF**: Page-by-page text yielding via `pypdf`.
    - **CSV/XLSX**: Row-by-row streaming with header mapping.
    - **Docx/EPUB**: ZIP-streamed extraction.

### Safety Guards:
- **Zip-Bomb Protection**: Uses `zf.open().read(buffer)` to strictly limit the memory consumed by any internal archive member.
- **AST Safety**: Large code files (>10MB) drop down to plain-text streaming to prevent AST parser crashes.

---

## 3) Universal Deep Summaries

Instead of simple text stubs, the system now generates **Functional Blueprints**:
- **Python**: Uses `ast` to map top-level Classes, Functions, and Docstrings.
- **Code (JS/TS/RS/GO)**: Regex-based symbol extraction for exports and public definitions.
- **Spreadsheets**: Extracts Sheet Names and Column Headers.
- **Presentations**: Extracts Slide Titles (Outline).
- **Data (JSON/YAML)**: Maps top-level keys and collection counts.

Summaries are integrated into the `PROJECT ARCHITECTURE` section of the RAG context.

---

## 4) Split-Brain Caching (Vector Store)

- **Source of Truth**: SQLite `chunk_embeddings` (Binary BLOBs).
- **Search Cache**: LanceDB `pma_chunks`.
- **Differential Sync Strategy**: At startup, the backend queries the `MAX(chunk_id)` in LanceDB and only streams missing records from SQLite.

---

## 5) Retrieval Pipeline (Profile-First Mode)

Step 0: **Planning** — `QueryPlanner` picks the mode; only `FULL_RAG` reaches the steps below.

Step 1: **Parallel Retrieval Branches**
- **Folder Profile Search**: Concurrent semantic search for the top 2 relevant synthesized folder profiles.
- **Hybrid Retrieval**: RRF fusion of FTS5 (SQLite), chunk semantic search and summary search (LanceDB), then cross-encoder reranking.

Step 2: **Categorized Context Assembly**
The `ContextBuilder` partitions the LLM input into explicit semantic layers:
1.  **Project Architecture**: High-level folder profiles providing architectural oversight.
2.  **Implementation Details**: Granular code/text chunks providing technical proof.

Step 3: **LLM Generation**
- Strict header-based authentication for Gemini to prevent secret leakage.
- 50ms throttled frontend display for buttery-smooth streaming.

---

## 6) Tuning Knobs (app/config.py)

- `max_file_size_bytes`: hard limit for individual file extraction.
- `index_concurrency`: number of parallel extraction workers.
- `lm_studio_url`: configurable local LLM endpoint.
- `supported_extensions`: module-level cached set for fast filtering.
