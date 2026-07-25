# Project Context Master (v0.0.70)
 Reference

This is the comprehensive context document for AI-assisted engineering on this repository.

## 1) Project Identity

- Name: Personal Memory Assistant (PMA)
- Repository: Personal Memory Assistant
- Architecture style: local-first monolith (FastAPI + SQLite + LanceDB + modular React frontend)
- Primary runtime target: Windows local machine (also runnable cross-platform)
- Current stage: stable high-performance implementation

## 2) Core Product Purpose

PMA indexes local folders and enables natural-language retrieval of user knowledge. It blends:

- lexical retrieval (SQLite FTS5)
- semantic retrieval (LanceDB vector cache + SQLite binary embeddings)
- **Profile-First RAG Intelligence**: prioritizes synthesized architectural folder profiles for broad queries
- conversational RAG analysis (Gemini via OAuth, Ollama, LM Studio)
- deterministic metadata-driven responses for inventory/project queries

User outcomes:

- quickly find information across personal and project files
- receive source-backed answers with conversational follow-up support
- inspect storage and usage insights with interactive 3D spatial environments (Crystal Dreamscape) and treemaps

## 3) Tech Stack

Backend:

- Python 3.11+
- FastAPI
- aiosqlite (SQLite with WAL mode)
- sentence-transformers + torch
- lancedb (High-performance vector store)
- httpx + tenacity
- sse-starlette
- pypdf (License-compliant PDF parsing)
- python-docx, openpyxl, python-pptx (Document parsing)
- **rust_core** (Custom PyO3 module for high-speed I/O, parallel text extraction, MFT traversal, and 3D Force-Directed Layout Simulation)   
- **zlib** (Native SQLite bindings for ~70% database reduction)

Frontend:

- Modular React + TypeScript + Vite
- TailwindCSS for glassmorphism theme
- WebGPU (Moment-Based Order Independent Transparency, Compute Shader Culling, Indirect Drawing, Procedural Billboarding)
- ECharts for advanced 2D "TreeSize" fallback
- Lucide-React icons

Desktop packaging:

- Tauri v2 (Canonical shell)
- PyInstaller standalone sidecar (.exe)

## 4) Repository Topology (Functional)

- app/main.py: API composition root, lifecycle and middleware (Graceful Shutdown)
- app/api/: Dedicated semantic routers (`indexing.py`, `search.py`, `insights.py`, `system.py`, `auth.py`, `models.py`)
- app/config.py: central runtime settings
- app/indexing/service.py: **Zero-Loss Streaming Pipeline** (header/chunk/footer worker protocol)
- app/indexing/summarizer.py: **Universal Deep Summaries** (AST/Regex structural mapping)
- app/scanner/scanner.py: portable scanner backend abstraction
- app/scanner/ntfs_mft.py: Windows MFT accelerated scanner
- app/scanner/rust_core/: PyO3 Rust extension module (jwalk parallelism, fast hashes, Barnes-Hut 3D layout simulation)
- app/storage/schema.sql: relational schema + FTS + profile/facts tables
- app/storage/db.py: all persistence operations and migration logic
- app/vector_store/lancedb_client.py: LanceDB abstraction with thread-safe writes
- app/embeddings/service.py: model load and embedding generation
- app/search/retrieval.py: query planning, fast-path logic, hybrid retrieval, **Profile-First Context**, RRF, rerank, LLM orchestration
- app/search/context_builder.py: prompt context assembly (Categorized headers)
- app/search/llm_client.py: Gemini (Header-based auth), Ollama, LM Studio integration
- app/search/reranker.py: cross-encoder reranking (Background preloading)
- app/insights/service.py: storage analytics
- app/utils/metrics.py: stage-level latency tracking
- frontend/: Modular React application with Vite, TailwindCSS, and TanStack Query
- scripts/: Build and maintenance utilities (backfill, reindex, build_exe)
- tests/: baseline API/scanner tests

## 5) Data Model (SQLite)

Core tables:

1. files

- path unique
- size
- modified_at
- type
- folder_tag
- usage_count
- **summary** (Now holds Structural Metadata Maps)

2. chunks

- file_id FK to files
- offsets
- text_preview (Compressed as zlib BLOB)

3. chunk_embeddings (Source of Truth)

- chunk_id FK to chunks
- embedding (float16 binary BLOB)

4. chunk_fts (FTS5 virtual table)

- indexed chunk text (content='')
- sync maintained by triggers with transparent zlib decompression

5. query_history

- question
- answer
- source_count
- latency_ms
- created_at

6. folder_profiles

- per indexed root/folder synthesized project profile
- project_type and summary metadata

## 6) Vector Model (LanceDB)

Split-Brain caching strategy:

- **Source of Truth**: SQLite `chunk_embeddings` table (preserves portability).
- **Host Cache**: LanceDB `pma_chunks` and `pma_summaries` tables (high-performance vector search).
- **Synchronization**: **Differential Vector Sync** (queries latest ID from LanceDB).

Metadata keys commonly used:

- file_path
- folder_tag
- chunk_id
- project_type
- is_folder_profile

## 7) Indexing Mechanics

Process summary:

1. Input folder normalization and overlap elimination.
2. Scan via NTFS MFT or rust_jwalk.
3. Incremental classification by modified_at.
4. **Pipelined Extraction**: `Extractor -> Chunker -> Embedder -> Storer` as a continuous stream.
5. **Universal Deep Summary**: Generates functional blueprints (AST classes/functions, PDF titles, Spreadsheet headers).
6. **Zero-Loss Streaming**: Indexes 10GB files with fixed 60MB RAM via Python generators.
7. SQLite upsert (files, chunks, chunk_embeddings).
8. LanceDB cache synchronization.
9. Folder profile synthesis and embedding.

