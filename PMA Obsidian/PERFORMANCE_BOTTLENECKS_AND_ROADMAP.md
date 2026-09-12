# Performance Bottlenecks and Roadmap

**Last swept: 2026-09-12, branch `updates`, baseline commit `8cc4d37`.**

Every number in this document is either measured — with the command that
produced it — or explicitly labelled **unmeasured**. The previous version of this
file published targets and an "achievement" that no measurement supported; the
retractions are kept in section 5 rather than deleted, so the old figures cannot
be re-cited from git history without the correction attached.

Rule for editing this file: **no verification, no claim.** A number without a
file:line or a command output does not belong here.

---

## 1. Bottleneck ranking

### P1 — `StreamChunker` was O(n²) in fragment size — **FIXED this sweep**

`app/indexing/service.py` advanced its buffer by re-slicing it on every emitted
chunk (`self.buffer = self.buffer[overlap_start:]`). That is cheap when
fragments are small, and most extractors yield per page, row or paragraph —
`_extract_plain_text_stream` reads 128 KB at a time (`service.py:1247`).

`app/indexing/extractors/json_extractor.py:19` did not: for any `.json` over
500 KB it did a single `f.read(max_file_size)` and yielded the **whole file as
one fragment**, against `max_file_size_mb = 50` (`app/config.py:408`).

Measured at `8cc4d37`, `chunk_size=1024`, `chunk_overlap=102`:

| input | one fragment (before) | 128 KB stream | one fragment (after) |
|---|---|---|---|
| 1 MB  | 0.030 s | 0.003 s | 0.002 s |
| 2 MB  | 0.627 s | 0.015 s | 0.004 s |
| 4 MB  | 3.234 s | 0.029 s | 0.008 s |
| 8 MB  | 12.071 s | 0.066 s | 0.019 s |
| 16 MB | 46.440 s | — | 0.036 s |

Before: 3.85× per doubling — quadratic. Chunk *counts* were identical between
the two fragmentations, so the entire cost was waste, not work. Extrapolated to
the 50 MB `max_file_size` cap that is roughly **7.5 minutes of `memmove` for one
file**; the sweep did not run the 50 MB case, so that figure is an
extrapolation, not a measurement.

**Fix:** a read cursor (`_pos`) into the buffer, with compaction taken only once
the cursor has passed half the buffer, so each copy discards at least as much as
it moves and the total is amortised O(n). The single-fragment path is now linear
and matches the streaming path (ratio ~1.0×); 16 MB went 46.440 s → 0.036 s.

**Control:** the new chunker emits **byte-identical** output to the old one —
7359 chunks compared across 72 files of `tests/eval/corpus_large` and
`corpus_squad` at four fragmentations (whole-file, 128 KB, 997 B, 1 B), zero
mismatches. Offsets included, which matters because CLAUDE.md §7 records that
chunk offsets address the extracted text stream and
`tests/test_eval_corpus_spans.py` asserts it.

**Secondary fix:** `json_extractor` now yields in 128 KB blocks. This is about
resident memory, not CPU — a fragment the size of the document made peak memory
a function of the largest file in the corpus rather than of the tunables, which
is what the §6 boundedness invariant forbids. `chunk_buffer_max_chars`
(1 MB, `config.py:407`) only ever guarded the *buffering* path used for code
files, never the streaming path.

### P2 — Visualizer endpoints were unbounded — **FIXED this sweep**

- `app/insights/visualizer.py` — `SELECT id, path, type, size FROM files`, no LIMIT.
- same file — `SELECT path, type, size, usage_count FROM files`, no LIMIT, then
  one Python dict per file **plus one per ancestor folder**.

Both are reachable (`app/api/insights.py:148`, `:165`) and the frontend requests
both on every visualizer load (`frontend/src/api.ts:515`, `:541`). On a large
corpus that is two full table scans materialised into Python dicts, against the
§6 250 MB idle ceiling.

**Fix:** `settings.visualizer_max_nodes` (default 200,000, **unmeasured** — a
generous bound, not a tuned value) applied to both queries through one shared
`_ORDER_AND_LIMIT` constant.

> The two queries **must** share an identical `ORDER BY … LIMIT …`. The meta
> sidecar joins to the binary buffer by `hash_tree_path(path)`, so a LIMIT
> without a deterministic ORDER BY lets SQLite hand the two calls different
> subsets, which drops nodes from the join *silently* rather than failing.
> `tests/test_insights.py::test_visualizer_queries_agree_under_the_node_cap`
> locks this.

Truncation is logged, not returned in the body: the meta response is a
hash-keyed map the frontend reads by lookup, and adding a differently-shaped
sibling key would change a typed contract for a condition that needs a 200k-file
corpus to reach. The operator needs to know; the renderer cannot act on it.

