# 09 — PMA Core Hardening: Session Handoff

**Written:** 2026-08-12. **Baseline commit:** `764d41e`, branch `updates`.
**All work below is uncommitted.** Nothing was committed, pushed, or branched.

This continues `08_NEXT_UPDATES_RESEARCH.md`. That document's findings were
re-verified from source before implementing; **§1 below lists where it was
wrong**. Read §1 and §6 before touching anything.

---

## 0. Where to pick up

The user is **reindexing their corpus now** (it was empty as of this session —
see §5.1). Once indexing finishes, the next task is:

1. User names the folders indexed and which subset becomes the eval slice
   (~200–500 files across several `folder_tag`s).
2. Build that slice, draft ~40 labelled queries, get the user to confirm labels.
3. Record the Phase D baseline:
   `.venv\Scripts\python.exe scripts\eval_retrieval.py --labelled tests\eval\queries.json -k 10 --json-out baseline.json`
4. Only then start Phase G (F-2 → F-1 → F-4 → F-6).

**F-1, F-2, F-4 and F-6 are blocked on that baseline. Do not start them without it.**

---

## 1. Corrections to `08_NEXT_UPDATES_RESEARCH.md` (verified this session)

| # | Doc claimed | Actually |
|---|---|---|
| V-1 | — | **`tiktoken` + `datasketch` were undeclared and uninstalled.** `_token_count()` was `max(1, len(text)//4)`; both MinHash dedup passes were dead. The project's "context budget is the binding constraint" was enforced by a character heuristic. |
| V-2 | — | **`retrieval.py:1466-1477` hardcoded** `answer_evolution_diff = "Mock diff: …compared to yesterday's answer."`, rendered on **every** assistant message. A fabricated claim about the user's own history. |
| V-3 | P-4: "MinHash runs twice, ~6.4M hash ops" | **Neither pass ran.** Both were on `ImportError` fallbacks. |
| V-4 | R-2: short tokens "unfindable" (recall bug) | **Precision bug.** On venv SQLite 3.49.1, `"gardening" "tomatoes" "AI"` matched a doc containing no "AI" — sub-3-char tokens contribute *zero constraint*. |
| V-5 | eval harness has no CLI | **`scripts/eval_retrieval.py` already existed** — 248-line argparse CLI. |
| V-6 | F-2 code in `app/embeddings/service.py` | It is in **`app/indexing/service.py`**. |
| V-7 | R-3 is two call sites | `stream_rag` **inlines** the cache probe; fix surface was 1 API + 2 readers + 3 writers. |
| V-8 | R-8 is "≈1 hour" | As specified it **freezes the UI permanently** — `api.ts` swallows `AbortError` and emits no `done`. |
| V-9 | — | **`api/search.py`'s 15s keepalive cancelled its own generator.** `asyncio.wait_for` cancels what it awaits. Any >15s inter-token gap silently truncated the answer and skipped history/telemetry writes. |
| V-10 | F-6 rescores against float32 | `sqlite_embedding_backup=False` by default and the backup is **float16**. Real saving with rescore ≈47%, not 98%. |

**Retracted from this session's own first draft:** delta "D-4" (`_degraded` lost by
`_rebalance_after_rerank`) was a **phantom** — `_allocate_by_domain` provably
preserves index 0. The flag was still relocated, because R-4 *reorders and
truncates* that list and would have created the bug D-4 falsely reported.

---

## 2. What shipped

### Phase A — preconditions
- Declared + locked **`tiktoken`**. Dropped `datasketch` (see §3 decision).
- Deleted the fabricated "Answer Evolution" panel (backend + `MessageBubble.tsx`
  + the field through `useChatStream.ts` / `api.ts`).
- New `tests/test_dependency_integrity.py` — makes a missing dep a red test.

### P-4 (pulled forward from Phase F)
- One exact dedup pass: `context_builder._deduplicate_redundant` — normalized-text
  identity + same-file span overlap from `file_id`/`start_offset`/`end_offset`.
- Removed the candidate-stage pass entirely. Dedup now runs **once, after
  reranking**. Deleted both O(n²) `difflib` fallbacks.

