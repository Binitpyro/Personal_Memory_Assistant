# Project Context Master (v0.0.72 on `updates`, unreleased; v0.0.71 is what shipped)

Reference document for AI-assisted engineering on this repository.

**Last verified against source: 2026-08-20, commit `1e4a6d0`, branch `updates`.** Anything here that a source read contradicts is stale — re-verify before acting on it.

**Branch standpoint.** The released baseline is `main` at `7c16249` (2026-07-25), which is v0.0.71. `updates` is **55 commits ahead** of it and **2 behind** — the branches have not been merged since PR #9. `main`'s README improvements were brought across by hand and extended, so a merge should take this branch's README. `origin/updates` is at `b3cdd59`; **13 commits are unpushed**. `main` contains no `app/ocr/` at all. The full delta is written up in `05_CHANGELOGS.md`.

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
- conversational RAG analysis through a 9-provider abstraction (Ollama, LM Studio, Gemini, Groq, Anthropic, OpenAI, NVIDIA NIM, OpenRouter, OpenAI-compatible), local-first by default
- deterministic metadata-driven responses for inventory/project queries

User outcomes:

- quickly find information across personal and project files
- receive source-backed answers with conversational follow-up support
- inspect storage and usage insights with interactive 3D spatial environments (Crystal Dreamscape) and treemaps

## 3) Tech Stack

Backend:

- Python >= 3.12 (`pyproject.toml`)
- FastAPI + uvicorn; slowapi for endpoint rate limiting
- aiosqlite (SQLite with WAL mode)
- **onnxruntime + tokenizers** for both embeddings and reranking. There is no torch and no sentence-transformers dependency — both went in the 0.0.70 ONNX migration.
- lancedb (host-side vector cache) + pyarrow
- httpx + tenacity
- sse-starlette
- pypdf (PDF text), pypdfium2==4.30.0 (page rasterization for OCR), pillow
- python-docx, openpyxl, xlrd (documents and spreadsheets)
- PPTX is read directly from the OOXML parts with `zipfile` + `xml.etree`; python-pptx is still declared but is now only used by `scripts/generate_perf_corpus.py`
- tiktoken (context-budget token counting), nltk, keyring, PyYAML
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
- app/api/: Dedicated semantic routers (`indexing.py`, `search.py`, `insights.py`, `system.py`, `models.py`, `providers.py`, `modules.py`, `telemetry.py`, `debug.py`), plus `deps.py` (singleton accessors) and `limiter.py`. There is no `auth.py`.
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
- app/indexing/extractors/: per-format streaming extractors (pdf, docx, pptx, xlsx, csv, json, epub) behind `_ooxml_guard.py`
- app/ocr/: OCR subsystem — durable SQLite queue (`queue.py`), page cache (`cache.py`), NATIVE/OCR detection gate (`gate.py`), tier registry and provisioning (`registry.py`), subprocess worker (`worker/`), vision-model path (`vlm_engine.py`, `raster_png.py`)
- app/providers/: 9-provider LLM abstraction (Ollama, LM Studio, Gemini, Groq, Anthropic, OpenAI, NVIDIA NIM, OpenRouter, OpenAI-compatible), `launcher.py` for starting local providers, `vision.py` for VLM image messages
- app/settings_store.py: the single reader/writer for `settings.json`
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
- folder_tag — the indexed folder's **basename** only. Not a path; two folders sharing a basename share a tag.
- **root_path** — the full path of the indexed root. Added by additive migration; rows predating it carry `''` and fall back to a shared directory prefix. Keys `/files/tree`.
- **sha256** — content digest, and the OCR cache's `content_key`. Also carries the re-attempt states `''` / `ERROR` / `CANCELLED` / `NOCONTENT`.
- **extract_status** — why a file produced no chunks: `''`, `binary`, `unreadable`, `encrypted`, `ocr_pending`, `nocontent`, `empty`, `error`, `cancelled`. Populated and queryable; no API or UI surface yet.
- usage_count
- **summary** (holds Structural Metadata Maps)

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
6. **Streaming**: Indexes large files without ever holding one in memory, via Python generators. Measured 2026-08-20: **195.7 MB idle, 535.3 MB above idle at ingestion peak**. The older "fixed 60MB RAM" figure is retracted — it was never measured.
7. SQLite upsert (files, chunks, chunk_embeddings).
8. LanceDB cache synchronization.
9. Folder profile synthesis and embedding.

Operational characteristics:

- indexing_lock prevents concurrent index runs
- progress object drives /index/status and SSE stream
- **Zip-Bomb Protection**: Streams internal ZIP members with size guards; OOXML parts declaring a DTD are refused outright, which closes the entity-expansion vector the size guard cannot see.
- JSON extraction is resilient and size-aware.
- **Completeness invariant**: a file is never recorded as complete unless it produced content. Zero chunks from a non-empty, non-stub file yields `NOCONTENT` and is re-attempted on the next run. A 0-byte file keeps its real hash and is not retried.
- **Extractor stubs never reach the index**: `[BINARY:`, `[UNREADABLE:` and `[ENCRYPTED` are filtered rather than chunked and embedded as document content.
- Pages a PDF defers to OCR are enqueued on the durable OCR queue and indexed later, transactionally isolated in a SAVEPOINT so the write cannot commit or discard the indexer's own transaction.