### P3 — `attach_parent_windows` issued a sequential N+1 — **FIXED this sweep**

`app/search/retrieval.py` ran one `SELECT` per distinct `file_id`, awaited
serially, on the interactive query path. Bounded by result count, so small — but
the codebase already used the batched `IN (...)` form four times elsewhere
(`retrieval.py:656`, `:1057`, `:1583`, `:1876`).

**Fix:** one statement with per-file ranges OR-ed together, so each file still
fetches only the siblings its own window needs. Measured against the pre-change
function pulled from git:

| files in result set | `8cc4d37` | patched |
|---|---|---|
| 1  | 1 query | 1 query |
| 5  | 5 queries | 1 query |
| 20 | 20 queries | 1 query |

Expansion output identical in every case (all files still expand).

### P4 — Open, not fixed

- **Remote LLM generation latency** dominates p95 on any cloud provider. Not
  PMA's code; listed because it is the largest single contributor to perceived
  answer latency and no amount of retrieval work moves it. **Unmeasured here.**
- **`app/indexing/summarizer.py:48` and `:65` walk the same AST twice** —
  `ast.walk` for class names, then again for a docstring when the module has
  none. One pass would do. Bounded by `chunk_buffer_max_chars`, on the ingestion
  path, and **unmeasured** — noted, not scheduled.
- **Two inert PRAGMAs**, both already annotated in place:
  `app/storage/db.py:181` `PRAGMA page_size = 32768` cannot take effect —
  verified this sweep by replaying `_configure_conn`'s exact pragma order, it
  reports back `4096`, because WAL is enabled two lines earlier. `db.py:184`
  `read_uncommitted` has no effect on private-cache connections. Neither costs
  anything at runtime; removing them needs a VACUUM decision, not a perf case.

---

## 2. Measured baselines

Carried from CLAUDE.md §6 and §8.3 — **do not restate these from memory, and do
not quote a smaller number without re-measuring.**

| quantity | measured | bound | source |
|---|---|---|---|
| Idle / serving queries | 195.7 MB | 250 MB | CLAUDE.md §6 (2026-08-20) |
| Ingestion, peak above idle | 535.3 MB | 1 GB | CLAUDE.md §6 (2026-08-20) |
| OCR worker, rasterization arrays | 76.3 MB @ 20 MP | 100 MB | CLAUDE.md §6 |
| BEIR SciFact nDCG@10, reranker on | 0.70 | — | CLAUDE.md §8.3 |
| OCR first end-to-end run | ~1.3 s/page cold, ~0.5 s/page warm | — | CLAUDE.md §8.5 |

**Boundedness invariant (the constraint that actually governs ingestion):** peak
above idle must be a function of the tunables alone — `index_concurrency`,
`embedding_batch_size`, `max_length` — and of nothing about the corpus: not its
size, not its file count, not the size of the largest document in it. P1's
secondary fix exists because `json_extractor` violated exactly this.

Measure in portable mode. `.env` on the dev box sets
`PMA_LANCEDB_MODE=split_brain`, which enables a backup path a default install
never has; run profiles with `PMA_LANCEDB_MODE=portable` in the environment.

---

## 3. Verified-good — do not "optimize" these

Checked this sweep and found already bounded. Re-deriving a bottleneck here
wastes a session.

- **Context dedup is not N².** `context_builder.py:136` `_deduplicate_redundant`
  is hard-capped at 100 results (`:161`) and uses exact integer span arithmetic.
  The MinHash pass that motivated the old "N² dedup" entry was removed, and it
  ran in the wrong place besides — before the reranker, where it could drop a
  chunk the reranker would have promoted (`retrieval.py:484-488`).
- **Retrieval candidate building is capped** at 100 (`retrieval.py:494`), behind
  a 500-entry LRU cache, with all three legs (FTS, semantic, summary)
  running concurrently.
- **Reranker batches are bounded by padded footprint, not count**
  (`reranker.py:211-229`). Padding is batch-longest, so cost scales with
  `len(batch) × longest`; capping count alone would not bound it.
- **ONNX arena is off deliberately** (`reranker.py:169`, embedder likewise):
  3848 MB → 172 MB measured, for a 9% throughput cost. Do not re-enable.
- **SQLite is tuned**: WAL, a read-connection pool, `mmap_size` 1 GB,
  `temp_store=MEMORY`, `busy_timeout`, `wal_autocheckpoint`
  (`db.py:161-187`).
- **Chat streaming is throttled** on the frontend
  (`frontend/src/hooks/useChatStream.ts:416-418`).
- **Explorer caps rendered rows** at `MAX_VISIBLE_FILES = 100`
  (`frontend/src/pages/ExplorerPage.tsx:31`), which is why virtualization has no
  measured case — see CLAUDE.md §6 on component libraries.