### Phase B — stream lifecycle
- **B-1** `api/search.py`: `asyncio.wait` replaces `wait_for` so the keepalive
  cannot kill the generator. Added `request.is_disconnected()` and a `finally`
  that awaits the cancelled task before `aclose()`.
- **B-2** Stop button as a **client-side terminal transition** — one `finalize()`
  shared by `done` and stop, guarded by `settled`. Partial answer kept, badged
  `Stopped · partial answer`. The `AbortError` guard in `api.ts` was **not** touched.
- **B-3** `count_tokens_uncached()` — usage no longer reports a confident `0/0`,
  and whole prompts/answers stop polluting the 1024-slot `_get_tokens` LRU.

### Phase C — reranker signal chain (fixed order: R-7 → flag → R-6 → R-4+R-5 → P-6)
- **R-7** Rewrote `app/search/reranker.py`. Unavailability is now an **explicit
  propagated state** (`RerankerNotInstalledError` vs `RerankerFailedError`,
  `reranker_status()`). Resolves from the HF cache via `models.lock.json`
  (`local_files_only=True`), integrity-verified, fails closed on unpinned.
  New `reranker_allow_unpinned` (default `False`).
- **`_degraded` relocated** off `results[0]` onto every result.
- **R-6** Copied **`enable_cpu_mem_arena=False` only**. Did *not* copy
  `inter_op_num_threads=2` (dead under `ORT_SEQUENTIAL`; CLAUDE.md §7 forbids
  hardcoding) or `enable_mem_pattern` (already the ORT default).
  `_bounded_candidates()` caps by padded footprint, not count.
- **R-4** `_apply_relevance_cutoff()` — three-way on which scale ordered the list.
- **R-5** Sufficiency judged on the cross-encoder scale; new `"unverified"` status.
- **P-6** Cost guard only (`len(results) <= 1`).
- `tests/test_reranker_absence_rule.py` — 14 tests over all five absence paths.

### Phase D/E
- `scripts/eval_retrieval.py`: `--json-out` + provenance block.
- **R-1 + R-2** together: FTS switched from implicit-AND-over-every-token to
  **OR over stop-word-stripped `plan.keywords`**, sub-trigram terms dropped,
  empty-match reported. `keywords` threaded through `hybrid_retrieve` /
  `_gather_full_rag_inputs` and added to the retrieval cache key.
  **`FUSION_VERSION` 2 → 3.**
- **R-3** `query_cache` gained a `scope` column; probe uses `.where()`.

### Phase F
- **P-1** `pma_summaries` now indexed; `prune_query_cache()` (cap 5,000 + compaction);
  "clear history" drops the LanceDB cache and **reports** whether it succeeded.
- **P-2b** query-embedding cache stores float32 ndarrays.
  Measured: **25.20 MB → 4.50 MB** (20.7 MB, 5.6×).
- **P-3** `_mean_pooling` float64 → float32. Transient 100.7 MB → 50.3 MB.
- **P-5** Dedicated single-slot `_onnx_executor`; corrected the false
  "wait_for can cancel it" comment.

### Phase G/H/I (partial)
- **F-5** Planner routing: removed the bare-substring hijacks (`"my index"`,
  `"the index"`, `"give me a summary"`, `"what folders"`), added verb+noun
  composite and a topic-marker bail-out. **7 of 8 real hijacks fixed, 10/10
  inventory queries preserved.** Residual: `"how much space does the renderer use…"`.
- **F-3** Numeric parameter-size parsing + `model_class_overrides`.
  `llama3.2:1b` / `qwen2.5:0.5b` no longer get a 10k budget they cannot use.
- **U-2** `sonner` wired up (was a declared-but-unimported dependency);
  `confirm()`/`alert()` gone.
- **U-5** File tree refreshes off `subscribeProgress` SSE instead of a 15s poll.
- **H-1** Contentless FTS no longer selects an always-NULL column.
- **H-4** Cycle guard on `get_relational_paths`.
- **H-2 / H-3** Deleted `dist/sidecar` (2.3 GB stale copy of `app/` that poisoned
  greps) and the two `dependency-check-report.*` files (57 MB).
- **U-3 dropped** — L effort, and with zero users the telemetry it proposes to
  collect cannot answer its own question.

