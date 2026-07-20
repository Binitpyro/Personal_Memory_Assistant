# Changelog v0.0.71

This release marks a comprehensive stability, security, and performance overhaul based on a deep codebase audit. All 72 identified issues have been resolved, and the core machine learning infrastructure has been modernized.

## Major Architectural Changes
* Transitioned the Machine Learning pipeline (embeddings and reranking) entirely to ONNX Runtime. This eliminates the heavy PyTorch dependency, reducing the compiled executable size from over 3.4GB to under 100MB while tripling CPU inference speed.

## Security & Authentication
* Hardened local authentication to fail-fast if the security token is missing, closing a potential bypass vulnerability.
* Prevented live API keys from being leaked in distributed executables by removing development environment files from the build bundle.
* Prevented SQL injection vulnerabilities in the vector store client by enforcing strict parameter escaping.

## Performance & Scalability
* Implemented a read-connection pool for the SQLite metadata database, significantly improving concurrency during multi-worker operation.
* Replaced string-matching algorithms with O(n) MinHash LSH for semantic deduplication, eliminating CPU spikes on large result sets.
* Added a dedicated disk I/O thread pool to prevent file hashing and stat operations from starving machine learning inference tasks.
* Upgraded vector indexing to use IVF-HNSW-SQ for a better balance of recall and search latency.
* Applied caching to tokenization routines to speed up context assembly during retrieval.

## Stability & Data Integrity
* Fixed a critical full-text search corruption issue that occurred when users reset their index.
* Resolved a multi-worker synchronization bug that could cause duplicate vector entries on startup.
* Addressed an indexing pipeline deadlock that could occur on malformed or inaccessible files.
* Mitigated database locking timeouts by transitioning away from heavy, blocking maintenance operations during active indexing.
* Added reconciliation logic to clean up unused vectors left behind after failed deletion attempts.
* Ensured query history and semantic cache updates are preserved even if the client disconnects mid-stream.

## Improvements & Bug Fixes
* Fixed a routing mismatch that caused certain folder management actions in the UI to return error codes.
* Prevented event loop blocking during heavy metadata imports and authentication token refreshes.
* Implemented GPU-accelerated spatial sorting and tree construction for the large-scale WebGPU visualizer.
* Corrected visualizer layout simulation to use the proper O(n log n) algorithm for hierarchical graph packing.
* Expanded reranker context windows to better utilize model capacity and improved metadata summarization to avoid data loss.
* Resolved memory bloat during differential synchronization by utilizing native Arrow-based aggregation.
* Fixed test suite leakage that caused file-lock errors in specific environments.
# Changelog v0.0.67 -> v0.0.69

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
- Version Bump: Synchronized all 12 project configuration files to v0.0.69.
- Full Test Pass: Verified stability with 232 backend and 40 specialized indexing tests.
- Linter Clean: Resolved 200+ Python naming and hygiene violations.
 
# Recent Changelog (from git log)

5759a7a Fix Insights 3D: migrate WebGPURenderer to instanced-mesh pipeline
7155273 overhaul tests
f2ec8ff Add claim-capability detector & stream UI refactor
4d61f23 Add sentence offsets, modes, and telemetry
3a1ee73 Add keyring, modules WS; remove Unreal import
e0d880c Add code-graph extraction and Graph RAG support
f866452 Optimize Rust release; remove OPTIONAL_ENV var
0cfa378 Merge branch 'fix/build-and-entrypoint' into updates
141de3f chore: commit all local changes before merging to updates
4f6d728 fix: make tauri release bundle sidecar zip