---

## 4. Hotspots to watch

Corrected — the previous list sent readers to the wrong file for dedup.

| file | what to watch |
|---|---|
| `app/indexing/service.py` | `StreamChunker` fragment handling; pipeline queue saturation |
| `app/indexing/extractors/*` | any extractor that yields a whole file as one fragment — that is the P1 shape |
| `app/search/context_builder.py` | `_deduplicate_redundant` (dedup lives HERE, not in retrieval) |
| `app/search/retrieval.py` | fusion, recall window, parent-window batching |
| `app/insights/visualizer.py` | both queries must keep the shared `ORDER BY … LIMIT` |
| `frontend/src/api.ts` | SSE stream lifecycle and AbortController cleanup |

---

## 5. Retractions

Claims removed from the previous version of this document, with what is actually
true. Kept so the old numbers are not re-cited.

| retracted claim | reality |
|---|---|
| "Adler32 Visualizer Hashing: 10x speedup in spatial coordinate generation" | `adler32` appears nowhere in the Python or Rust source. `visualizer.py` uses `zlib.crc32`. Already flagged at `04_AUDITS_AND_ISSUES.md:359`; the roadmap kept claiming it anyway. |
| "Zero-Loss Streaming Pipeline … Constant 60MB RAM usage even for multi-gigabyte files" | Retracted by CLAUDE.md §6. Measured idle is 195.7 MB. The 60 MB figure was never owned or measurable. |
| "Memory Ceiling: Indexing RAM < 100MB for any project size" | False. Measured ingestion peak above idle is 535.3 MB, and "for any project size" is the boundedness invariant, not a byte count. |
| "N^2 semantic deduplication in the retrieval layer" (ranked bottleneck #2) | Wrong layer and already designed out — see section 3. |
| "Indexing Speed: 50,000 files/min (HDD) / 250,000 files/min (NVMe)" | No measurement exists anywhere in the repo. |
| "Visualizer Load Time: < 1s for 4M nodes" | The endpoint had no pagination at all; 4M nodes would not have completed. Now capped at `visualizer_max_nodes`. |
| "Boot Sync Latency: < 5s for 100k chunks" | No measurement exists. |
| "Rust Chunker Integration … for 3x indexing speedup" | The "3x" was never measured. The actual chunker cost was the buffer re-slicing, now fixed in Python for a ~1290× improvement on the pathological case. A Rust port may still be worth it — but it now has to beat the fixed version, not the broken one. |
| Duplicated SLO block (the old section 4 listed the same five bullets twice) | Copy-paste artifact. |
| "military-grade security", "infinite architectural scalability", "Zip-Bomb Immunity" | Marketing. The zip-bomb *guard* is real and landed (CLAUDE.md §8.2); "immunity" is not a property anything here establishes. |

The old section 6 (token handoff / `PMA_DB_PATH` / sidecar retrieval) was
correctness and ops debt rather than performance, and moved verbatim to
**`PMA Obsidian/04_AUDITS_AND_ISSUES.md`**, under
"Tech debt moved out of PERFORMANCE_BOTTLENECKS_AND_ROADMAP.md (2026-09-12)".
The keyring item was re-verified there and still holds at `app/main.py:63`.

---

## 6. Service levels

There are **no measured SLOs for this project yet.** The previous version
published five; none had a measurement behind it and they are retracted above.

Setting real ones needs a defined corpus, a defined machine, and a repeatable
harness. What exists today that could seed them:

- `scripts/profile_ingest_memory.py` — working set by phase at 5 Hz. Note its
  phase table is **not** attribution: 96.4% of samples are labelled `embed`.
- `scripts/eval_retrieval.py` / `scripts/eval_chunking.py` — retrieval quality,
  not latency.
- `tests/fixtures/perf_corpus` (5151 small files) — **does not** exercise what a
  real Documents folder produces. Any memory result taken only on it is
  unrepresentative.

Until a latency harness exists, this section stays empty rather than aspirational.

---

## 7. Reproducing this sweep

```bat
:: chunker timing and the byte-identity control (the control needs the
:: pre-change implementation, e.g. `git show <old-rev>:app/indexing/service.py`)
set PMA_LANCEDB_MODE=portable

:: ingestion memory profile by phase
.venv\Scripts\python.exe scripts\profile_ingest_memory.py --corpus tests\fixtures\perf_corpus

:: the gates - both, in this order, and read SCRIPT_EXIT from the log
scripts\run_ci_checks.bat
scripts\Run-Tests.bat
```

Neither gate can run while the dev backend is up (`uv sync` cannot replace
`rust_core.pyd` while uvicorn holds it), and a running Vite preview server breaks
the Playwright stage because it adopts port 5173.