### Out-of-plan fixes found while running
- **Token injection.** The SPA was served as a raw static file, so a browser at
  `127.0.0.1:8000` had **no token source at all** and every `/api/` call 401'd.
  Only the Tauri shell worked. `main.py` now injects `window.__PMA_TOKEN__` for
  **loopback clients only** (`--host 0.0.0.0` must not hand out the token), with
  `Cache-Control: no-store`. `api.ts` reads it.
- **False startup log.** `preload_reranker()` swallows its own failure, so
  `"Reranker model loaded successfully."` printed unconditionally one line under
  `"Reranker unavailable"`. Now reports the real state.
- **Reranker pinned.** `Xenova/ms-marco-MiniLM-L-6-v2` @ `a09144355…`,
  `onnx/model_quantized.onnx`, 23.1 MB. Chose the mirror because the canonical
  repo's only quantized builds are `avx512`/`avx2`/`arm64`-specific (portability)
  and its portable build is fp32 at 91 MB. Embedder lock entry byte-identical.
- **Summary leg root cause.** It embedded *display scaffold*
  (`[MD: notes.md] Structure: …`), identical corpus-wide, so it ranked documents
  on what they share. New `summary_embedding_text()` strips it.
  Recall at weight 0.3: **0.819 → 0.917**. See §5.2 — still unresolved.

---

## 3. Decision log

| Decision | Alternatives | Why |
|---|---|---|
| Drop `datasketch`, do P-4 as offset-based dedup | keep it | It pulls **scipy: 97.9 MB, 357 modules loaded eagerly**, not excludable from the PyInstaller bundle. Not justifiable on a 4 GB-class target. **Cost accepted: cross-file *near*-duplicate detection is gone**; exact duplicates and same-file overlap still caught, exactly rather than approximately. |
| Absence is a third state | default `rerank_score` to 0.0 / −inf | 0.0 marks every sub-question satisfied on a reranker-less install; −inf reports "nothing in your files" for every question. Both are false claims. |
| Missing reranker ≠ degraded answer | flag it | It is true of *every* query on that install, so the badge would be lit 100% of the time and become noise. Capability state belongs in Settings; per-answer degradation is for timeout/failure. |
| R-4 uses an absolute floor, not a ratio | ratio on `rerank_score` | Logits are **signed**. Multiplying a negative top score by `score_multiplier` raises the bar and empties the context. |
| Reranker never downloads at runtime | mirror `embedding_allow_download` | Stronger privacy posture, and the plan's `reranker_allow_download` would have been dead config. Only the integrity gate exists. |
| Format only the 5 files I newly broke | format all 36 | 31 already failed at `764d41e`; reformatting them would bury the diff. |

**Binding rule — reproduce in code review:**

> No cutoff, floor, or threshold may be expressed on the raw cross-encoder logit
> scale unless the code has established the score is present *and* was produced
> by the pinned model. Absence is a distinct third state — "not assessed" —
> which disables the threshold and is reported. It is never defaulted to `0.0`
> and never silently read as "below threshold."

`rerank_score` is absent on five paths: `use_reranker=not (project or inventory)`
(fires on the bare word "summary"), timeout, inference failure, packaged builds
(`PMA.spec` bundles no `models/`), dev checkouts.

---

## 4. Verification state

```
pytest tests/            715 passed, 5 deselected
ruff check app/ tests/   All checks passed
npx tsc -b               exit 0
mypy .                   4 errors — ALL pre-existing, in files never touched
ruff format --check .    31 files — ALL pre-existing at 764d41e
```

**Pre-existing failures — do not attribute to this work.** `mypy`:
`app/settings_store.py`, `scripts/pin_models.py`, `scripts/memory_profiler.py`,
`app/insights/portrait.py`. `ruff format`: 31 files unformatted at `764d41e`.
**Both gates therefore fail on a clean checkout; `run_ci_checks.bat` stops at
step [1/4] on formatting.** Decide whether to run `uv run ruff format .` once.

Frontend vitest: **11 failures, all pre-existing** — proven by restoring the
three touched files from HEAD and getting identical counts. They fail on
unmocked `getPortrait()` network calls in Insights/Library/Settings/Explorer,
none of which import anything changed here.

