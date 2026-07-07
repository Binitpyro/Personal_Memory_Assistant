# Codebase Failure Points Report

## Executive Summary
This report compiles and synthesizes the findings from three parallel codebase scans and the verification of the unit test suite on the Personal Memory Assistant (PMA) codebase. The scans targeted potential bugs, architectural risks, performance bottlenecks, regressions, and compliance issues across text extraction, indexing, search/RAG pipelines, authentication, and local database storage.

A total of **27 issues** were identified:
- **5 Critical Issues**: Google OAuth redirection block, infinite recursion in the LLM capability detector, SQLite shared write connection transaction interleaving, GraphRAG edge resolution database syntax error, and chunks retrieval index error.
- **13 High-Medium Issues**: Context builder return type mismatch, chunker `_split_text` regression, out-of-folder profiling, query stream end-of-stream crash, Windows UNC path canonicalization bugs, NTFS scanner memory bloat, network starvation in offline environments, and concurrency race conditions.
- **9 Low/Minor Issues**: Mock import failure in DOCX extractor, warnings/binary text stubs pollution, UTF-16 misclassification, legacy Excel crashes, zip-bomb risks, and missing OCR for scanned documents.

Resolving these issues is necessary to ensure offline stability, prevent stack overflow crashes, secure user authentication, and maintain the application's strict memory ceiling limit (~60MB RAM).

---

## Critical Issues

### 1. Google OAuth Callback Authentication Bypass Bug
- **File Path and Lines**: `app/main.py` (Lines 398–412) and `app/api/auth.py` (Lines 179–181, 192–195)
- **Impact and Logic Chain**:
  1. The `security_and_telemetry_middleware` blocks all endpoints starting with `/api/` if the `X-Local-Access-Token` is missing from headers or query parameters.
  2. The Google OAuth Callback endpoint `/api/auth/google/callback` starts with `/api/` but is not exempted from token checks (the whitelist only contains `/api/health`, `/health`, and `/api/index/progress-stream`).
  3. Google redirects the user's browser back to `/api/auth/google/callback?code=...` which is a standard browser redirect. This GET request has no custom headers, and Google does not include the application's local access token.
  4. The middleware rejects the request and returns a `401 Unauthorized` page to the browser.
  5. **Impact**: Google Sign-In is completely broken and authentication cannot succeed.
- **Verbatim Code Evidence**:
  - In `app/main.py`:
    ```python
    @app.middleware("http")
    async def security_and_telemetry_middleware(request: Request, call_next):
        # 1. Enforce Local Access Token if running in desktop mode
        expected_token = os.environ.get("X_LOCAL_ACCESS_TOKEN")
        if not expected_token:
            raise RuntimeError("X_LOCAL_ACCESS_TOKEN missing. Refusing to serve request.")
            
        if request.url.path.startswith("/api/") and request.method != "OPTIONS":
            if request.url.path not in ("/api/health", "/health", "/api/index/progress-stream"):
                provided_token = request.headers.get("X-Local-Access-Token")
                if not provided_token:
                    provided_token = request.query_params.get("token")

                if not provided_token or not secrets.compare_digest(provided_token, expected_token):
                    return JSONResponse(status_code=401, content={"error": "Unauthorized local access."})
    ```
- **Recommended Remediation**: Add `"/api/auth/google/callback"` (and any other OAuth callback paths) to the whitelist of exempted paths in the middleware.

### 2. Infinite Recursion in CapabilityDetector
- **File Path and Lines**: `app/search/capability_detector.py` (Lines 18–62, specifically line 48) and `app/search/llm_client.py` (Lines 241–247)
- **Impact and Logic Chain**:
  1. The LLM Client's `generate_answer` method initiates a capability check if `skip_capability_check` is False (which is the default).
  2. The capability check invokes `capability_detector.detect_capabilities(self)`.
  3. If the capability cache is empty, the detector performs a probe query by calling `llm_client.generate_answer(probe_prompt, context="", history=[])`.
  4. Crucially, the detector's call does not set `skip_capability_check=True`.
  5. This causes the probe call to initiate a secondary capability check, which again triggers a probe query, generating an infinite recursion loop that quickly exhausts stack memory and crashes the sidecar process.
  6. **Impact**: RAG and query services crash with a stack overflow / recursion limit error during initialization of the capability detector.