## 8) Retrieval and Answering

A) Deterministic path (no LLM)

- trigger: inventory/project query heuristics
- sources: DB aggregate metadata and profiles
- benefit: low latency and predictable output

B) Full RAG path

- query embedding
- **Profile-First Retrieval**: Concurrent search for relevant folder profiles.
- FTS keyword retrieval (`rrf_fts_weight` 0.4)
- LanceDB semantic retrieval (`rrf_semantic_weight` 0.6)
- LanceDB **summary-vector** retrieval over `pma_summaries` (`rrf_summary_weight` 0.05). This leg shipped at 0.3 and cost recall against not using it at all; see `05_CHANGELOGS.md`.
- LRU caching for retrieval results and RAG answers
- RRF merge (`rrf_k` 60), with ties broken on chunk id so fusion is deterministic across index builds of the same corpus
- optional cross-encoder rerank
- **Structured Context Build**: Partitioned into `PROJECT ARCHITECTURE` and `IMPLEMENTATION DETAILS`.
- LLM generation (Strict header-based auth for Gemini).

## 9) API Surface Snapshot

Everything below is mounted under `/api` (`app/main.py`). `GET /health` and `GET /api/index/progress-stream` are the only token-exempt paths.

- GET /health — `status` (`model_ready and db_ok`), `split_brain_sync_status`, and `subsystems` (`up | down | disabled | unknown` for OCR, the folder watcher and the reranker)
- Indexing: POST /index/start, POST /index/cancel, GET /index/status, GET /index/progress-stream, POST /index/cleanup, POST /index/clear, GET /index/export, POST /index/folder/remove
- Search: POST /query, POST /query/stream, GET /query/history, POST /query/history/clear
- Insights: GET /insights, GET /insights/by-type, GET /insights/portrait, GET /files/tree, GET /visualizer/stream, GET /visualizer/meta
- System: GET /system/info, GET /system/config, GET /system/metrics, GET /system/drive_info, POST /system/enable-split-brain, POST /system/purge-host-cache, POST /system/compact-db (+ /status), POST /system/clear-cache, GET /pick/folder
- LLM: GET /llm/preferences, POST /llm/preferences, GET /llm/detect, POST /llm/chat
- Providers: GET|PUT /providers/settings, GET /providers, GET /providers/current, POST /providers/{id}/validate, POST /providers/{id}/self_test, GET /providers/{id}/launch_status, POST /providers/{id}/launch, PUT|POST|DELETE /providers/{id}/key, PUT /providers/{id}/default_model
- OCR: GET /ocr/status, GET /ocr/tiers, POST /ocr/select, POST /ocr/enable, POST /ocr/install (+ /status, /cancel), POST /ocr/uninstall, POST /ocr/resume, GET /ocr/queue, POST /ocr/retry, POST /ocr/force, POST /ocr/queue/clear, DELETE /ocr/cache, GET /ocr/vlm/models, POST /ocr/vlm/select, GET /ocr/vlm/selection
- Telemetry: GET /telemetry/metrics

## 10) Runtime and Configuration

Settings in app/config.py (env-prefixed PMA\_):

- storage: db_path, schema_path, lancedb_persist_dir, lancedb_mode (`portable` by default; `split_brain` enables the SQLite embedding backup)
- embeddings: embedding_model (`BAAI/bge-small-en-v1.5`), embedding_batch_size (64, the row cap), **embedding_batch_char_budget** (10240 — caps `rows × width-of-widest-row`, which is what actually bounds peak memory; the row cap only ever narrows it)
- indexing: chunk_size (512 characters), chunk_overlap, max_file_size_bytes, index_concurrency (16), supported_extensions
- ocr: ocr_enabled (**False** by default), ocr_conf_floor, ocr_max_attempts, ocr_worker_idle_timeout_s, ocr_vlm_doc_timeout_s
- llm: per-provider keys, models, base URLs and timeouts across all nine providers, plus cloud_privacy_consent
- retrieval: rrf_fts_weight (0.4), rrf_semantic_weight (0.6), rrf_summary_weight (0.05), rrf_k (60), rrf_score_scale, retrieval_top_k (15), context_max_tokens (8000), summary_expand_chunks_per_file (5), query_stream_timeout_s (180)
- `summary_boost_factor` no longer exists; the summary signal is a weighted RRF leg, not a multiplier.

## 11) Performance and Scalability Notes

Strengths:

- **Streaming Pipeline**: no document is ever held whole in memory.
- **Token-budget batching**: peak embedding memory tracks a fixed token budget rather than a row count, which cut ingestion peak 56% on a real corpus while *raising* throughput 16%.
- **Differential Vector Sync**: High-speed boot synchronization.
- **Reranker Preloading**: Background ONNX model load at startup.

Measured budget (2026-08-20, `scripts/profile_ingest_memory.py`, `GetProcessMemoryInfo` sampled at 5 Hz — not `tracemalloc`, which cannot see the ONNX arena):