**Use the two batch scripts, not bare pytest** — see CLAUDE.md §13.
Never run `uv sync` alone: it uninstalls the compiled `rust-core` PyO3 extension.

---

## 5. Open problems

### 5.1 The index was cleared
`pma_metadata.db` had 0 rows in `files`/`chunks` (166 MB file → bulk DELETE, not a
fresh DB) and `lancedb_cache/` had no tables, both at 02:35–02:36.
`chunk_embeddings` is empty too, so the split-brain fp16 backup is also gone.

Established as **not** the cause: the eval harness (uses `mkdtemp`, never the real
DB); this session's code changes (`clear_query_cache` drops only `query_cache`);
split-brain orphan reconciliation (`main.py:429-441` deletes from LanceDB only).
The signature matches `/api/index/clear`. **The trigger was not determined — do
not assert one.** Source files were never touched. User is reindexing.

### 5.2 The summary leg is fixed but still net-negative
After the scaffold fix, on `tests/eval/corpus`: OFF ≈ 1.000, ON@0.3 ≈ 0.917.
Better than the 0.819 before, **still worse than off**.

Do **not** conclude from this that the leg should be disabled. The corpus cannot
resolve the question — see 5.3. A document-routing signal only pays off where
chunk search picks the *wrong file*, which cannot happen across 24 files in 3
domains with recall already at ceiling.

### 5.3 The eval instrument cannot serve as a baseline yet
Two identical runs, same code and config:

| | recall OFF | recall ON |
|---|---|---|
| run 1 | 1.000 | 0.917 |
| run 2 | 0.972 | 0.889 |

**±0.03 nondeterminism**, recall at ceiling, 12 queries. The summary-leg gap is
only ~3× the noise floor. Two jobs: (a) find the nondeterminism (suspect LanceDB
ANN index or ingest ordering) — **not yet investigated**; (b) build a real corpus
slice (§0).

### 5.4 Not started
- **U-1 accessibility** — 6 `aria-*` attributes in 2 files across ~12k lines; no
  keyboard path into the WebGPU canvas. L effort. `ExplorerPage.tsx` is the
  tractable route (already has `tabIndex` + Enter/Space).
- **F-4 tier 3** (LLM-generated contextual retrieval) — do tiers 1–2 first.
- **U-4 trust surface** — apply the three-bucket rule: capability state in
  Settings, per-answer degradation only when the capability existed and this
  answer missed it, provenance as neutral grey text. If the degraded badge fires
  on >~1 in 5 answers on a healthy install, it is misclassified.

### 5.5 Housekeeping
- `dependency-check-report.*` were **tracked**; their deletion is staged in the
  working tree and needs a commit. The 57 MB stays in git history regardless.
  Consider adding them to `.gitignore`.
- `dist/PMA-sidecar.zip` (387 MB) still present — untracked, regenerable, not deleted.
- `scripts/pin_models.py` **rewrites the whole lockfile**; every model must stay
  in `MODEL_SPECS` or it is dropped.

---

## 6. Gotchas that will bite a fresh session

1. **`_get_tokens` returns `[]` when tiktoken is missing — it does not raise.**
   Any `len(_get_tokens(x))` silently reports 0 and surrounding `except` blocks
   never fire. Use `count_tokens_uncached()` for one-shot text.
2. **`uv sync` alone removes `rust-core`.** `run_ci_checks.bat` is safe only
   because `maturin develop --release` follows it.
3. **The eval harness mutates global `settings`** (`db_path`,
   `lancedb_persist_dir`) and passes `use_reranker=False`. It cannot observe
   R-3, R-4, R-5, R-6, R-7 or P-6 — those are covered by unit tests instead.
4. **The reranker now fails closed.** An unpinned model will not load. Run
   `scripts/pin_models.py` after changing model artifacts.
5. **`FUSION_VERSION` must be bumped** on any change to fusion behaviour, or
   cached results from the previous ranking are served and the change looks inert.
6. **Serving the SPA:** `main.py` injects the token only for loopback. A frontend
   change needs `cd frontend && npm run build` (outputs to `static/react/`) before
   the served bundle reflects it.
7. **Metrics on `tests/eval` support direction of change only.** Never report a
   delta from it as an effect size.
