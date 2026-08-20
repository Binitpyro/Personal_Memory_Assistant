# Changelog v0.0.72

*Version bumped to `0.0.72` across all five manifests — `pyproject.toml:3`, `frontend/package.json:4`, `frontend/src-tauri/tauri.conf.json:4`, `frontend/src-tauri/Cargo.toml:3`, `app/scanner/rust_core/Cargo.toml:3` — plus the README badge and the `Run-Tests.bat` banner. Lockfiles regenerate from their tools. Not tagged, not released.*

**Baseline: `main` at `7c16249` (2026-07-25), which is v0.0.71 as shipped.** Everything below is the delta `main..updates` — **55 commits**, 2026-07-28 through 2026-08-20, none of them released.

Branch state at the time of writing, worth knowing before the next merge:

- `updates` is **55 ahead** of `main` and **2 behind** it. Those two are `42d0103` (the merge of PR #9) and `7c16249` (a README update). The branches have not been merged since 2026-07-25; `7c16249`'s README improvements were brought across by hand in this cycle and extended, so a merge should take this branch's README, but the commits themselves are still unmerged.
- `origin/updates` is at `b3cdd59` (2026-08-18). **13 commits are unpushed**, including every fix from `d56b94e` onward.
- `main` contains no `app/ocr/` at all. The whole OCR subsystem is unreleased.

---

## Executive summary

* **OCR ingestion ships.** Three tiers, from a subprocess-isolated CPU engine to a DirectML GPU tier to routing pages through a vision model the user already runs. PMA never downloads a VLM.
* **The ingest path can no longer record a file as complete when it produced nothing.** Hash success said nothing about extraction success; three permanently unsearchable files were found in the working databases, including a 97 KB `requirements.txt`.
* **Peak ingestion memory fell 56%** on a real 403 MB corpus — 1307.1 MB → 569.4 MB above idle — and again to 535.3 MB once the PPTX extractor stopped building an lxml tree per deck. Throughput went *up*, not down.
* **A corpus-driven XSS path is closed.** The chat renderer ran model output through `rehype-raw`, so a poisoned document could get executable HTML into a page holding the API token.
* **Retrieval fusion is deterministic**, and with the measurement trustworthy the summary-leg weight turned out to be 6x too high — it was costing recall against not using the leg at all.
* **The indexer no longer hangs after finishing.** A run that indexed 151 files and 21,584 chunks then sat 61 minutes at zero CPU.
* **Provider selection is local-first and fail-closed**, embedding weights are pinned by digest with drift detection, and `settings.json` has one atomic writer instead of three.
* **The Creative-module handlers were added and then removed inside this same window.** Net effect against `main`: 257 lines of Creative RAG code are *not* in MIT Core.

---

## 🔍 OCR ingestion

The headline feature of this cycle. Scanned documents were previously invisible to the index — they extracted zero native text, produced zero chunks, and nothing recorded that anything had happened.

### Three tiers

| Tier | Engine | Provisioning | Cost |
|---|---|---|---|
| **Standard** (`cpu`) | PP-OCRv4 mobile, `rapidocr-onnxruntime==1.4.4` | subprocess-isolated uv venv, ~230 MB | CPU only, ~1.3 s/page cold, ~0.5 s/page warm |
| **High accuracy** (`gpu`) | PP-OCRv4 **server** weights on DirectML (NVIDIA + AMD; not CUDA). Windows only | ~430 MB, SHA256-gated | **~5.2 GB VRAM** |
| **Your own AI model** (`vlm`) | the user's own vision model in Ollama or LM Studio | nothing to install | minutes per page, not seconds |

* **OCR runs in its own venv** because ONNX Runtime wheels are mutually exclusive — a GPU runtime cannot co-exist with the CPU one in the main environment. The worker is a separate process speaking NDJSON, backed by a durable SQLite queue so live ingestion throughput is preserved (`764d41e`, `9f52499`).
* **Tier 2 does not fit the 4 GB VRAM target, and the tier copy says so** (`63fdf3c`). 5355 MiB measured on an RTX 5060 Laptop, against a 0 MiB control on the CPU tier — so the figure is the OCR session, not ambient load. On a GTX 1650 or RX 580 this tier will spill to shared memory and end up slower than Tier 1. A user on a 4 GB card now learns that *before* the download rather than after.
* **Tier 3 sends page images to a model the user picked** (`9dfcdae`, `63fdf3c`). The settings card lists the vision models actually present in their Ollama or LM Studio, and names models worth pulling when there are none. A provider whose `base_url` is not on this machine is marked in the picker — page images are the most sensitive thing PMA sends anywhere, and that belongs on screen before selection, not at a consent prompt.

### Tier 3 decisions worth keeping

* **Pages transcribe sequentially.** The vision model is usually the same one serving chat and on a 4 GB card is already spilling layers to CPU; concurrent requests contend for that GPU rather than adding throughput.
* **Confidence is uniform and assigned, not invented per line.** A chat model returns no scores. Fabricating them would make `ocr_conf_floor` meaningless on this tier.
* **Cache identity is `vlm:<provider>:<model>`.** Two vision models transcribe differently, so switching must miss cache rather than serve the previous model's text.
* **A reply that reads as commentary is recorded as `VLM_COMMENTARY` with zero lines.** The user selects the model themselves, so a text-only one can be chosen — and it will confidently describe nothing. That must never be stored as page text. A page legitimately opening "The image shown on page 4…" is still kept.
* Markdown fences and "Here is the transcription:" lead-ins are stripped. Models emit both despite being told not to, and neither is page text.

### Defects fixed before a second tier could multiply them (`9f52499`)

* `ocr_cache` keyed on a module constant, so any second model would have aliased onto Tier 1's rows. Identity now comes from the worker's own `ready` report, which was already being logged and discarded.
* Any override model set was labelled `"custom"` — one cache key for all possible weights. Now a digest of the files actually loaded.
* `POST /ocr/install` awaited the whole provision, so the client never saw `running` and both the progress bar and the Cancel button were unreachable. Install is now a background task armed before the response returns.
* `uninstall_tier1` `rmtree`'d the shared models directory, which `registry.py` and `worker/engine.py` both document as a user drop-in slot. It now removes only the tier's own downloads.
* `PINNED_DEPS` pinned `opencv-python-headless` while rapidocr requires `opencv-python` — a different distribution, so both installed and the effective version floated. `uv pip compile` confirms one `cv2` now.
* **The install smoke test used to assert only that the worker started.** It now OCRs a generated fixture page with known text, so a recognition model paired with the wrong character dictionary fails the install instead of writing confident nonsense into the index.

### Supervision and accounting

* **`25f1002`** — `page_count` is the whole PDF, not the OCR denominator. A 10-page document with 3 scanned pages behaved as though 10 pages of OCR were owed: any error at all read a fully successful run as incomplete, retried it to `ocr_max_attempts`, then marked it done with `"OCR incomplete: 3/10 pages, incomplete"` — a message whose two halves contradict each other. `pages_pending` never reached zero, so the Library backlog banner and the Settings "Pending pages" tile reported a permanent phantom backlog.
* **`a6d0e23`** — a document whose every page errored was marked `done` with `last_error` wiped, and `enqueue_document` only re-arms on a content change, so it was retired permanently with nothing indexed and nothing recorded. Pages are now partitioned by error; a genuine success that produced nothing readable is still `done` but records *why*.
* **`4a32d1a`** — three independent supervision defects: `force_ocr` was inert (the API wrote the flag, `_process_doc` consulted the cache unconditionally, so Force OCR returned exactly the cached text the user was overriding); a row left `running` by a process that died mid-document was stuck until the next restart; and a crashed worker's traceback was cleared before it could be reported. Stale-claim reclamation is tier-aware — 600 s for the worker tiers against 7200 s for VLM — because one fixed value would either never reclaim or would steal a VLM document mid-run.
* **`2d3f684`** — `index_ocr_pages` used `begin_transaction()`/`commit()`, which on a shared write connection committed *the indexer's* transaction, or on the failure path discarded its uncommitted chunks. Now isolated in a `SAVEPOINT`, which nests: outside a transaction it behaves like BEGIN/COMMIT, inside one it is a marker.
* **`e52caa9`** — the Tier 3 loop re-opened and re-parsed the whole PDF per page, so a 50-page document paid 50 full parses. And `ocr_worker_idle_timeout_s` was read nowhere, so the worker was killed the instant the queue drained — under the normal one-file-at-a-time watcher pattern that meant a fresh venv start plus an ONNX model load per document. An idle worker now lingers, **except on the GPU tier**, where a resident DirectML session holds VRAM against a budget that is already over.
* **`b3cdd59`** — the indexing-active deferral was removed. Its stated premise ("`index_ocr_pages` raises outright when the indexer is busy") cited a line that does not exist, and since `2d3f684` the write is genuinely safe. It also carried a livelock: `release_claim()` refunds the attempt, so a persistent `RuntimeError` retried the same document forever with no budget to exhaust.
* **`5ff4d4e`, `ce8202d`** — partial runs record missing-page ranges and persist a meaningful `last_error` even when marking done; cache byte accounting computes a net delta on replacement and LRU deletes on `last_used_at`; VLM responses capped at 500k characters; a background raster thread with a 2-slot prefetch queue, watchdog-wrapped recognition, and explicit OPEN_FAILED/OOM handling; `layout_fragmentation_score` prevents garbled text being labelled NATIVE.

### First verified end-to-end run (`d462867`)

Nothing in the tree could exercise the OCR path. Every PDF under `tests/` and all 1000 in the perf corpus carried a text layer, so every page classified NATIVE at the detection gate and was never queued — which is why OCR quality could not be measured at all.

`scripts/make_scanned_pdf.py` builds a genuine image-only PDF with exact ground truth: text is laid out, rasterized with pypdfium2, and the grayscale raster re-embedded as a `/DeviceGray` image XObject in a second PDF carrying no text operators. No new dependency. The raster was verified to actually contain glyphs — 9,587 dark pixels for a two-line page at 150 DPI against exactly 0 for an empty one — because without that control the fixture could have been blank and every OCR run against it would have "succeeded" with no text, indistinguishable from a broken engine.

```
engine     : model_version='ppocrv4-mobile' ep='CPUExecutionProvider'
   <- doc_done  {"pages_ok": 1, "pages_failed": 0, "mean_conf": 0.9962}
recognized : 'PMA OCR 12345'   expected: 'PMA OCR 12345'   char recall: 100%
```

The perf corpus gains 50 scanned PDFs, keyed to their filenames so a run can be scored.

### A confidence threshold for fallback was rejected on data

The plan called for "a concrete threshold number for LM Studio fallback". Measured over 27 synthetic variants of a known-ground-truth page, a single `mean_conf` threshold is not a sound trigger for this engine:

| variant | mean_conf | char recall |
|---|---|---|
| clean 150 dpi | 0.9962 | 100% |
| noise sigma=110 | 0.9559 | 100% |
| 30 dpi | 0.9645 | 100% |
| **downscale x6 + noise 60** | **0.9254** | **36%** |
| blur r=14 | 0.5663 | 27% |
| downscale x10 | 0.0000 | 0% |

Three reasons it fails. Confidence does not track quality monotonically — blur r=2 scored 0.9979, *higher* than clean 300 dpi. The bands overlap: worst fully-correct page 0.9559 against best badly-read page 0.9254. And the dangerous mode is confidently wrong — `downscale6+noise60` returned `'A215'` for `'PMA OCR 12345'` at a confidence above any threshold that would not also fire on good pages. A gate there would pass that garbage into the index, which is the exact silent-failure class this workstream exists to remove. Total failure returns `mean_conf 0.0` with zero lines and is already handled without a threshold.

---

## 📥 Ingestion correctness

### A file is never recorded as complete unless it produced content (`193a439`)

`sha256_result = "ERROR" if hash_failed else hasher.hexdigest()` handed a valid digest to files that yielded nothing, and `_detect_changes` then treated those rows as up to date on every later run — permanently. A read-only assessment of the working databases found three of them, including a **97 KB `requirements.txt` that was silently unsearchable**.

* `_INCOMPLETE_SHA_STATES = ("", "ERROR", "CANCELLED", "NOCONTENT")` is the single list re-attempted. `""` matters twice: it is the header's placeholder *and* the pre-migration default. A 0-byte file keeps its real hash and is not retried.
* **A crashed run can no longer report success.** `_batch_index_pipeline` re-raises its `ExceptionGroup`; `complete()` honours `run_failed`. `fail()` deliberately leaves `status` at `idle`, because parking it non-idle stalls the OCR drain loop.
* **Stubs and binaries stay out of the index.** Only `[BINARY:` was filtered, so `rust_core`'s read-failure stub and the `[ENCRYPTED …]` notices were chunked, embedded and stored *as document content*. `.rtf`/`.odt`/`.ipynb` are in `supported_extensions` but not `TEXT_EXTENSIONS`, so an `.odt` — a zip — was indexed as U+FFFD noise.
* **An mtime touch with identical bytes now costs nothing.** The hash pass moved ahead of the header, since the header is what triggers `_delete_existing_chunks`.

### Four regressions from that change, repaired (`4192e9f`)

Found by adversarial review after the fact. The corpus used for the original verification had no scanned document and no UTF-16 file, so it exercised neither path.

1. **`NOCONTENT` poisoned the OCR cache.** `files.sha256` is the OCR cache's `content_key`. `NOCONTENT` was not in `INVALID_CONTENT_KEYS`, so every scanned document in a corpus shared one key and the `ocr_cache` primary key collided across unrelated documents — **one document silently served another's page text**. A scanned page yields no *native* text by definition; that is the OCR case, not a failure, so it now keeps its real digest.
2. **`[UNREADABLE:` retired files on a transient error.** It is `rust_core`'s I/O-failure stub — antivirus lock, network share blip — not a deliberate skip. It now yields `ERROR` and retries.
3. **UTF-16 text was silently dropped.** `_looks_binary` flagged any NUL, and every ASCII character in UTF-16 carries one. PowerShell ≤5.1 wrote UTF-16LE by default, so ordinary `.csv`/`.json`/`.sql`/`.log` files on Windows hit this and were marked complete with zero chunks. Added a BOM check and BOM-aware decoding — passing the binary gate is not enough, since UTF-16 read as UTF-8 still decodes to U+FFFD noise.
4. **The failure path dropped summary vectors for work that had succeeded**, leaving a permanent hole in the document-routing signal.

Also: `last_error` was removed from the SSE payload. `/api/index/progress-stream` is on the token exemption list and that field carries exception text, which for `OSError` embeds a full path.

### `files.extract_status` (`5ab3e78`)

A deliberately-skipped binary and a page deferred to OCR both keep their *real* digest with zero chunks, so the two were indistinguishable in the database. `files.sha256` was carrying the reason alongside the digest and could not carry all of it — the digest has to stay real, because it is the OCR cache's content key.

New column, added by the existing additive migration (`app/storage/db.py:322`), so an index built before this gains it without a rebuild. Vocabulary: `""` produced content, `binary` / `unreadable` / `encrypted` stub-skipped, `ocr_pending` deferred to OCR, `nocontent` non-empty but yielded nothing, `empty` 0-byte source, `error`, `cancelled`.

**Scope deliberately tight: populated and queryable, with no API or UI surface yet.**

---

## ⚡ Memory and throughput

### Token-budget batching (`61eaa23`)

Peak working set during ingestion was **1307.1 MB above idle** on a real 403 MB PDF/PPTX corpus, while the reference fixture stayed at 425.9 MB. The fixture was not representative: `perf_corpus` averages 5.1 chunks/file, a real Documents folder 142.9.

Attributed by stage ablation rather than argument. Three hypotheses died to measurement first — chunk volume (near-equal counts, 3x peak gap), `index_concurrency` (a 4x cut moved peak 6%), and LanceDB buffering.

| ablation | peak above idle | wall clock | delta |
|---|---|---|---|
| none | 1307.1 MB | 916.4 s | — |
| **embed stubbed** | **318.6 MB** | 175.8 s | **−988.5 MB (−76%)** |
| lancedb stubbed | 1290.1 MB | 826.9 s | −17.0 MB (−1%) |
| both stubbed | 301.6 MB | 171.8 s | −1005.5 MB |

The mechanism is the one `app/config.py` already named: the tokenizer pads each batch to its longest member — `enable_padding` has no `length=` — so cost tracks `rows × width`, not row count. Measured 0.140 MB per (row × token): 64 rows × 110 tokens predicts 986 MB against 988.5 observed.

`_length_sorted_batches` now takes a `char_budget` capping `rows × width-of-widest-row`, wired through **`settings.embedding_batch_char_budget`** (default 10240, ~2000 token-slots at the measured 5.09 chars/token; `app/config.py:104`). `embedding_batch_size` stays the row cap, so the budget only ever narrows.

| corpus | before | after | delta |
|---|---|---|---|
| College (403 MB, 151 files, 21,584 chunks) | 1307.1 MB | **569.4 MB** | **−56%** |
| `tests/fixtures/perf_corpus` | 425.9 MB | **302.5 MB** | −29% |

**Throughput improves rather than regresses** — 171.3 texts/s at budget 10240 against 148.1 with none (+16%) on 3000 realistic 528-char texts, because wide padding wastes compute.

> A prior claim that padding was fixed-length and therefore "provably bounded" is **retracted**. It was false against the code, and the code says so itself.

### PPTX read from the OOXML parts (`28ae790`)

`Presentation()` built an lxml element tree for every part in the package before a single character was read — the tree, *not* embedded media, which is only 0.1 MB against 0.15 MB of slide XML in a typical deck. `PptxExtractor` now reads the zip's XML parts directly, parsing one slide at a time and dropping it before opening the next, so peak is bounded by the largest single slide rather than by the deck.

| measurement | before | after |
|---|---|---|
| 42 real decks, extraction only | 90.0 MB | **2.2 MB** (41x) |
| PPTX-only ingestion floor | 111.9 MB | **30.2 MB** (−73%) |
| PPTX-only wall clock | 1.5 s | 0.8 s |

**Equivalence is asserted, not assumed.** Hand-rolled XML replacing a library is exactly the change that loses content silently, and the tests that existed mocked python-pptx out entirely, so they could not have caught a regression. `tests/test_pptx_extractor_stream.py` keeps a verbatim copy of the old implementation and diffs against it — 42/42 identical over 42 real lecture decks, *after* it caught a genuine defect where reading every text frame on a notes part appended the slide-number placeholder to every note.

**An XML-bomb vector was closed on the way past.** Both `xml.etree` and lxml expand internal entities — 30,000 characters from a 9-line prolog at depth 4, scaling geometrically — so python-pptx carried this too, and the archive guard bounds declared decompressed size but cannot see post-parse expansion. `_parse_part` refuses any package part declaring a DTD, which OOXML never legitimately does.

`scripts/generate_perf_corpus.py` now emits 150 `.pptx` decks. The fixture had none, which is why every memory number taken on it missed the most expensive extractor in the pipeline.

### Measured end state (2026-08-20)

| Constraint | Kind | Measured | Bound | Status |
|---|---|---|---|---|
| Idle / serving queries | hard ceiling | 195.7 MB | 250 MB | holds |
| Ingestion, peak above idle | transient cap | 535.3 MB | 1 GB | holds |
| OCR worker rasterization arrays | hard ceiling | 76.3 MB @ 20 MP | 100 MB | holds |

Method: `GetProcessMemoryInfo` working set sampled at 5 Hz on a thread, phase-labelled — `scripts/profile_ingest_memory.py`. **Not** `tracemalloc`, which cannot see the ONNX arena or numpy, i.e. cannot see the cost.

---

## 🔧 The indexer hang (`0cdfa23`)

A run over a 403 MB corpus indexed all 151 files, wrote 21,584 chunks and `folder_profiles`, closed the database — then **sat for 61 minutes at zero CPU** until killed. The same pipeline serves `POST /api/index/start`.

**Root cause is a non-daemon thread**, not the event loop and not the executors. `aiosqlite.Connection` starts its worker with `Thread(...)` and no `daemon=True`, so any unclosed connection blocks `threading._shutdown`, which runs *after* the `concurrent.futures` atexit hook. The unclosed connection came from the OCR enqueue path reaching into the API layer's on-demand `get_ocr()`, which pulls in a **second** `DatabaseManager` on the same file. `app/main.py` closes its own singleton, so the FastAPI app was never affected — the eval harness and `scripts/` build their own manager and never touched it. Only reachable with OCR enabled *and* a document that defers pages, which is why a corpus of `.md` and `.py` never showed it.

Three further teardown defects fixed alongside, each negative-controlled:

* The pipeline's queue handoffs were untimed, and the retry loops only tested `progress.is_cancelled` — so a *task* cancellation with the run still live stranded a worker for the life of the process. Reverting this makes pytest itself unable to exit.
* `_get_read_conn` wrapped its cleanup rollback in `suppress(Exception)`. `CancelledError` is a `BaseException`, so a cancellation there escaped and skipped the `put`, losing a pooled connection permanently. `close()` also now closes borrowed connections. A 30 s acquisition bound was added so starvation raises instead of parking silently.
* `index_folders` fired `wal_checkpoint` into a task set that only the FastAPI lifespan drains, and which *cancels* rather than awaits — so the checkpoint either never ran or raced `close()`, leaving the WAL un-truncated, which is the one thing the call exists to prevent. Now awaited inline.

Verified end to end: the run prints its report and **exits 0 in 818.9 s**, leaving no temp directory behind.

---

## 🔒 Security and privacy

* **Provider credentials no longer leak into error bodies** (`c695415`). `chat_passthrough` interpolated the raw exception into the client-facing 502 detail; for httpx errors `str(e)` typically includes the full request URL, so an `openai_compatible` base URL carrying embedded credentials (`https://user:token@host/v1`) landed verbatim in the response body.
* **Cloud privacy consent gates on the resolved destination** (`3555f6c`, `9f52499`), not on the provider's declared *kind*. A provider registered "local" but pointed at another host was exempt forever. This closed a live defect on the existing text-chat path, before OCR ever sent an image anywhere.
* **The local access token is header-only** (`d56b94e`, S-1). The middleware accepted `?token=` on every `/api/` path as a fallback; query strings reach uvicorn's access log, browser history and `Referer`. It had no producers — the frontend sends the header on every request and only *reads* `?token=` off the page URL.
* **Destructive endpoints are rate-limited** (S-2). `/index/start` carried 3/minute; `/clear`, `/cleanup`, `/export` and `/folder/remove` carried nothing, and `/clear` wipes the index. `/index/status` is deliberately left unlimited and pinned by a test — LibraryPage polls it every 10 s, so any limit under ~10/minute would break idle browsing.
* **Content-Security-Policy on browser-served pages** (S-3), mirroring the policy Tauri already ships, with `script-src` tightened from `'unsafe-inline'` to a per-request nonce. Governs the browser path only: Tauri loads the SPA from its own bundle at `tauri://localhost`.
* **Query history length bounded** (S-4). This bounds the context budget, not memory — the request body is fully parsed before any validator runs.

### The XSS path, and why the CSP was not the fix (`a3839b1`)

`MessageBubble` rendered the LLM's answer through `ReactMarkdown` with `rehypeRaw` — a plugin that exists precisely to re-enable the raw HTML react-markdown escapes by default. So HTML in the model's output became real DOM.

**That closes a loop with no remote attacker in it.** PMA indexes documents the user did not write. A chunk crafted to steer the model into emitting `<img src=x onerror=…>` gets that HTML rendered, and `window.__PMA_TOKEN__` sits in the same page authorising every `/api/` route, `/api/index/clear` included. **The attacker is a file in the corpus.**

Verified against a live server that the CSP blocks all three exfiltration channels — `connect-src` (fetch), `img-src` (an image-URL exfil, which needs no script at all and survives every JS-level defence) and `script-src-attr` (the `onerror` handler). But the CSP is the second layer, not the fix: `tauri.conf.json` ships `script-src 'self' 'unsafe-inline'`, so an inline handler **would** execute in the desktop app, which has no CSP backstop.

Closed at the source with **`rehype-sanitize` pinned 6.0.0** running *after* `rehypeRaw` (`frontend/package.json:32`). `rehypeRaw` could not simply be dropped — it is load-bearing: the LLM is instructed to wrap grounded assertions in `<claim sources="[n]">`, a capability detector probes whether a model can, and the components map turns those tags into the citation UI. Dropping raw HTML would have rendered them as literal text and silently killed the grounding feature. The schema therefore *extends* the GitHub default and adds only `claim`, `inference` and their `sources` attribute; `on*` handlers are not in the default allowlist and are dropped. Plugin order is negative-controlled — reversed, sanitisation runs before the dangerous nodes exist and the injection tests fail.

### Split-brain back-fill (`31f2e0d`)

Two independent defects in a path that **had never been tested**, because the suite builds its client with `ASGITransport` and no lifespan context, so startup never runs.

1. `emb_svc.model` does not exist on `EmbeddingService` → `AttributeError` before the loop ever ran. Caught by the function's own `except`, which raised a red banner in the UI — so this failed loudly, not silently.
2. Behind it: `LIMIT ? OFFSET ?` over a **self-consuming** predicate (`WHERE ce.chunk_id IS NULL`) while advancing the offset. Every row embedded left the result set, so the next offset stepped over an equal number of never-embedded chunks. Reproduced through the real function: 23 chunks at a batch of 5 left **10 of 23 unembedded** while reporting success.

Defect 2 was latent behind defect 1, and `lancedb_mode` defaults to `portable`, so neither reached a default install.

### Query stream bounds (`18c2459`)

The NDJSON stream had no upper bound. The keepalive frame proves the socket is open, not that the model is producing, and `is_disconnected()` only resolves once the ASGI server delivers the disconnect — which behind a buffering proxy can lag for minutes while tokens burn. `settings.query_stream_timeout_s` (180 s, `app/config.py:325`) is checked per loop iteration; generous on purpose, since a local 3 tok/s model on a long answer is legitimately slow.

`save_query()` moved out of the background task and is awaited before the response, because the history row is user-visible at `GET /api/query/history` and a task scheduled after the response can be lost to a shutdown. Its failure is caught rather than raised — the caller already has an answer and should not lose it to a logging problem. Telemetry stays backgrounded.

---

## 🎯 Retrieval quality (`9fe92f3`)

Two findings, the second only visible because of the first.

**RRF ties fell back to dict insertion order.** The summary leg gives every chunk of a file the same contribution, so it injects large blocks of exactly-equal scores, and Python's stable sort then resolved them by chunk id and LanceDB result order — both of which change between index builds of *the same corpus*. A single build was a point estimate of a random variable: the summary ablation measured −0.028, −0.042 and −0.278 on three builds of an identical corpus. Ties now break on chunk id, and every ablation aggregates over several independent builds.

**With the measurement trustworthy, `rrf_summary_weight = 0.3` turned out ~6x too high** — it cost recall against not using the leg at all. Swept over 4 independent builds:

| weight | recall | nDCG |
|---|---|---|
| leg off | 0.979 | 0.985 |
| **0.3 (shipped)** | **0.868** | **0.873** |
| 0.1 | 0.993 | 0.944 |
| **0.05 (new default)** | **0.993** | **0.995** |
| 0.02 | 0.993 | 0.995 |

The signal was never at fault; the scale was. At 0.3 a top-ranked file contributed `0.3/(rrf_k + 1)` = 0.004918 to *each* of its chunks against a semantic hit's `0.6/(60 + r + 1)` — outweighing ~61 ranks of chunk-level evidence, more than a 44-chunk corpus contains. The leg stopped breaking ties and became a near-binary "is this file in the top 5 summaries" flag overriding chunk search. `rrf_k = 60` on a **5**-element list compounds it: rank 0 and rank 4 differ by 6%.

**0.1 is a trap worth naming.** Recall looks excellent while nDCG collapses, because promoting a file's chunks as a block reorders them against each other. A first pass measured recall alone and would have shipped it. **Sweep both metrics, never recall alone.**

Two eval assertions encoded claims the corpus cannot support and were rewritten as non-regression: the summary test had been failing for as long as the multi-build fixture existed — unnoticed because the eval suite is `-m eval` and deselected from both CI gates — and the balancing test **passed only because the summary weight was wrong**.

---

## 🩺 Health, platform and UI

* **`/api/health` reports optional-subsystem startup state** (`1e6b530`). OCR, the folder watcher and the reranker each start inside a `try` whose `except` only logs — correct policy, but it left the outcome invisible outside the console. The reported symptom was "my PDFs never get text", noticed days later. Four states, not two: `up | down | disabled | unknown`, because `ocr_enabled` defaults to `False` and reporting a switched-off feature as `down` would warn on every stock install. `status` deliberately still means `model_ready and db_ok`.
* **Every file on a network share was silently skipped** (`1e4a6d0`). `canonicalize` returns extended-length paths, and the code stripped the `\\?\` prefix with a 4-character trim — right for `D:\a\b.txt`, wrong for a UNC path, which Windows canonicalizes to `\\?\UNC\server\share\x`. The strip left the *relative* string `UNC\server\share\x`, which `Path.absolute()` then glued onto the process working directory, inventing a path inside the repo that has never existed. Change detection hit the `OSError`, filed the file under skipped, and logged nothing naming the cause. Only reachable when `rust_core` is importable, which is the default — the Python fallback scanner handles UNC correctly.
* **The Explorer tree is grouped by the indexed folder root** (`2a1052e`). `files.folder_tag` holds only the folder's *basename*, but the UI treated the group key as a full path prefix, so the whole absolute path rendered nested under the folder it belonged to. Two consequences beyond the display: **the delete-folder button was dead while still alerting success**, because it sent a name where a path was expected; and two folders sharing a basename (`D:\a\College` and `E:\b\College`) collapsed into one root, making removal ambiguous. New `files.root_path` column via the additive migration. Rows predating it carry `''` and fall back to the longest shared *directory* prefix in their tag group — that fallback cannot separate two folders sharing a basename, and only a re-index can.
* **Local providers can be started from the UI** (`ca755a2`). A hardened launcher module, `/providers/{id}/launch_status` and `/providers/{id}/launch`, with Windows job breakaway in Tauri so a launched provider outlives the PMA sidecar.
* **The Insights page grew without bound** (`9f52499`). A `flex-1` on a grid whose parent is a block box was inert, so the row auto-sized, the panel's `h-full` could not resolve, and the canvas stayed at its attribute size — which the `ResizeObserver`, observing that same canvas, then multiplied by DPR on every cycle. Verified live at DPR 1.75: an 8000px backing store now moves layout 0px. Also fixed the WebGL2 renderer applying DPR twice and the WebGPU renderer seeding its canvas in CSS px.

---

## 🧹 Repo hygiene

* **Both CI gates restored to green** (`495b580`) after an unrecorded regression: 6 `ruff check` errors and 9 unformatted files, none of them in the change that preceded the commit. Two of the lint findings were real — a drain loop importing `progress` and never reading it, which is why the guard its comment describes turns out not to exist, and an unused numpy import in the rasterizer.
* **SQLite runtime artifacts untracked** (`a8df42a`) — `pma_metadata.db-shm`/`-wal` were tracked despite `pma_metadata.db*` already being gitignored, so they churned on every run. `graphify-out/` was untracked and unignored.
* **Six CI artifacts untracked** (`84b11a6`, `3be788a`) — `pytest-report.xml`, `bandit-report.json`, two `sonar-issues.json`, `ruff-report.txt` and `frontend/eslint-report.json`. Every gate run rewrote them, so they showed up as spurious working-tree changes to be manually excluded from each commit. `git rm --cached` only; the files stay on disk.

> The recurring lesson, worth stating once: **a green-gate claim is evidence about when it was written, not about now.** Establish the baseline yourself before attributing a failure to new work.

---

## 🧩 Provider trust surface, model provenance and settings integrity

The 2026-07-28 → 2026-08-03 stretch, which precedes the P0 sprint and is the older half of the delta against `main`.

### Settings integrity (`245ec8d`, `39c4146`)

`app/settings_store.py` became the single atomic, schema-versioned reader and writer for `data/settings.json`. Two ad-hoc readers still bypassed it, each with a different silent failure mode, and routing them through exposed a **live destructive write**:

* `set_preferences` did `data["llm"] = {...}` — replacing the whole sub-dict on every call. The providers endpoints write `llm.fallback_chain` and `llm.per_provider` into that same sub-dict, so any POST to the deprecated-but-live `/api/llm/preferences` silently destroyed both. And because the bypass never stamped `schema_version`, fallback-chain resolution then bailed to defaults, because it requires the current schema version.
* `app/api/models.py` shadowed the module-level `SETTINGS_PATH` (so a test monkeypatching the real one missed this copy), wrote non-atomically, and returned `{}` on corrupt JSON — making a corrupt file **indistinguishable from first run**, so the next POST silently overwrote it. It now raises 500, matching the precedent already set in `app/api/providers.py`.

### Provider resolution (`245ec8d`, `30caf4b`, `12e083e`, `4c08a3b`)

* **Local-first default fallback chain**, with stale saved chains ignored rather than honoured.
* **Reachability is required, not assumed.** Ollama and LM Studio are reported configured only if their local endpoint actually answers. A short TTL cache plus async wrappers keep that probe off the event loop during LLM flows.
* **The `PYTEST_CURRENT_TEST` production sniff was removed** — reachability carried an in-code shortcut keyed on running under test, which is exactly the kind of branch that makes a green suite mean nothing. A fixture mocks local reachability instead.
* Anthropic base URL doubling (`/v1/v1/`) fixed; rate limiting added to the passthrough.
* `EmbeddingService.is_ready` now requires a live session *and* no load error, with `has_failed` / `load_error` accessors, so a failed cold start can no longer read as ready.
* Cloud consent enforced on Gemini preferences, and the Providers page copy states the privacy and regional-availability position.

### Model pinning and provenance (`30caf4b`, `d22bf91`, `12e083e`)

* **Fail-closed pinning**: lockfile contents validated, sha256 digests **required** for pinned tokenizer and ONNX files, exact pinned-path matching enforced before checksum verification, deterministic ONNX candidate resolution order, and an offline-only mode. Repinned to the Xenova quantized ONNX INT8 build of `bge-small-en-v1.5`.
* **Drift detection**: `model_signature` (`repo_id@revision:resolved_file`) is computed from the resolved ONNX file and the lockfile entry, persisted in a new `system_state` table, and compared on every startup. A changed vector space without a re-index now warns loudly instead of silently returning wrong neighbours — existing LanceDB vectors would be stale. `load_model()` also gained a `repo_id` override for pinned third-party re-exports, and raises a clear error when a pinned revision stops resolving.

### Corrupt blobs no longer become searchable text (`591672c`)

`_zlib_decompress_fn`'s except branch returned `str(blob)` — the Python *repr* of the raw bytes, e.g. `b'x\x9c...'`. That string flowed straight into `chunk_fts` through the insert/delete triggers, indistinguishable from real content, so a corrupt or truncated blob became searchable garbage instead of surfacing as an error. It now logs at ERROR and returns `""`.

The fix also exposed a latent test defect: a test inserted raw uncompressed bytes into `text_preview` and **passed only because the old `str(blob)` fallback preserved enough of the original text inside the bytes repr** for retrieval to still match.

### The Creative-module license event

Worth recording as one arc rather than two commits. `397b8fb` and `4c08a3b` (2026-07-30) added `/api/modules/ws` Creative actions — Houdini scene ingest, project-scoped and cross-project query, project listing — backed by DB chunk storage and a `generate_raw()` path. `e3d62bd` (P0-2, 2026-08-03) removed all 257 lines as a **license-boundary violation**: Creative-module code has no place in MIT Core.

Confirmed dead before deletion — the private Creative module runs its own server and its own database, and reaches Core through exactly two HTTP endpoints, `POST /api/llm/chat` and `GET /api/providers`, both untouched. Core's `/ws` extension point, its token auth and ping/pong all stay.

**The removal exposed a real bug.** The malformed-frame handler caught bare `Exception` and unconditionally `continue`d, so any persistent non-JSON failure looped forever instead of reaching `close(code=1011)` — a test for it had been hanging indefinitely. Narrowed to `(json.JSONDecodeError, KeyError, UnicodeDecodeError)`.

Also removed: a dead `DROP TABLE IF EXISTS unreal_project_facts` from `clear_all()`, a second game-engine artifact with no creator anywhere in Core. `scripts/purge_creative_module_rows.py` is a one-shot, dry-run-by-default cleanup for orphaned `files` rows the old handler wrote under `path='houdini://…'` — those carried a corrupted `size` (it stored `len(chunks)`) and an uncompressed `text_preview`, unlike every other row in the table.

---

## 🖼️ WebGPU renderer overhaul (`1030b95`, `397b8fb`)

* Device-loss and fallback handling corrected.
* Crystal variant, motion and picking corrections, plus shader pipeline fixes.
* Scratch-buffer reuse for visible-set building.
* Per-session local tokens for dev startup.

Later in the cycle `9f52499` fixed the DPR feedback loop that was making the Insights canvas grow without bound; see **Health, platform and UI**.

---

## P0 hardening sprint (2026-08-03)

Four P0 defects found during an audit of the `updates` branch.

### Embedding batch size and ONNX session tuning (P0-4)
- **Batch size**: `embedding_batch_size` 512 → 64. The tokenizer's `enable_padding` has no `length=`, so every batch pads to its own longest sequence (BatchLongest) — at 512 that was a ~262k-token worst case.
- **`intra_op_num_threads`**: removed the `min(4, cpu_count-1)` override. ORT's default (`0`) resolves to physical core count *with* thread affinitization, which the override discarded while also hard-capping at 4 threads.
- **`enable_cpu_mem_arena`**: `True` → `False`. The arena grows to its high-water mark and never shrinks. Measured on a synthetic 2000-text corpus (mixed 15-400 word lengths, batch_size=64): **3848 MB → 172 MB peak RSS (22x)** for a 9% throughput cost (22.12 → 20.06 texts/sec). Disabling `enable_mem_pattern` in the same pass gave no further memory benefit (173 MB, within noise) but roughly halved throughput (→ 10.19 texts/sec), so it stays on.
- **End-to-end validation** (`scripts/benchmark_ingestion.py`, full 5150-file / 26354-chunk corpus): **62.74 → 159.12 chunks/sec (2.5x)**, peak RSS 722.96 → 745.68 MB (+3%).

### Also in this sprint
- **Ollama base URL** (P0-1): `config.py` defaulted to the legacy `/api/generate` path, which every Ollama request then appended its own path onto (`/api/tags`, `/api/chat`, …), 404ing everything and silently falling through to cloud providers despite Ollama running. Fixed, with a normalizer for stale `PMA_OLLAMA_URL` values.
- **License boundary** (P0-2): removed 257 lines of Creative-module RAG handlers from `app/api/modules.py` (MIT Core) — confirmed dead, since the private Creative module runs its own server and never calls Core's WebSocket.
- **Extractors** (P0-3): EPUB now reads the OPF spine (reading order) instead of alphabetical filename order — `chapter10.xhtml` sorts before `chapter2.xhtml`; PPTX tables and speaker notes are no longer silently dropped; CSV's row cap logs when it truncates instead of failing silently; `pdf_extractor`'s bare `except Exception` narrowed, so a corrupt PDF no longer indexes as empty with no signal. **`.epub`/`.pptx`/`.xlsx`/`.xls` were also added to `supported_extensions`** — the scanner filters on that list before a file reaches an extractor, so none of these formats was reachable from a normal folder scan. Existing indexes need a re-index to pick them up.

---

## ⚠️ Known limitations at `1e4a6d0`

Stated flat, because the alternative is someone rediscovering them:

* **The ingestion boundedness invariant is improved, not satisfied.** Peak-above-idle should be a function of the tunables alone — `index_concurrency`, `embedding_batch_size`, `max_length` — and of nothing about the corpus. Two corpora still differ.
* **The OCR savepoint's isolation is partial by construction.** The write lock is released between the savepoint calls, so an interleaved indexer commit can still make partial work durable. That is a property of sharing one write connection; fully solving it needs a second write connection and `SQLITE_BUSY` handling.
* **The summary-routing signal is inert on an index built before it landed** until `scripts/reindex_embeddings.py` runs. An operational migration step, not a bug.
* **The eval corpus is 12 queries across 24 documents** and is saturated at k=5. Treat every `improves` assertion in `tests/test_eval_retrieval.py` as suspect until it is bigger — three have already been found asserting things this corpus cannot show.
* **`files.extract_status` has no API or UI surface.** Populated and queryable only.
* **OCR Tier 2 does not fit the 4 GB VRAM design target** (~5.2 GB measured).
* **`init_db` cannot open a genuinely pre-migration database.** `schema.sql` creates an index on `files(path, modified_at, sha256)` and `executescript` runs before the column migrations, so a `files` table predating `sha256` fails outright. Pre-existing and not fixed.

---

# Changelog v0.0.71

Personal Memory Assistant v0.0.71 represents a major milestone release. This update introduces **Multi-Provider AI model support**, **OS-level keyring credential security**, **Graph RAG architectural intelligence**, a **hardware-accelerated 3D WebGPU visualizer**, **zero-loss streaming ingestion**, and an **extensive test suite overhaul**.

---

## 🌟 Executive Highlights

* **Multi-Provider AI Ecosystem**: Seamlessly switch between OpenAI, Anthropic (Claude), Google Gemini, OpenRouter, and custom local AI models directly within the new Provider Settings window.
* **Enterprise Credential Security**: API keys are now securely encrypted in the Windows OS Keyring instead of plain-text configuration files.
* **Graph RAG & Structural Intelligence**: Understands code dependencies and project architecture using AST-based graph extraction alongside traditional text search.
* **Next-Gen 3D Visualization Engine**: Explores codebases visually at 60 FPS with a new WebGPU instanced rendering engine and WebGL2 fallback support.
* **Zero-Loss Streaming Ingestion**: Processes repositories of any size (even 10GB+) without ever loading a whole file into memory. *(Corrected 2026-08-20: this line originally claimed "a constant ~60MB RAM footprint". That figure was never measured and is retracted — the measured steady state is 195.7 MB idle and ingestion peaks 535.3 MB above it, per `scripts/profile_ingest_memory.py`. See v0.0.72 → Memory and throughput.)*

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

* **Streaming Memory Footprint**: Scaled the document extraction and indexing pipeline (`app/indexing/service.py`) to process files of any size without holding one in memory. *(Corrected 2026-08-20: this line originally claimed "a fixed ~60MB RAM ceiling". That figure was never measured and is retracted — the measured steady state is 195.7 MB idle and ingestion peaks 535.3 MB above it, per `scripts/profile_ingest_memory.py`. See v0.0.72 → Memory and throughput.)*
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
- Streaming Memory Footprint: Processed files of any size (even 10GB+) without loading one into memory. *(Corrected 2026-08-20: this line originally claimed "a fixed ~60MB RAM usage". That figure was never measured and is retracted — the measured steady state is 195.7 MB idle and ingestion peaks 535.3 MB above it, per `scripts/profile_ingest_memory.py`. See v0.0.72 → Memory and throughput.)*
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