Operational characteristics:

- indexing_lock prevents concurrent index runs
- progress object drives /index/status and SSE stream
- **Zip-Bomb Protection**: Streams internal ZIP members with size guards.
- JSON extraction is resilient and size-aware.

## 8) Retrieval and Answering

A) Deterministic path (no LLM)

- trigger: inventory/project query heuristics
- sources: DB aggregate metadata and profiles
- benefit: low latency and predictable output

B) Full RAG path

- query embedding
- **Profile-First Retrieval**: Concurrent search for relevant folder profiles.
- FTS keyword retrieval
- LanceDB semantic retrieval
- LRU caching for retrieval results and RAG answers
- RRF merge
- optional cross-encoder rerank
- **Structured Context Build**: Partitioned into `PROJECT ARCHITECTURE` and `IMPLEMENTATION DETAILS`.
- LLM generation (Strict header-based auth for Gemini).

## 9) API Surface Snapshot

Main endpoints:

- GET /health (includes `split_brain_sync_status`)
- GET /
- POST /index/start
- GET /index/status
- GET /index/progress-stream
- POST /index/reindex (Modernized for LanceDB)
- POST /index/cleanup
- POST /index/clear
- POST /query
- POST /query/stream
- GET /insights
- GET /files/tree
- GET /pick/folder
- GET /system/info
- GET /system/metrics

## 10) Runtime and Configuration

Settings in app/config.py (env-prefixed PMA\_):

- storage: db_path, schema_path, lancedb_persist_dir, lancedb_mode (portable/split_brain)
- embeddings: embedding_model, embedding_batch_size
- indexing: chunk_size, chunk_overlap, max_file_size_bytes, index_concurrency, supported_extensions
- llm: gemini_api_key, gemini_model, gemini_timeout, ollama_url, ollama_model, ollama_timeout, **lm_studio_url**
- retrieval: rrf weights, retrieval_top_k, context_max_tokens, summary_boost_factor

## 11) Performance and Scalability Notes

Strengths:

- **Streaming Pipeline**: Constant memory usage during indexing.
- **Differential Vector Sync**: High-speed boot synchronization.
- **WebGPU Adler32 Hashing**: 10x faster visualizer data stream generation.
- **Reranker Preloading**: Background model load at startup.

Current bottlenecks:

- LLM round-trip latency for semantic questions
- Semantic deduplication logic complexity (N^2 check)

## 12) Testing Posture

Existing tests:

- tests/test_main.py: basic endpoint availability and validation checks
- tests/test_summarizer.py: **Coverage for multi-format deep summaries**
- tests/test_indexing_service_extended.py: coverage for pipelined indexing workers
- tests/test_db_manager_extended.py: coverage for split-brain persistence

## 13) Security and Privacy Considerations

- **API Key Hardening**: Secrets sent only in HTTP headers, never in URLs.
- **Zip-Bomb Guard**: Prevents resource exhaustion from malicious documents.
- **Local Isolation**: Strict CORS (localhost/tauri) and security headers.
- **Prompt Isolation**: Query delimiters (`<user_query>`) protect against injection.

## 14) AI Assistant Working Guidance

When extending this codebase:

1. Preserve local-first assumptions unless explicitly changed.
2. Prefer deterministic answer path for metadata questions.
3. **Pipelined Consistency**: Ensure new extractors support the `extract_stream` generator protocol.
4. Maintain `PROJECT ARCHITECTURE` vs `IMPLEMENTATION DETAILS` separation in context building.
5. Update docs in this folder whenever contracts or behavior changes.

## 15) Technical Debt & Next Milestones

1. Quantize Cross-Encoder reranker to ONNX for 5x speedup.
2. Implement semantic search bar in Explorer tree.
3. Add global Tauri search shortcut (Spotlight-style).

# AI Assistant Playbook for This Repository

This playbook is designed for coding agents and copilots to work safely and effectively in this project.

## 1) Primary Engineering Principles

1. Preserve local-first behavior by default.
2. Prefer deterministic metadata answers before invoking LLM where appropriate.
3. Keep retrieval cost proportional to question complexity.
4. Maintain compatibility of existing API contracts unless explicitly versioning.
5. Update docs in PMA Obsidian whenever behavior or contracts change.

## 2) What to Read First

Read in this order:

1. PROJECT_CONTEXT_MASTER.md
2. ARCHITECTURE.md
3. INDEXING_AND_RAG.md
4. API_SPEC.md
5. IMPLEMENTATION_PLAN.md
6. PERFORMANCE_BOTTLENECKS_AND_ROADMAP.md

## 3) Typical Change Workflows

### A) New retrieval behavior

1. Update app/search/retrieval.py.
2. If context shape changes, update app/search/context_builder.py and app/search/llm_client.py.
3. Update API_SPEC if response behavior changes.
4. Clear retrieval caches via `clear_retrieval_cache()` if logic changes.
5. Add/update tests where practical.

### B) New indexed metadata

1. Add schema.sql table/index.
2. Add db.py migration and CRUD methods.
3. Integrate indexing and/or import endpoint in main.py.
4. Integrate into retrieval fast-path and/or RAG context.

### C) Performance optimization

1. Capture baseline latency from `GET /system/metrics`.
2. Implement change in smallest safe step.
3. Re-measure p50/p95 and validate no quality regression.
4. Document before/after in PERFORMANCE_BOTTLENECKS_AND_ROADMAP.md.

