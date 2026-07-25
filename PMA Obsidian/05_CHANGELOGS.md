# Changelog v0.0.71

Personal Memory Assistant v0.0.71 represents a major milestone release. This update introduces **Multi-Provider AI model support**, **OS-level keyring credential security**, **Graph RAG architectural intelligence**, a **hardware-accelerated 3D WebGPU visualizer**, **zero-loss streaming ingestion**, and an **extensive test suite overhaul**.

---

## 🌟 Executive Highlights

* **Multi-Provider AI Ecosystem**: Seamlessly switch between OpenAI, Anthropic (Claude), Google Gemini, OpenRouter, and custom local AI models directly within the new Provider Settings window.
* **Enterprise Credential Security**: API keys are now securely encrypted in the Windows OS Keyring instead of plain-text configuration files.
* **Graph RAG & Structural Intelligence**: Understands code dependencies and project architecture using AST-based graph extraction alongside traditional text search.
* **Next-Gen 3D Visualization Engine**: Explores codebases visually at 60 FPS with a new WebGPU instanced rendering engine and WebGL2 fallback support.
* **Zero-Loss Streaming Ingestion**: Processes repositories of any size (even 10GB+) with a constant ~60MB RAM footprint.

---

## 🤖 Multi-Provider AI Engine & Keyring Security

* **Universal AI Model Support**: Integrated dynamic provider switching supporting OpenAI (`gpt-4o`, `gpt-4o-mini`), Anthropic (`claude-3-5-sonnet`), Google Gemini (`gemini-1.5-pro`, `gemini-1.5-flash`), OpenRouter, and generic OpenAI-compatible API endpoints.
* **Windows OS Keyring Storage**: API keys and access tokens are saved directly to the system keyring (`app/api/keyring_service.py`), eliminating plain-text secrets in configuration files and preventing accidental credential exposure.
* **Dedicated AI Providers Interface**: Added an intuitive **Providers Settings Window** (`ProvidersPage.tsx`) complete with real-time key verification, latency sparklines, automated setup recipes, and a guided setup tour.

---

## 🕸️ Graph RAG & AST Code Architecture Intelligence

* **AST Code-Graph Extraction**: Built an Abstract Syntax Tree code parser (`app/indexing/graph_extractor.py`) that extracts classes, functions, and cross-file import relationships across Python, TypeScript, Rust, and multi-language files.
* **Profile-First Graph Retrieval**: Combined high-level folder profiles, technical code snippets, and structural dependency graphs during search retrieval (`app/search/retrieval.py`). The AI can now explain how system components interact with deep architectural context.
* **3D Knowledge Graph Tracer**: Added an interactive trace component (`CrystalGraphTrace.tsx`) that allows users to click and trace code execution paths and file dependencies visually.

---

## 🎨 Hardware-Accelerated 3D Visualizer & Spatial BVH

* **Instanced Mesh WebGPU Pipeline**: Upgraded the 3D codebase visualizer (`WebGPURenderer.ts`) to use GPU instanced mesh rendering, delivering fluid 60 FPS performance when navigating graph nodes.
* **WebGL2 Hardware Fallback**: Added a WebGL2 rendering pipeline (`WebGL2Renderer.ts`) ensuring smooth 3D node exploration on systems without native WebGPU support.
* **Custom WGSL Shaders**: Built custom WebGPU shaders (`bubble.wgsl`, `crystal.wgsl`, `outline.wgsl`, `picking.wgsl`) for volumetric crystal aesthetics, ray-casted node selection, and outline highlights.
* **Linear BVH Spatial Acceleration**: Integrated a Bounding Volume Hierarchy tree (`LinearBVH.ts`) for sub-millisecond node selection and spatial queries.

---

## ⚡ Zero-Loss Streaming Engine & Database Resilience

* **O(1) Fixed Memory Footprint**: Scaled the document extraction and indexing pipeline (`app/indexing/service.py`) to process files of any size with a fixed ~60MB RAM ceiling.
* **Thread Starvation & Deadlock Guards**: Implemented a dedicated disk I/O thread pool and bounded task queues, ensuring file hashing and stat operations never starve machine learning inference threads.
* **Full-Text Search (FTS5) Delta Safety**: Corrected SQLite FTS5 delta tracking and WAL checkpoint handling, preventing data corruption during background index resets and job cancellations.
* **Optimized Rust Core Binary**: Re-compiled the Rust extraction core with high optimization settings (`opt-level=3`, thin LTO, stripped debug symbols) for faster document extraction and smaller binary sizes.

---

## 💬 Responsive Chat Experience & Diagnostic Telemetry

* **50ms State Throttling**: Implemented a state buffer in the chat stream hook (`useChatStream.ts`) to throttle incoming message chunks at 50ms intervals, eliminating browser stutter during rapid responses.
* **Smart UI Controls**: Added claim-capability detection for AI tools, enhanced search filter bars, model picker dropdowns, and message metadata inspect views.
* **Anonymous Health Telemetry**: Added diagnostic endpoints (`app/api/system.py`) to monitor search response latencies and system error rates safely.