- **Verbatim Code Evidence**:
  - In `app/search/capability_detector.py`:
    ```python
    try:
        logger.info("CapabilityDetector: Running <claim> tag probe...")
        # We call generate_answer. Using history=[] to ensure no contamination.
        response = await llm_client.generate_answer(probe_prompt, context="", history=[])
    ```
  - In `app/search/llm_client.py`:
    ```python
    async def generate_answer(
        self, query: str, context: str, history: list[dict[str, str]] | None = None, mode: str | None = None, skip_capability_check: bool = False
    ) -> str:
        await self._ensure_token_loaded()
        supports_claims = False
        if not skip_capability_check:
            supports_claims = await capability_detector.detect_capabilities(self)
    ```
- **Recommended Remediation**: Explicitly pass `skip_capability_check=True` in the probe call inside `capability_detector.py`:
  ```python
  response = await llm_client.generate_answer(probe_prompt, context="", history=[], skip_capability_check=True)
  ```

### 3. SQLite Shared Write Connection Transaction Interleaving
- **File Path and Lines**: `app/storage/db.py` (Lines 33-43, 117-122) and write methods such as `increment_usage_count` (Lines 951-958)
- **Impact and Logic Chain**:
  1. The `DatabaseManager` creates a single shared `self._write_conn` connection that is exposed globally via `_get_conn()`.
  2. Transaction scoping in SQLite (via `aiosqlite`) is tied to the connection object.
  3. In a multi-task asynchronous environment, concurrent write calls (such as file metadata writes, batch chunk inserts, and telemetry/usage increments) share the exact same write connection.
  4. When any task executes `await conn.commit()`, it commits *all* pending modifications on that connection.
  5. A foreground task calling a small write method like `increment_usage_count()` will trigger a commit, which prematurely commits an ongoing, half-finished batch insert from an indexing task.
  6. **Impact**: Transaction isolation is lost. A crash or rollback will leave the SQLite database in an inconsistent state, leading to orphaned database entries or corrupted indexes.
- **Verbatim Code Evidence**:
  - In `app/storage/db.py`:
    ```python
    class DatabaseManager:
        # ...
        def __init__(self, db_path: str = "pma_metadata.db", pool_size: int = 4):
            self.db_path = db_path
            self.pool_size = pool_size
            self._write_conn: aiosqlite.Connection | None = None
        # ...
        def _get_conn(self) -> aiosqlite.Connection:
            if self._write_conn is None:
                raise RuntimeError("Database not connected. Call connect() first.")
            return self._write_conn
    ```
- **Recommended Remediation**: Implement an asynchronous lock (`asyncio.Lock`) for write operations to serialize writes, or manage transaction states through transaction context managers that hold locks until the entire batch transaction commits or rolls back.

### 4. Database Syntax Error in GraphRAG Edge Resolution (`resolve_pending_graph_edges`)
- **File Path and Lines**: `app/storage/db.py` (Line 721, inside `resolve_pending_graph_edges`)
- **Impact and Logic Chain**:
  1. Indexing a folder crashes during the final GraphRAG linkage phase when resolving pending edges.
  2. The SQL query inside `resolve_pending_graph_edges` references `kg_nodes.name` (`WHERE kg_nodes.name = substr(...)`).
  3. However, the `kg_nodes` table schema does not define a `name` column. The primary identifier column is `id`.
  4. **Impact**: GraphRAG edge resolution fails on every run, throwing a sqlite3 Syntax Error and crashing the indexing pipeline.
- **Verbatim Code Evidence**:
  ```python
  await conn.execute(
      """
      UPDATE kg_edges
      SET target = (
          SELECT id FROM kg_nodes
          WHERE kg_nodes.name = substr(kg_edges.target, 10)
          LIMIT 1
      )
      WHERE target LIKE 'PENDING::%'
      AND EXISTS (
          SELECT 1 FROM kg_nodes
          WHERE kg_nodes.name = substr(kg_edges.target, 10)
      )
      """
  )
  ```
- **Recommended Remediation**: Change all references to `kg_nodes.name` to `kg_nodes.id` in `resolve_pending_graph_edges` to match the table's schema.

### 5. IndexError in Chunks Retrieval Candidate Builder (`_build_candidate_results`)
- **File Path and Lines**: `app/search/retrieval.py` (Line 321, inside `_build_candidate_results`)
- **Impact and Logic Chain**:
  1. RAG query searches fail with an `IndexError` during result candidate construction.
  2. The helper function `_build_candidate_results` attempts to unpack row elements at index 4 to 8 (e.g. `row[4]` for `modified_at`, `row[5]` for `start_offset`, etc.) to build metadata.
  3. However, database query mocks and some active database calls return only 4-element tuples (`(id, text, path, tag)`), causing an index out of bounds error.
  4. **Impact**: Search and retrieval operations crash with `IndexError: tuple index out of range` on queries that do not match the expected row format.