| Constraint | Kind | Measured | Bound |
|---|---|---|---|
| Idle / serving queries | hard ceiling | 195.7 MB | 250 MB |
| Ingestion, peak above idle | transient cap | 535.3 MB | 1 GB |
| OCR worker rasterization arrays | hard ceiling | 76.3 MB @ 20 MP | 100 MB |

Current bottlenecks:

- LLM round-trip latency for semantic questions
- **The boundedness invariant is not satisfied.** Ingestion peak should depend only on the tunables (`index_concurrency`, `embedding_batch_size`, `max_length`) and on nothing about the corpus. Two corpora still differ.
- OCR Tier 2 (DirectML) measures ~5.2 GB VRAM, above the ~4 GB design target.

## 12) Testing Posture

**Verification goes through the two batch scripts, not bare `pytest`.** Bare `pytest` is roughly a third of the gate.

- `scripts\run_ci_checks.bat` — `uv sync --all-extras` → `maturin develop --release` → `ruff check` → `ruff format --check` → `mypy` → `pytest tests/` → `bandit` → `eslint` → `cargo clippy`/`deny`/`audit` for both Rust crates.
- `scripts\Run-Tests.bat` — pytest with coverage → `cargo test` for both crates → Miri → vitest with coverage → Playwright E2E.

Notes that matter:

- **Never run `uv sync` on its own** — it uninstalls the compiled `rust_core` extension. `run_ci_checks.bat` is safe only because `maturin develop --release` immediately follows.
- Neither gate can run while the dev backend is up: `uv sync` cannot replace `rust_core.pyd` while uvicorn holds it.
- The eval suite is marked `-m eval` and is **deselected from both gates**. A failing eval assertion can therefore sit unnoticed indefinitely — this has already happened twice.
- Playwright runs against the Vite dev server, so anything served only by FastAPI (response headers included) is invisible to E2E.
- Tests must be deterministic, offline, and must not download models.

## 13) Security and Privacy Considerations

- **API Key Hardening**: Secrets sent only in HTTP headers, never in URLs. Provider URLs and embedded credentials are kept out of client-facing error bodies.
- **Local access token is header-only**: `X-Local-Access-Token`, compared with `secrets.compare_digest`. The `?token=` query fallback was removed — query strings reach access logs, browser history and `Referer`.
- **Rate limits on destructive endpoints**: `/index/start`, `/clear`, `/cleanup`, `/export`, `/folder/remove`. `/index/status` is deliberately unlimited because the Library page polls it every 10 s.
- **Content-Security-Policy** on browser-served HTML, with a per-request `script-src` nonce. Governs the browser path only; Tauri serves the SPA from its own bundle under `tauri.conf.json`.
- **Model output is sanitised**: `rehype-sanitize` runs after `rehype-raw` in the chat renderer, with a schema extending the GitHub default plus the `claim`/`inference` grounding tags. Without it, a poisoned corpus chunk can steer the model into emitting HTML that executes in a page holding the API token. **The threat model includes the indexed documents themselves.**
- **Cloud privacy consent** gates on the *resolved destination*, not the provider's declared kind, so a provider registered "local" but pointed at a remote host is not exempt.
- **Zip-Bomb and XML-bomb guards**: streamed ZIP members with size guards, and OOXML parts declaring a DTD are refused.
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

1. Satisfy the ingestion boundedness invariant — peak must not track corpus shape.
2. Surface `files.extract_status` in the API and Library UI; it is written but invisible.
3. Grow the retrieval eval corpus past 12 queries / 24 documents. It is saturated at k=5 and cannot demonstrate an improvement, only a regression.
4. Implement semantic search bar in Explorer tree.
5. Add global Tauri search shortcut (Spotlight-style).

Done, do not re-propose: the cross-encoder reranker is already ONNX (`app/search/reranker.py`).

# AI Assistant Playbook for This Repository

This playbook is designed for coding agents and copilots to work safely and effectively in this project.

## 1) Primary Engineering Principles

1. Preserve local-first behavior by default.
2. Prefer deterministic metadata answers before invoking LLM where appropriate.
3. Keep retrieval cost proportional to question complexity.
4. Maintain compatibility of existing API contracts unless explicitly versioning.
5. Update docs in PMA Obsidian whenever behavior or contracts change.

## 2) What to Read First

Read in this order (these are the files that actually exist in this folder):

1. `01_PROJECT_CONTEXT.md` — this document
2. `02_ARCHITECTURE_AND_CORE.md`
3. `05_CHANGELOGS.md` — what changed and, more usefully, why
4. `PERFORMANCE_BOTTLENECKS_AND_ROADMAP.md`
5. `03_ROADMAP_AND_VISION.md`
6. `06_DESIGN_SYSTEM.md` — the frontend design system, its measured contrast
   table, and the constraints that bite. Read before touching anything under
   `frontend/src/`.

`.claude/CLAUDE.md` at the repo root carries the working rules and the current defect state. It is untracked, so it is a state file rather than history.

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