---

## 🔒 Security & Defense-In-Depth Hardening

* **Strict HTTP Headers**: Applied essential security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`) and restricted CORS permissions strictly to `localhost` and Tauri desktop origins.
* **Clean Open-Source Licensing**: Removed legacy third-party dependencies and audited all packages to guarantee 100% permissive licensing (MIT, Apache 2.0, BSD).

---

## 🧪 Comprehensive Quality Assurance & Testing Suite

* **250+ Test Coverage Expansion**: Added comprehensive test modules for API providers (`test_llm_client_providers.py`, `test_api_providers.py`), query endpoints (`test_query_endpoints.py`), security robustness (`test_security_and_robustness.py`), context builders, and database managers.
* **Frontend React Component Testing**: Built a complete Vitest suite covering UI pages and components (`ProvidersPage.test.tsx`, `InsightsPage.test.tsx`, `SearchPage.test.tsx`, `LibraryPage.test.tsx`, `MessageBubble.test.tsx`, etc.).
* **Benchmarking & Profiling Tools**: Included automated scripts for ingestion benchmarks (`benchmark_ingestion.py`, `benchmark_full.py`) and memory profiling (`memory_profiler.py`).

---

## 📦 Historical Release Notes

### [0.0.70] - 2026-06-01

#### Machine Learning & Core Overhaul
* **ONNX Runtime Migration**: Transitioned the Machine Learning pipeline (embeddings and reranking) entirely to ONNX Runtime, eliminating the heavy PyTorch dependency, reducing executable size under 100MB, and tripling CPU inference speed.

#### Security & Authentication
* **Fail-Fast Authentication**: Hardened local authentication to fail-fast if security token is missing.
* **Environment Isolation**: Prevented live API keys from being leaked in distributed executables.
* **SQL Injection Guards**: Parameterized vector store client queries to eliminate injection vulnerabilities.

#### Performance & Stability
* **SQLite Read Connection Pool**: Implemented multi-connection read pool for SQLite metadata database.
* **MinHash LSH Deduplication**: Replaced legacy string matching with O(n) MinHash LSH for semantic deduplication.
* **Dedicated I/O Thread Pool**: Prevented file hashing and stat operations from starving machine learning inference tasks.
* **FTS5 Integrity Fixes**: Resolved full-text search index corruption during resets and multi-worker startup bugs.

---

## [0.0.69] - 2026-05-02

### Major Achievement: Zero-Loss Streaming Indexing
The indexing engine has been completely re-architected from a monolithic "load-whole-file" model to a High-Performance Streaming Pipeline.
- Constant Memory Footprint: Processed files of any size (even 10GB+) with a fixed ~60MB RAM usage.
- Pipelined Workers: Parallelized extraction, embedding, and storage stages using a header/chunk/footer message protocol.
- Infinite Scalability: Support for massive datasets on consumer-grade hardware.

### RAG & AI Intelligence Refinements
- Profile-First Retrieval: The search engine now retrieves high-level Folder Profiles in parallel with code chunks, providing the LLM with architectural oversight before implementation details.
- Universal Deep Summaries: Implemented structural metadata mapping for 30+ file formats.
    - Code (PY, TS, RS, etc.): AST-aware and regex-based symbol extraction (Classes, Functions).
    - Documents (PDF, PPTX, Docx): Outline and slide title extraction.
    - Data (JSON, CSV, XLSX): Schema and key mapping.
- Prompt Injection Hardening: Wrapped user queries in <user_query> tags and added explicit safety instructions to the system prompt.

### Security & Hardening
- Zip-Bomb Protection: EPUB and DOCX extractors now use streaming reads with size guards, providing absolute immunity to decompression-based resource exhaustion attacks.
- API Key Security: Gemini client now strictly uses HTTP headers for API key transmission, preventing secrets from leaking into URL logs.
- Local Isolation: Hardened CORS policy to strictly allow localhost and tauri origins.
- Defense-in-Depth: Added X-Content-Type-Options, X-Frame-Options, and Referrer-Policy headers to all API responses.

### Performance & Stability
- Visualizer Optimization: Replaced expensive MD5 hashing with high-speed zlib.adler32 and fixed unindexed SQL queries, resulting in 10x faster WebGPU data loading.
- Graceful Shutdown: Implemented a formal task-join sequence in the FastAPI lifespan to ensure background tasks complete before resource closure.
- Non-Blocking I/O: Refactored Unreal metadata import and settings handlers to use asyncio.to_thread.
- Frontend Throttling: Implemented a 50ms state buffer for chat streaming to eliminate browser "render thrashing".
- O(1) Vector Sync: Optimized split-brain synchronization by querying the latest ID instead of loading the entire set of keys into memory.

### Maintenance
- Version Bump: Synchronized project configuration files to v0.0.69.
- Full Test Pass: Verified stability with 232 backend and 40 specialized indexing tests.
- Linter Clean: Resolved 200+ Python naming and hygiene violations.