- **Verbatim Code Evidence**:
  ```python
  file_path = row[2]
  rrf_score = score_map[cid] * settings.rrf_score_scale
  if file_path in relevant_doc_paths:
      rrf_score *= settings.summary_boost_factor
  results.append(
      {
          "chunk_id": cid,
          "text": text,
          "file_path": file_path,
          "folder_tag": row[3],
          "modified_at": row[4],
          "start_offset": row[5],
          "end_offset": row[6],
          "sentence_offsets": row[7],
          "segmenter_version": row[8],
          "score": round(rrf_score, 4),
      }
  )
  ```
- **Recommended Remediation**: Add boundary checks or use fallback values when unpacking database rows in `_build_candidate_results`, or ensure that all SQL queries and mock environments consistently pass the full 9 required fields.

---

## High-Medium Issues

### 6. Return Type Mismatch in Context Builder (`build_context`)
- **File Path and Lines**: `app/search/context_builder.py` (Line 311, `build_context` signature and return statements)
- **Impact and Logic Chain**:
  1. Unit tests in `test_context_builder_extended.py` and `test_coverage_boost.py` fail because they assert on string values but receive a tuple.
  2. `build_context` was refactored to return `tuple[str, int]` (context string and token count) for token-budget optimization.
  3. The test suites still invoke `build_context` expecting a string return type.
  4. **Impact**: Breaks unit test suite assertions, failing context builder tests.
- **Verbatim Code Evidence**:
  ```python
  def build_context(
      retrieved_results: list[dict[str, Any]],
      max_tokens: int = 0,
      file_stats: dict[str, Any] | None = None,
      folder_profiles_text: str = "",
      metadata_insights: str | None = None,
      graph_paths_text: str = "",
      model_class: str = "cloud",
  ) -> tuple[str, int]:
  ```
- **Recommended Remediation**: Update unit tests to extract the context string from the returned tuple (`result[0]`) before running assertions.

### 7. Refactoring Regression in Chunker Method (`_split_text` Removed)
- **File Path and Lines**: `tests/test_indexing_service_extended.py` (Line 192)
- **Impact and Logic Chain**:
  1. Indexing unit tests fail with an `AttributeError`.
  2. The private method `_split_text` was removed or renamed from `IndexingService` during refactoring.
  3. The test suite was not updated to reflect this change and still attempts to call `svc._split_text`.
  4. **Impact**: Outdated indexing service tests crash.
- **Verbatim Code Evidence**:
  ```python
  plain_chunks = svc._split_text("A. B. C. D." * 10, "Prefix: ", 0)
  assert plain_chunks
  assert "Prefix: " in plain_chunks[0]["text_preview"]
  ```
- **Recommended Remediation**: Update or remove the outdated test cases in `test_indexing_service_extended.py` that call `_split_text`.

### 8. Out-of-Folder Files Profiled in `build_folder_profile`
- **File Path and Lines**: `app/indexing/folder_profiler.py` (Line 118, `build_folder_profile`)
- **Impact and Logic Chain**:
  1. Folder profiling counts files that lie outside the target folder.
  2. `build_folder_profile` takes a list of files and directly profiles them without checking if their paths are relative to/contained within the target folder.
  3. This breaks logical isolation and causes `test_profile_excludes_files_outside_folder` to fail.
  4. **Impact**: Fails folder profiling isolation tests and logical boundary constraints.
- **Verbatim Code Evidence**:
  ```python
  def build_folder_profile(
      folder: Path,
      folder_tag: str,
      files: list[tuple[Path, str]],
  ) -> dict[str, Any]:
      """Analyse an indexed folder and produce a rich profile dict."""
      folder_files = files
      # ...
  ```
- **Recommended Remediation**: Add a filtering step in `build_folder_profile` or ensure the input file list is strictly filtered by path.

### 9. Query Stream End-of-Stream Crash
- **File Path and Lines**: `app/api/search.py` (Lines 114–124)
- **Impact and Logic Chain**:
  1. The search streaming router catches `StopAsyncIteration` when the underlying async generator `stream_rag` terminates.
  2. It attempts to read `e.value` to construct the final response payload.
  3. According to PEP 525 (Asynchronous Generators), async generators cannot return a value, and `StopAsyncIteration` exceptions do not carry a `.value` attribute (unlike sync `StopIteration` exceptions).
  4. This access raises an `AttributeError`, causing the request stream to terminate abruptly and log an error instead of gracefully closing with a `done` payload.
- **Verbatim Code Evidence**:
  ```python
          while True:
              try:
                  chunk = await asyncio.wait_for(anext(agen), timeout=15.0)
                  yield json.dumps(chunk) + "\n"
              except asyncio.TimeoutError:
                  yield json.dumps({"type": "ping"}) + "\n"
  except StopAsyncIteration as e:
      payload = {"type": "done"}
      if e.value:
          payload.update(e.value)
      yield json.dumps(payload) + "\n"
  ```
- **Recommended Remediation**: Remove the `if e.value` check and simply yield the `{"type": "done"}` payload without inspecting `e.value`.

### 10. Windows UNC Network Path Canonicalization Bug
- **File Path and Lines**: `app/scanner/rust_core/src/lib.rs` (Lines 252-257)
- **Impact and Logic Chain**:
  1. During directory scanning on Windows, `std::fs::canonicalize` returns absolute UNC paths starting with `\\?\` (or `\\?\UNC\` for network shares).
  2. The code attempts to clean this prefix by trimming `\\?\` directly: `.trim_start_matches(r"\\?\")`.
  3. For UNC paths like `\\?\UNC\server\share\file`, this yields `UNC\server\share\file` which is not a valid Windows path format.
  4. **Impact**: Subsequent file access calls using this invalid path fail, preventing the scanner and indexer from processing files stored on Windows network shares.
- **Verbatim Code Evidence**:
  ```rust
  if let Ok(abs_path) = std::fs::canonicalize(&path) {
      let path_str = abs_path.to_string_lossy();
      Some(path_str.trim_start_matches(r"\\?\").to_string())
  } else {
      path.to_str().map(|s| s.to_string())
  }
  ```
- **Recommended Remediation**: Handle the `\\?\UNC\` prefix specifically by replacing it with `\\` (e.g. if path starts with `\\?\UNC\`, replace it with `\\`, otherwise trim `\\?\`).

### 11. NTFS MFT Scanner Memory Bloat
- **File Path and Lines**: `app/scanner/ntfs_mft.py` (Lines 57-59, 215-219)
- **Impact and Logic Chain**:
  1. The Windows-specific NTFS scanner opens the raw drive volume and enumerates all USN/MFT records on it.
  2. Every MFT entry is stored in memory in `self.entries` and indexed in `self.children_map`.
  3. On large NTFS partitions (e.g. system drives containing 2 million+ files), this allocates millions of dictionary items and lists.
  4. **Impact**: Scale scales linearly with partition size, consuming 500MB - 2GB+ of memory, violating the ~60MB RAM ceiling constraint in `GEMINI.md` and causing OOM crashes.
- **Verbatim Code Evidence**:
  ```python
  self.entries: dict[int, MFTEntry] = {}
  self.children_map: dict[int, list[MFTEntry]] = defaultdict(list)
  # ...
  def _build_children_map(self) -> None:
      """Index every entry by its parent reference."""
      self.children_map.clear()
      for entry in self.entries.values():
          self.children_map[entry.parent_ref].append(entry)
  ```
- **Recommended Remediation**: Transition the NTFS MFT scanner to use a streaming generator format or SQLite-backed temporary storage for records during mapping rather than holding all records in Python's memory space.

### 12. Offline / Firewalled Embedding Model Load Failure
- **File Path and Lines**: `app/embeddings/service.py` (Lines 76-105)
- **Impact and Logic Chain**:
  1. If the local ONNX embedding model assets are missing, `EmbeddingService` attempts to download the model from Hugging Face.
  2. If the application is run in an offline or firewalled environment (common for a local-first desktop application), the `snapshot_download` call raises a network connection error.
  3. This network error propagates, failing the embedding service initialization.
  4. **Impact**: The application fails to start up or crashes on startup with `RuntimeError: Embedding model failed to load.`
- **Recommended Remediation**: Bundle the ONNX embedding model with the Tauri desktop installer or degrade gracefully to a search-only offline fallback mode with clear user alerts.

### 13. Missing Reranker Model Downloader / Local Dependency
- **File Path and Lines**: `app/search/reranker.py` (Lines 20-43)
- **Impact and Logic Chain**:
  1. The reranker lazy-loads the cross-encoder model (`cross-encoder/ms-marco-MiniLM-L-6-v2`) on query execution.
  2. If the model file is not found locally under `models/` or the cache, a `FileNotFoundError` is raised.
  3. Unlike the embedding service, `reranker.py` lacks any logic to download the missing assets.
  4. **Impact**: All search queries using RAG fail, returning errors and falling back to RRF-only search.
- **Verbatim Code Evidence**:
  ```python
  def _load_onnx_model():
      # ...
      onnx_file = model_path / "model.onnx"
      if not onnx_file.exists():
          onnx_file = model_path / "onnx" / "model.onnx"

      if not onnx_file.exists():
          raise FileNotFoundError(f"ONNX reranker model not found at {onnx_file}")
  ```
- **Recommended Remediation**: Implement a lazy downloader for the reranker model assets similar to the embedding model, or pre-bundle the model in the desktop installer.

### 14. Offline NLTK Download Starvation Loop
- **File Path and Lines**: `app/indexing/service.py` (Lines 42-57)
- **Impact and Logic Chain**:
  1. When segmenting text during indexing, `_get_sentence_offsets` looks for NLTK's `punkt` and `punkt_tab` data.
  2. If not found locally (e.g. fresh install), a `LookupError` is caught, and the code calls `nltk.download()`.
  3. In offline mode, the download call fails due to connection timeout/socket errors, throwing an exception.
  4. Because the exception is raised before `_nltk_punkt_downloaded = True` is set, the flag remains `False`.
  5. The application retries the network download on *every single* subsequent chunk, causing massive network timeout overhead.
  6. **Impact**: Indexing becomes extremely slow, effectively freezing the system.
- **Verbatim Code Evidence**:
  ```python
  try:
      import nltk
      if not _nltk_punkt_downloaded:
          try:
              nltk.data.find('tokenizers/punkt')
              nltk.data.find('tokenizers/punkt_tab')
          except LookupError:
              nltk.download('punkt', quiet=True)
              nltk.download('punkt_tab', quiet=True)
          _nltk_punkt_downloaded = True
  ```
- **Recommended Remediation**: Wrap the download calls in a nested `try-except` block and set `_nltk_punkt_downloaded = True` even if the download fails (or mark a download attempt flag) to avoid subsequent download retry attempts in the same run.

### 15. Memory Leak & Stuck Indexing Progress on Extraction Failures
- **File Path and Lines**: `app/indexing/service.py` (Lines 414-485, 550-587)
- **Impact and Logic Chain**:
  1. In `_stream_extract_and_prepare`, the "header" is queued first.
  2. If an exception occurs later during chunking, streaming, or summary generation, execution jumps to the `except` block.
  3. The "footer" token is never pushed to the queue.
  4. Because the `_storer_worker` only removes a file from `active_files` and increments the progress stream when it receives a "footer" token, the file metadata remains in `active_files` indefinitely.
  5. **Impact**: Causes a memory leak (leaking dictionary items in RAM) and leaves the progress stream stuck below 100% completion.
- **Verbatim Code Evidence**:
  ```python
  async def _stream_extract_and_prepare(
      self, path: Path, folder_tag: str, pre_text: str | None, queue: asyncio.Queue
  ) -> None:
      loop = asyncio.get_running_loop()
      try:
          # ...
          await queue.put(header)
          # ... [extraction logic]
          await queue.put({"type": "footer", "path": path, "summary": summary})
      except Exception as e:
          logger.error("Streaming extraction failed for %s: %s", path, e)
  ```
- **Recommended Remediation**: Add a `finally` block or ensure that in the `except` block, a fallback "footer" token with an empty summary is always queued.

### 16. LanceDB Folder Purge SQL Injection / Syntax Error
- **File Path and Lines**: `app/vector_store/lancedb_client.py` (Lines 270–286, specifically 280, 284)
- **Impact and Logic Chain**:
  1. `delete_folder` removes folder vectors from the chunk and summary tables using: `.delete(f"folder_tag = '{folder_tag}'")`.
  2. If a folder name contains a single quote (e.g. `"John's Folder"`), the string evaluates to `folder_tag = 'John's Folder'`, breaking string quotes.
  3. **Impact**: This raises a query compilation syntax error in LanceDB/Arrow, crashing the folder deletion pipeline.
- **Verbatim Code Evidence**:
  ```python
  def _delete_impl():
      with self._write_lock:
          # 1. Chunks
          tbl_chunks = self._get_table("pma_chunks")
          if tbl_chunks:
              tbl_chunks.delete(f"folder_tag = '{folder_tag}'")
          # 2. Summaries
          tbl_sums = self._get_table("pma_summaries")
          if tbl_sums:
              tbl_sums.delete(f"folder_tag = '{folder_tag}'")
  ```
- **Recommended Remediation**: Escape single quotes (e.g. `.replace("'", "''")`) in the `folder_tag` before injecting it into the query string, matching the search metadata parser logic.

### 17. Mismapped Chunks in `insert_chunks_bulk`
- **File Path and Lines**: `app/indexing/service.py` (Lines 600-621, specifically 606)
- **Impact and Logic Chain**:
  1. After batch inserting file chunks to the database, the code maps generated row IDs to memory chunk objects using `zip(chunk_ids_int, chunks, strict=False)`.
  2. Because `strict=False` is used, if the lists mismatch in size (e.g. due to partial insertion failures or concurrent database transactions), Python truncates the mapping silently.
  3. **Impact**: Mismatched database row IDs are associated with wrong chunk vectors inside the SQLite embeddings table and LanceDB vector store, leading to retrieval corruption.
- **Verbatim Code Evidence**:
  ```python
  chunk_ids_int = await self.db.insert_chunks_bulk(chunk_rows)
  # ...
  for chunk_id, item in zip(chunk_ids_int, chunks, strict=False):
  ```
- **Recommended Remediation**: Set `strict=True` to explicitly detect size mismatches, and handle failures gracefully.

### 18. Uvicorn Multi-Worker Sync Race Condition
- **File Path and Lines**: `app/main.py` (Lines 230-237) and `app/api/system.py` (Lines 244-246)
- **Impact and Logic Chain**:
  1. The database synchronization and compaction functions verify `os.environ.get("UVICORN_WORKER_ID", "0") == "0"` to restrict database syncs/compactions to a single primary worker.
  2. However, Uvicorn does not set the `UVICORN_WORKER_ID` environment variable by default in multi-worker modes.
  3. This causes all worker processes to evaluate the ID as `"0"` and simultaneously run sync/compaction tasks.
  4. **Impact**: Concurrent database writes to SQLite/LanceDB on startup trigger transaction collisions and write lock errors (`sqlite3.OperationalError: database is locked`).
- **Verbatim Code Evidence**:
  - In `app/main.py`:
    ```python
    async def _split_brain_sync(db_manager, lancedb_client, emb_svc):
        # ...
        if os.environ.get("UVICORN_WORKER_ID", "0") != "0":
            state.split_brain_sync_status = "idle"
            return
    ```
- **Recommended Remediation**: Avoid relying on worker environment variables for task synchronization, or use a file/database-based locking mechanism to ensure only a single process runs maintenance tasks.

---

## Low/Minor Issues & Constraints Review

### 19. Mock Import Failure in `TestDocxExtractor`
- **File Path and Lines**: `tests/test_extractors.py` (Line 173, inside `test_extract_success`)
- **Impact and Logic Chain**:
  1. Unit tests for DOCX files fail with a `ModuleNotFoundError` or return empty strings.
  2. The test setup mocks python-docx by patching `sys.modules["docx"]` with a flat `MagicMock`.
  3. During execution, the refactored DOCX extractor attempts to import nested submodules like `docx.text.paragraph` and `docx.table`.
  4. Python fails to locate these submodules under the flat mock object, leading to warnings or import errors and failing the test assertions.
  5. **Impact**: DOCX unit tests fail.
- **Verbatim Code Evidence**:
  - In `tests/test_extractors.py`:
    ```python
    mock_docx = MagicMock()
    mock_para = MagicMock()
    mock_para.text = "Paragraph text content"
    mock_doc = MagicMock()
    mock_doc.paragraphs = [mock_para]
    mock_doc.tables = []
    mock_docx.Document.return_value = mock_doc
    with patch.dict("sys.modules", {"docx": mock_docx}):
        result = self.ext.extract(fake_path, MAX_SIZE)
    assert "Paragraph" in result
    ```
  - In `app/indexing/extractors/docx_extractor.py`:
    ```python
    from docx import Document
    # ...
    from docx.text.paragraph import Paragraph
    from docx.table import Table
    ```
- **Recommended Remediation**: Ensure the mock configuration properly stubs the nested modules `docx.text.paragraph` and `docx.table` in `sys.modules`.

### 20. Indexing Error/Warning Stubs
- **File Path and Lines**: `app/indexing/service.py` (Lines 328-349, 409-436) and `app/scanner/rust_core/src/lib.rs` (Lines 513–534)
- **Impact**: Unreadable or binary files yield text stubs (e.g. `[BINARY: <path>] Binary content not indexed.`). These stubs are sent to the embedding service and LanceDB, wasting storage and polluting search results with useless text matches.
- **Recommended Remediation**: Filter out binary warning stubs or skip chunking/embedding entirely for files classified as unreadable.

### 21. UTF-16 Text Files Misclassified as Binary
- **File Path and Lines**: `app/scanner/rust_core/src/lib.rs` (Lines 480-488) and `app/indexing/service.py` (Lines 803-809)
- **Impact**: The binary heuristic checks for null bytes (`b"\x00"`) in the first 8192 bytes. UTF-16 files contain null bytes for standard ASCII characters, causing them to be incorrectly skipped during indexing.
- **Recommended Remediation**: Enhance binary detection logic to recognize UTF-16 BOMs (`0xFFFE` or `0xFEFF`) before checking for null bytes.

### 22. openpyxl Old XLS Crash
- **File Path and Lines**: `app/indexing/extractors/xlsx_extractor.py` (Lines 8-17)
- **Impact**: While the extractor accepts `.xls` files, loading a legacy binary Excel file using openpyxl (an OOXML-only reader) raises an `InvalidFileException`. *(Note: Although a partial fallback for `.xls` using `xlrd` was added to the code, format mismatch or absence of the optional `xlrd` dependency still triggers failures).*
- **Recommended Remediation**: Standardize extractor formats and verify formats before loading libraries.

### 23. WebGPU Visualizer Memory Spike
- **File Path and Lines**: `app/insights/visualizer.py` (Lines 13-35)
- **Impact**: `_stream_visualizer_binary_impl` retrieves database files using `fetchall()`. In large environments, this loads the entire dataset into RAM, violating the O(1) memory bound of ~60MB RAM.
- **Recommended Remediation**: Stream database rows using an async cursor iterator (e.g. `async for row in cursor`).

### 24. EPUB Scrambled Reading Order
- **File Path and Lines**: `app/indexing/extractors/epub_extractor.py` (Lines 26-32)
- **Impact**: The extractor reads internal XHTML files alphabetically (`sorted()`) rather than following the OPF file's manifest spine structure. This scrambles the reading/indexing layout order of the document.
- **Recommended Remediation**: Parse the EPUB's `.opf` container file first and read the XHTML components in the order declared in the `<spine>` element.

### 25. Zip-Bomb Vulnerability in DOCX, XLSX, PPTX, and EPUB Extractors
- **File Path**: `app/indexing/extractors/*`
- **Impact**: The ZIP extractors extract archive files without validating uncompressed XML components sizes, exposing the application to Denial of Service (DoS) memory exhaustion attacks via zip-bomb.
- **Recommended Remediation**: Audit and validate uncompressed size headers for zip files before opening them.

### 26. Missing OCR Fallback for Scanned PDFs
- **File Path**: `app/indexing/extractors/pdf_extractor.py`
- **Impact**: Scanned PDFs yield no text layer. The extractor yields 0 chunks and silently skips the document, leaving the user unaware that the scanned file was not indexed.
- **Recommended Remediation**: Implement image-only PDF detection and report warnings, or support a local OCR fallback interface (e.g. Tesseract).

### 27. Syntax Error on Empty ID List in `delete_documents`
- **File Path and Lines**: `app/vector_store/lancedb_client.py` (Lines 255-268)
- **Impact**: If an empty list of IDs is passed to `delete_documents()`, it yields `id IN ()` which triggers a syntax compilation error in LanceDB/Arrow.
- **Recommended Remediation**: Check if `ids` is empty at the start of the method and return immediately.

---

### Constraints & Licensing Review

#### 1. Strict Permissive Licensing Check
Repository dependencies were audited via `pyproject.toml`, `Cargo.toml`, `package.json`, and `uv.lock`. All packages use permissive licenses (MIT, Apache 2.0, BSD-3, BSD-2, or ISC). No copyleft dependencies (GPL/AGPL) are integrated into the product. This fully complies with the project licensing constraints.

#### 2. Constraints Compliance & Memory Limits
- **Memory Ceiling Violation (Fail)**: The application has a strict ~60MB RAM ceiling limit (as specified in `GEMINI.md`). The NTFS Scanner (`ntfs_mft.py`) and WebGPU Visualizer (`visualizer.py`) load whole datasets into Python memory (using standard dicts or `.fetchall()`), causing memory consumption to scale linearly with the dataset size.
- **Security Hardening - Zip-Bomb Immunity (Fail)**: The ZIP extractors (DOCX, XLSX, PPTX, EPUB) unpack archives without checking size headers, which violates the zip-bomb immunity constraint.

---

## Adversarial & Scale Risk Review

### 1. SQLite Single Write Connection Lockups under Heavy Load
- **Assumption Challenged**: That an async queue or WAL mode fully isolates transactions when using a single shared connection object.
- **Scenario**: An indexing task running multiple batches of inserts via `execute_many` and executing `commit()` concurrently with a foreground task calling `increment_usage_count()`. Since SQLite locks the connection during commit, a race condition causes a `database is locked` operational error, or causes half-written batches to commit prematurely, leaving the database in an inconsistent state.
- **Blast Radius**: Database corruption, index desynchronization, or crash of the indexing worker.
- **Mitigation**: Implement an explicit `asyncio.Lock` for all writes, or utilize a write-connection pool where each write operation leases an isolated connection.

### 2. Memory Ceiling Violation on 1M+ File Tree
- **Assumption Challenged**: That the SPA visualizer stream is O(1) memory bound because it returns a `StreamingResponse`.
- **Scenario**: A user indexes a large directory containing 1,000,000 files. When the frontend requests the WebGPU visualizer data, the backend calls `db.execute_query` which calls `fetchall()`, loading all 1,000,000 rows into RAM.
- **Blast Radius**: The process crashes with Out of Memory (OOM), violating the ~60MB RAM ceiling constraint.
- **Mitigation**: Rewrite `_stream_visualizer_binary_impl` to use a cursor stream (`async for row in cursor`) rather than `fetchall()`.

---

## Verification Methodology

### 1. OAuth Callback Bypass Verification
Run the sidecar application and visit the callback route directly:
```bash
curl -i http://127.0.0.1:8000/api/auth/google/callback
```
*Expected Result*: Returns `HTTP/1.1 401 Unauthorized` instead of being accepted or handled by the routing pipeline.

### 2. Capability Detector Recursion Verification
Temporarily reset the capability detector cache and perform a search/RAG query:
```python
from app.search.capability_detector import capability_detector
capability_detector.reset_cache()
# Execute search query
```
*Expected Result*: The application crashes due to exceeding Python's recursion depth limit (`RecursionError: maximum recursion depth exceeded`).

### 3. SQLite Concurrency / Transaction Isolation Verification
Simulate concurrent writes by initiating an indexing job on a folder and sending concurrent usage counts:
```bash
# While indexing, run:
curl -X POST http://127.0.0.1:8000/api/files/usage -d '{"path": "..."}'
```
*Expected Result*: Transaction commits are interleaved, causing database transaction lockups or premature commits of uncompleted indexing batches.

### 4. GraphRAG Edge Resolution Syntax Error Verification
Run a test script that executes `resolve_pending_graph_edges()` with a database setup containing pending graph edges:
```python
await db_manager.resolve_pending_graph_edges()
```
*Expected Result*: Fails with `sqlite3.OperationalError: no such column: kg_nodes.name`.

### 5. Chunks Retrieval Candidate Builder IndexError Verification
Mock a RAG search result query where the SQLite database rows return a subset of the fields (e.g. 4 fields: `id, text, path, tag`):
```python
rows = [(1, "text content", "doc.txt", "tag")]
_build_candidate_results(rows, score_map, relevant_doc_paths)
```
*Expected Result*: Raises `IndexError: tuple index out of range`.

### 6. Context Builder Return Type Mismatch Verification
Execute unit tests in `tests/test_context_builder_extended.py`:
```bash
pytest tests/test_context_builder_extended.py
```
*Expected Result*: Assertion failures or `TypeError` due to comparing `tuple` with `str`.

### 7. Chunker Method Removal Verification
Run the chunking/indexing tests in `tests/test_indexing_service_extended.py`:
```bash
pytest tests/test_indexing_service_extended.py
```
*Expected Result*: Test crashes with `AttributeError: 'IndexingService' object has no attribute '_split_text'`.

### 8. Folder Profiler Ancestor Check Verification
Run `test_profile_excludes_files_outside_folder`:
```bash
pytest tests/test_indexing_service_extended.py -k test_profile_excludes_files_outside_folder
```
*Expected Result*: Fails because files located outside the folder are included in the folder profile statistics.

### 9. Mock Import Failure in DocxExtractor Verification
Run the document extractor tests:
```bash
pytest tests/test_extractors.py -k TestDocxExtractor
```
*Expected Result*: Fails due to `ModuleNotFoundError` during submodule imports of `docx.text.paragraph` and `docx.table` when `docx` is patched with a flat mock.

### 10. LanceDB Quote Deletion Crash Verification
Delete a folder tagged with an apostrophe:
```python
await lancedb_client.delete_folder("User's Folder")
```
*Expected Result*: Arrow query compiler fails and throws a syntax parsing error exception.

### 11. UTF-16 Binary Detection Verification
Save a plain text file in UTF-16 format and index it.
*Expected Result*: The chunker skips the file, returning `[BINARY: ...] Binary content not indexed.`.
