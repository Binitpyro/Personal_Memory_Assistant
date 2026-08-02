# PMA Implementation Plan — Execution Spec

Derived from `PMA_implementation_plan.md` (v2 brief, verified against `updates@12e083e`, 2026-07-30).
This document is the **executable** form: the brief decides *what and why*; this decides *how, in what order, and how we know it worked*.

**Today: 2026-08-02. Deadline: 2026-08-17 — 15 days remaining, not 16.**
Three days elapsed between the brief's verification date and this plan. The sequence below is rebased to real calendar dates.

---

## §A — Verification status of the brief

I re-verified every P0 claim against the working tree before writing this. Results:

| Brief claim | Status |
|---|---|
| §1.1 Ollama URL typo | **Confirmed — and worse than described.** See §A.1 |
| §1.2 `modules.py` license boundary, 349 lines, live dispatch | **Confirmed** |
| §1.3 EPUB `sorted()`, CSV `i > 5000`, DOCX `iterchildren()`, PPTX no tables/notes | **Confirmed, all four** |
| §1.4 `enable_padding` with no `length=`, `embedding_batch_size = 512` | **Confirmed** |
| §1.4 "Do NOT set `intra_op_num_threads`" | **Incorrect framing.** It is already set. See §A.2 |
| §4 P1 `settings.json` bypasses at two sites | **Confirmed** |
| §4 P1 `_zlib_decompress_fn` returns `str(blob)` on failure | **Confirmed** |
| §1.2 `unreal_project_facts` in Core | **Confirmed**, one site only |

### A.1 — The Ollama break is larger than the brief states

The brief describes a broken *health check*. In fact the misconfigured base URL breaks **every** Ollama code path, because all four request builders concatenate onto `base_url`:

| Site | Constructed URL (current) | Result |
|---|---|---|
| `app/providers/ollama.py:43` `list_models` | `.../api/generate/api/tags` | 404 |
| `app/providers/ollama.py:65` `validate` | `.../api/generate/api/tags` | 404 |
| `app/providers/ollama.py:125` `chat` | `.../api/generate/api/chat` | 404 |
| `app/providers/ollama.py:149` `stream` | `.../api/generate/api/chat` | 404 |

So even if the health gate at `app/search/llm_client.py:353-357` were bypassed, generation would still fail. The health gate is not the bug — it is the only thing currently producing a *legible* error instead of a 404 mid-stream.

**Three independent proofs this is a config typo, not a design choice:**

1. `app/providers/registry.py:137` — `default_base_url="http://localhost:11434"` (bare origin, correct).
2. `app/config.py:102` — `lm_studio_url = "http://localhost:1234/v1"` matches `registry.py:153` exactly, and LM Studio's health check works.
3. `tests/test_llm_client_providers.py:290` — `client.ollama_url = "http://ollama"`, a bare origin with no suffix. The test suite already encodes the correct contract.

**Second fix site the brief missed:** `.env.example:11` carries `PMA_OLLAMA_URL=http://localhost:11434/api/generate`. Fixing only `config.py` leaves every user who copied `.env.example` — i.e. anyone following the README — still broken. This is the more damaging of the two, since it survives the code fix.

### A.2 — `intra_op_num_threads` is already set

The brief says *"Do NOT set `intra_op_num_threads`"*, framed as rejecting a v1 proposal. But committed code at `app/embeddings/service.py:178` (HEAD) already does:

```python
options.intra_op_num_threads = min(4, max(1, (os.cpu_count() or 1) - 1))
```

The brief's reasoning still holds — ORT's default of `0` resolves to physical core count *and* enables thread affinitization, which this expression discards. But the action is **remove an existing line**, not **decline to add one**. On a 12-core machine this hard-caps ORT to 4 intra-op threads.

Likewise `options.enable_cpu_mem_arena = True` at `service.py:180` (HEAD) is already explicit, so §1.4's "test `False`" is flipping a stated value, not overriding a default. Both are folded into Task P0-4 below.

### A.3 — Line numbers in the brief have drifted

The working tree carries uncommitted changes: `service.py` +62 lines, `main.py` +38, `db.py` +19. Every line number in the brief for those three files is now low by roughly that amount.

| Brief reference | Working-tree location |
|---|---|
| `service.py:122-123` truncation/padding | `service.py:129-130` |
| `service.py:174` providers | `service.py:186` |
| `db.py:1481` `DROP TABLE unreal_project_facts` | `db.py:1500` |

**Rule for this plan: anchor on symbols, not line numbers.** Every task below names the function or statement. Re-grep before editing.

---

## §B — Pre-flight (do first, ~20 min)

The working tree has uncommitted work across 8 files, including a partly-finished `model_signature` feature in `service.py`. Starting P0 on top of this makes every task's diff ambiguous and every rollback risky.

1. `git diff` the four source files (`service.py`, `main.py`, `db.py`, `memory_profiler.py`, `tests/test_embeddings_service.py`). Decide per file: finish, commit as-is, or stash.
2. `pma_metadata.db-shm` / `-wal` are tracked and dirty. These are SQLite runtime artifacts and should not be in version control — add to `.gitignore` and `git rm --cached`. They will otherwise churn on every run and pollute every diff for the rest of this sprint.
3. `graphify-out/` is untracked — gitignore it.
4. Record the baseline: run the existing benchmark and capture **62.74 chunks/sec / 722 MB peak RSS** on *this* machine. The brief's numbers are the target of comparison for P0-4; if they were measured elsewhere they are not a valid baseline.

**Gate:** clean `git status` on source files before starting P0-1.

---

## §C — P0 tasks

### P0-1 — Ollama base URL (Aug 2, ~45 min)

Restores the local-first chain. This is the privacy claim, so it is first and not deferrable.

**Files**
- `app/config.py` — `ollama_url` field
- `.env.example` line 11 — `PMA_OLLAMA_URL`

**Steps**
1. Grep for any consumer relying on the `/api/generate` suffix:
   ```bash
   grep -rn "api/generate" app/ frontend/src/ tests/ scripts/ .env.example
   ```
   Expected: only `config.py` and `.env.example`. If a consumer appends nothing and expects the full generate path, it must be migrated to `/api/chat` — the provider has no `/api/generate` code path at all.
2. `app/config.py` → `ollama_url: str = "http://localhost:11434"`
3. `.env.example:11` → `PMA_OLLAMA_URL=http://localhost:11434`
4. Add a normalizer so a user who pastes a suffixed URL is not silently broken. Strip a trailing `/api/generate`, `/api/chat`, `/api/tags`, and any trailing slash, in the `Settings` validator alongside the existing `db_path` normalization. Log at WARNING when a suffix is stripped.

**Acceptance**
- With Ollama running: `_check_ollama_health()` returns `True`; a query resolves to the `ollama` provider without touching any cloud provider.
- With Ollama stopped: chain falls through to `lm_studio` with a legible `ProviderNotConfiguredError`, not a 404.
- Setting `PMA_OLLAMA_URL=http://localhost:11434/api/generate` still works (normalizer), with a WARNING logged.

**Tests**
- Regression test asserting `settings.ollama_url` has no path component.
- Parametrized normalizer test over the four suffix forms + trailing slash.
- Extend `tests/test_llm_client_providers.py` to assert the chain reaches `ollama` first when healthy — the current test sets `ollama_url` but does not assert chain position.

**Rollback:** single-line revert, no data migration.

---

### P0-2 — License boundary strip, `app/api/modules.py` (Aug 3–4, 2–3 h)

MIT-licensed Core currently contains 257 lines of Creative-module handler bodies, wired live by `4c08a3b`. This is a licensing fact, not a bug — it does not get deferred for schedule reasons.

**Blocking prerequisite — do this before writing any code:**
Grep the private Creative repo for how it reaches Core.
- If it communicates **only** via WS actions (`creative_ingest`, `creative_query`, `creative_cross_query`, `creative_list_projects`) → clean delete, handlers move to the private repo.
- If it imports Python symbols from `app.api.modules` → the contract must be stubbed in Core *before* deletion, or the private repo breaks on next pull.

This grep gates the whole task. If the Creative repo is not accessible today, start P0-3 and come back.

**Delete**
- `_handle_creative_ingest` (working tree ~L18–98)
- `_handle_creative_query` (~L101–184)
- `_handle_creative_cross_query` (~L187–255)
- `_handle_creative_list_projects` (~L258–280)
- The four `elif action == "creative_*"` branches in the WS dispatch (~L324–335)

**Keep:** the `/ws` endpoint, its token auth (`secrets.compare_digest` against `X_LOCAL_ACCESS_TOKEN`), the malformed-frame handler, `ping`/`pong`, and the unknown-action error response. The auth and framing logic is Core infrastructure and is correct.

**Data-model contamination to sweep in the same commit**
- `INSERT INTO files` with `type='houdini_hip'`, `path=houdini://{project}`, and `size` abused to store `len(chunks)` — goes with the handler.
- `INSERT INTO chunks` writing plain `str` into `text_preview`, surviving only via the `isinstance(blob, str)` passthrough in `_zlib_decompress_fn` (`app/storage/db.py:26-27`). Note this passthrough is what let the violation go unnoticed; see P1-2.
- `_handle_creative_query` reads `text_preview` **raw**, unlike every other read site (`retrieval.py:419,638,973`; `db.py:1065`; `main.py:266`), which all wrap `zlib_decompress`.
- `DROP TABLE IF EXISTS unreal_project_facts` at `app/storage/db.py:1500` — a second game-engine artifact. One site only; remove it.
- The unbounded LLM context builds (5 rows and 10 rows, no token ceiling) violate the FULL_RAG-only bounded-window rule. They leave with the handlers — but if any of this logic is reimplemented in the private repo, the token ceiling must come with it.

**Acceptance**
- `app/api/modules.py` contains no Houdini, Unreal, or `creative_*` reference.
- `grep -ri "houdini\|unreal" app/` returns nothing outside tests/fixtures.
- WS `ping` → `pong` still works with a valid token; still `1008` without one.
- Unknown action returns the error frame rather than raising.
- Existing rows: decide explicitly whether to leave orphaned `houdini://` rows in `files`/`chunks` or purge them. **Recommendation: purge in a one-shot script**, since they carry a corrupt `size` semantic and uncompressed `text_preview` that will confuse retrieval scoring. Do not route this through `clear_all()` (see P2).

**Rollback:** the deleted code is preserved in git history and, after this task, in the private repo. Reverting is a `git revert` — but reverting *reintroduces the license violation*, so treat this as forward-only.

---

### P0-3 — Extractors (Aug 5–6, ~4 h)

Ship the three that produce **visibly wrong retrieval output** in a demo. Slip the two that only degrade recall.

**Ship:**

| Format | Anchor | Fix |
|---|---|---|
| EPUB | `epub_extractor.py:36` `sorted(content_files)` | Read OPF spine order; fall back to `sorted()` only if the spine is unreadable, with a WARNING. Alphabetical order means chapter 10 precedes chapter 2 — retrieval returns text in the wrong narrative order, which is visible and embarrassing. |
| PPTX | `pptx_extractor.py` — zero matches for `GraphicFrame`/`notes` | Walk `shape.shape_type == MSO_SHAPE_TYPE.TABLE` (via `graphic_frame.table`) and emit cell text; read `slide.notes_slide.notes_text_frame` when `slide.has_notes_slide`. Tables and speaker notes are currently dropped silently — often the densest content on a slide. |
| CSV | `csv_extractor.py:26` `if i > 5000` | Replace the hardcoded row cap with a cap derived from the file-size budget (`settings.max_file_size_mb`) and/or a token budget. Log when truncation occurs — currently it is silent. |

**Slip to Aug 12–13 buffer (or post-deadline):**

| Format | Anchor | Defect |
|---|---|---|
| DOCX | `docx_extractor.py:23` `doc.element.body.iterchildren()` | Headers, footers, footnotes, endnotes dropped |
| PDF | `pdf_extractor.py:33` | Bare `except Exception`; no OCR path, no scanned-PDF detection |

Rationale for the split: DOCX and PDF *reduce* what is found. EPUB/PPTX/CSV *misrepresent* what is found — wrong order, missing tables, silent truncation. Misrepresentation is the demo risk.

**PDF caveat:** the bare `except Exception` at `pdf_extractor.py:33` is a 10-minute fix independent of the OCR work — narrow it and log. Do that even if the rest of PDF slips. A swallowed exception here means a corrupt PDF indexes as empty with no signal.

**Acceptance:** one fixture per format committed under `tests/fixtures/`, each asserting the previously-dropped content is now extracted — multi-chapter EPUB (spine order ≠ alphabetical), PPTX with a table and speaker notes, CSV over the cap (asserts the warning fires).

---

### P0-4 — Embedding batch size, padding, and session options (Aug 2, ~1 h + benchmark)

Runs same-day as P0-1 — it is config-only and the benchmark can run while P0-2's Creative-repo grep is pending.

**The defect.** `service.py` `_load_onnx_model` calls `enable_padding(pad_id=0, pad_token="[PAD]")` with **no `length=` argument**, which selects BatchLongest — dynamic padding to the longest sequence in the batch. Combined with `enable_truncation(max_length=512)`, one 512-token document inflates the whole batch to an `N × 512` activation rectangle.

Memory is therefore bounded by `batch_size × longest_seq_in_batch`, **not** by item count. With `embedding_batch_size = 512` (`config.py:61`, commented *"Doubled: modern GPUs/CPUs handle this well"*) the worst case is ~262k tokens — on a product targeting 4 GB VRAM / 60 MB RAM. A GTX 1650 user is running a batch size chosen for hardware they do not own.

**Changes (a safe value, not a tuned one):**

1. `config.py:61` → `embedding_batch_size: int = 64`. sentence-transformers defaults to 32; 64 is still generous. Update the comment — it currently documents the reasoning we are rejecting.
2. **Remove** `options.intra_op_num_threads = min(4, max(1, (os.cpu_count() or 1) - 1))` (`service.py:178` HEAD / ~190 working tree). Let ORT default to `0`, which resolves to physical core count with thread affinitization. The current expression hard-caps to 4 threads and discards affinity. See §A.2.
3. Test `options.enable_cpu_mem_arena = False` (currently explicitly `True` at `service.py:180` HEAD). ORT docs: default is `true`; `false` gives significant memory savings for **smaller models** at some latency cost. bge-small INT8 is 34 MB — squarely in scope, with a 60 MB budget to defend.
4. Benchmark all three against the §B baseline.

**Decision rule for the benchmark** (so this does not become an open-ended tuning exercise):
- If throughput at `64` holds within ~10% of baseline → keep `64`; the `512` was buying nothing and costing a 8× memory ceiling.
- If throughput drops >10% → try `128` before conceding. Do not go back to `512` regardless; the memory ceiling is the point.
- `enable_cpu_mem_arena=False` ships **only** if peak RSS drops meaningfully and throughput loss is under ~10%. Otherwise revert to `True` and record the number.
- Removing `intra_op_num_threads` ships unless it measurably *regresses* throughput.

Record all four numbers in `05_CHANGELOGS.md`. They are the "before" row for §D.1's token-budget work.

**Explicitly out of scope here:** fixing the padding strategy itself. Setting a fixed `length=512` would make every batch worst-case; the real fix is token-budget batching (§D.1), post-deadline. This task only bounds the blast radius.

**Note the dead seam:** `service.py:64` `self.optimal_batch_size = settings.embedding_batch_size` — a field named for a discovered value, assigned a constant. The seam exists and was never wired. §D.1 owns it. Leave the name alone for now; renaming it pre-deadline is churn.

---

## §D — P1 (Aug 7, ~2.5 h total)

### P1-1 — `settings.json` bypasses (~1 h)
`SettingsStore` (`app/storage/settings_store.py:11-42`) is correct — atomic write, schema-versioned. Two ad-hoc readers bypass it:
- `app/search/llm_client.py:173` — `pref_path = Path("data/settings.json")`
- `app/api/models.py:18` — `SETTINGS_PATH = Path("data/settings.json")`

Both read the file directly, so they see torn writes and ignore schema version. Route both through `SettingsStore`.
**Acceptance:** `grep -rn 'settings.json' app/` matches only `settings_store.py`.

### P1-2 — `_zlib_decompress_fn` silent corruption (~20 min)
`app/storage/db.py:30-31` — `except Exception: return str(blob)`. A corrupt blob's Python repr (`b'\x78\x9c...'`) gets indexed into FTS as searchable text. Change to: log at ERROR with the chunk id, return `""`.

Keep the `isinstance(blob, str)` passthrough at `db.py:26-27` **for now** — but once P0-2 removes the only writer of uncompressed strings, that branch becomes dead. Add a `# TODO(post-P0-2): verify no remaining str writers, then remove` comment so it is not forgotten. Removing it in the same commit as P0-2 risks breaking legacy rows written before the fix.

### P1-3 — `/api/llm/chat` audit (~1 h)
Endpoint is `app/api/models.py:178` — `@models_router.post("/chat")`, inside the **models** router, not a dedicated LLM router. Confirm: the actual mounted prefix, the auth model applied, and that no provider credential echoes into the response body or error strings. The Creative Module depends on this endpoint, so it survives P0-2 and must be correct.

---

## §E — Schedule (calendar, rebased)

| Date | Work | Gate |
|---|---|---|
| **Aug 2** | §B pre-flight; **P0-1** Ollama; **P0-4** batch + benchmark | Clean `git status` before starting |
| **Aug 3–4** | **P0-2** license boundary strip | **Creative repo grep** — hard blocker |
| **Aug 5–6** | **P0-3** EPUB spine, PPTX tables + notes, CSV cap | — |
| **Aug 7** | **P1** settings bypasses, zlib fallback, `/api/llm/chat` audit | — |
| **Aug 8–11** | **Paper 1** — quantitative results | Needs P0-4 benchmark numbers |
| **Aug 12–13** | DOCX + PDF extractors if time; else buffer | — |
| **Aug 14–17** | Paper 1 finalization + submission packaging | — |
| **post** | §D.1 token budget → §D.2 closed loop → §D.3 OCR → §F detection → §D.4 onboarding | — |

**Two scheduling notes:**

1. **Paper 1 moved earlier than the brief implies.** It is page-limited (4 pages, ACM two-column — not word-limited) and still carries placeholder quantitative results. Those placeholders need the P0-4 benchmark numbers, which land Aug 2. Starting the paper Aug 8 rather than "day 9+" gives it four clear days before the extractor buffer, instead of competing with it.
2. **If the Creative repo grep is blocked on Aug 3**, swap P0-2 and P0-3. Do not let a missing grep idle two days.

---

## §F — Post-deadline, in order

Unchanged from the brief in substance; restated here as sequence with entry conditions.

**F.1 — Token-budget batching** *(first; prerequisite for anything adaptive)*
Order: tokenize all → sort by **true token count** → pack until a token budget fills. Not `batch_size` items at fixed stride.
This is the industry unit — vLLM schedules on `max_num_batched_tokens` and chunks prefills that won't fit. sentence-transformers sorts by **character** length before tokenization, a decent but imperfect proxy (a 900-char chunk may yield 400 or 600 tokens). Runtime and memory for BERT-family encoders grow roughly quadratically with sequence length, so this is where 62.74 chunks/sec actually moves.
`service.py:64` `optimal_batch_size` is the seam to own — rename it here, not before.
**Entry condition:** P0-4 benchmark numbers recorded.

**F.2 — Closed-loop budget** *(optional, after F.1)*
Start conservative, sample RSS per batch, additively raise every N clean batches, multiplicatively halve on pressure. Precedent: Lightning's `BatchSizeFinder` (power scaling then binary search, with a `margin`); vLLM's KV-cache profiling pass.
Known failure modes, stated up front: Windows RSS is working-set and lags GC → needs smoothing and hysteresis or it oscillates; Linux's OOM killer gives no catchable signal → a coarse ceiling from total RAM is still required (it just need not be accurate); short jobs never converge → persist the converged budget as a **starting hint**, explicitly not a decision.
**Note:** there is currently *zero* memory observation in the backend — only a bare `gc.collect()` at `app/indexing/service.py:857`. Instrumentation is step one.

**F.3 — OCR ladder**
Policy decided; detection only picks a rung.
0. `pypdf` text-layer presence check (detection gate, no model)
1. PP-OCRv6 mobile ONNX — baseline for all hardware
2. GLM-OCR 0.9B — above the floor only
3. VLM — only if the §0.2 decision approves, opt-in only

`oar-ocr` (PP-OCR in pure Rust) is the strongest Stage 1 fit given `rust_core` exists. **Nothing is exposed in the UI until the engine behind it is written** — a dropdown selecting a `NotImplementedError` is worse than no dropdown.

**F.4 — Hardware detection** *(descoped; see §G)*

**F.5 — Onboarding**
Cut from pre-deadline entirely. `frontend/src/pages/SetupPage.tsx` (331 lines, 2 steps) stays as-is.
When it does change it recommends **downloads**, not settings — one or two decisions, not a matrix. If a step is inserted, the progress bar at L166–167 must become a mapped array; it is two hardcoded divs and will silently break.
Every recommended value writes as a **default the user can change**, with a stored rationale string. A user override must survive relaunch. Silently re-applying a recommendation over an explicit user choice is the same defect class as the persist-on-read bug already fixed at `providers.py:99-110`.

---

## §G — Hardware detection: design constraints (for when F.4 starts)

Carried forward so the resolved decisions are not re-litigated.

**Why it shrank.** Every setting the v1 profiler was justified by fell: `intra_op_num_threads` → ORT default is correct (and P0-4 removes ours); `embedding_batch_size` → not a detection problem, it's a padding problem fixed by F.1; reranker aggressiveness and index worker concurrency → speculative, no measurement supporting a change. Only **OCR / LLM download choice** survives — irreversible, expensive, no feedback possible.

Decisive: `app/embeddings/service.py` and `reranker.py:48` both hardcode `providers = ["CPUExecutionProvider"]`. ONNX inference in PMA is CPU-only, so VRAM is irrelevant to embedder and reranker. The 4 GB VRAM envelope applies **only** to the out-of-process LLM and to future OCR. A single global tier would have been actively wrong.

**No composite score.** Resources are not substitutable — 64 GB RAM and no GPU scores well on any weighting and still cannot load a VRAM-resident model. Model fit is binary. A score also destroys the only useful output: *which resource is binding*. `llmfit` uses a weighted score, but only to **rank what already passed a hard fit gate**. WinSAT reached the same conclusion in 2009 — `WinSPRLevel` is the **minimum** subscore, not an average.

> **Rule:** eligibility = per-resource AND-gate with a headroom factor. Selection = first eligible in a fixed preference order. Any score is display-only and never read back by logic.

Headroom is not optional. LM Studio's practical guidance is ~80% of available VRAM, and its own estimator is documented beta that doesn't fully account for KV cache growth. `profile[r] >= requires[r]` alone makes a 3.9 GB need "eligible" on a 4 GB machine, which then thrashes.

**Tooling.** CPU/RAM/disk → `sysinfo` crate in `app/scanner/rust_core` (MIT, 235 KiB, actively maintained; `serde` feature → JSON; rides the existing PyO3 bridge). This kills the psutil question — `pyproject.toml:56` carries only `types-psutil`, a dev stub, so psutil would be a genuine new runtime dependency on a project treating dependency minimalism as architecture. MSRV is a non-issue: `rust_core/Cargo.toml` declares no `rust-version`, the `1.77.2` in `frontend/src-tauri/Cargo.toml:8` is scoped to the Tauri crate, there is no `rust-toolchain.toml`, and CI runs `dtolnay/rust-toolchain@stable`. Two usage constraints from its docs: refresh before reading (values are diffs — keep one `System` instance alive), and it holds file descriptors open for process-refresh performance — use targeted refresh, not `new_all()`.

**sysinfo does not do GPU or VRAM.** VRAM → DXGI, not WMI: `IDXGIFactory1::EnumAdapters1` → `GetDesc1()`. `DXGI_ADAPTER_DESC1.DedicatedVideoMemory` is a `SIZE_T` (64-bit on x64, no truncation) and is vendor-neutral across AMD/NVIDIA/Intel, plus `VendorId`/`DeviceId` and `SharedSystemMemory` for integrated GPUs. This is the actual fix for the RX 580 problem: `Win32_VideoController.AdapterRAM` is uint32, so any GPU above 4 GB reports as exactly 4 GB, and `nvidia-smi` never sees AMD at all (llmfit concedes "Windows GPU detection currently focuses on NVIDIA"). Caveats: DXGI reports usable, not nameplate (a 4 GB GTX 770 reports 3.93 GB — the honest number), and feature-level-9 hardware returns zeros with "Software Adapter" as the description, so filter software adapters first. `src-tauri` already links `winapi 0.3.9` under `[target.'cfg(windows)'.dependencies]`; `rust_core` adds the same with the dxgi feature. Linux/macOS → `None`.

**Unknown handling.** `None` and `0` stay distinct. `0` = confirmed no GPU. `None` = detection failed. Both fail eligibility identically but carry different user-facing copy and different telemetry meaning — collapsing them means you cannot tell "user has no GPU" from "our probe is broken on their hardware." Fail closed but **visibly**: "couldn't read your GPU — assuming CPU-only", with a manual override. llmfit ships exactly this (`--memory=32G`) for broken drivers, VMs and passthrough. LM Studio's failure mode is the warning: it sometimes fails to auto-detect and defaults to CPU without telling you.

**Cut, with reasons (do not resurrect):**
- **Calibration run at onboarding.** Onboarding is precisely when Defender is scanning a freshly-downloaded 34 MB ONNX and the Tauri binary; 200 chunks likely never leaves ORT warmup. A contaminated number, cached permanently. Also `service.py` `load_model_background()` means the model loads lazily — calibration would race the background loader or force an early download on a possibly-metered connection.
- **Three-tier label** (`constrained`/`baseline`/`headroom`). If a label lands in persisted JSON it becomes an API and something will branch on it. Derive per-subsystem readouts in the UI; persist the vector only.
- **`requires` block inside `models.lock.json`.** `sha256`/`revision` change rarely and are security-critical; requirements change often from tuning. Co-locating means every performance tweak edits the file whose entire job is "don't change without verification." Split them.

**Naming:** if a hardware component is built, call it `HardwareProfiler` / `MachineProfile`. Do **not** extend `CapabilityDetector` — `app/search/capability_detector.py` is an LLM *prompt*-capability probe (tests `<claim>` tag support, short-circuits on `model_class == "3b_local"`, caches per provider+model). No hardware inspection anywhere in `app/`. A second, older tiering surface at `llm_client.py:119-131` branches on model-name strings (`"3b" in model_lower`). Fragile, but leave it this cycle — two tiering systems disagreeing is worse than one crude one.

---

## §H — P2 (deferred, unchanged)

- **`clear_all()`** (`app/storage/db.py`, ~L1452–1491 in the brief's numbering): deletes `chunks`, `files`, `query_history`, `folder_profiles`; drops `unreal_project_facts` and `chunk_fts`, recreating FTS. **Do not run a model-change wipe through it** — no vectors-only path exists. Add `clear_vectors_only()` later. Note P0-2 removes the `unreal_project_facts` drop.
- **SourceViewer / shell.** `tauri-plugin-shell = "2"` (`Cargo.toml:25`) and `@tauri-apps/plugin-shell ^2.3.5` (`package.json:22`) are declared but referenced **only** in `frontend/src/__tests__/setup.ts:47`. Zero production usage — candidates for removal. No component named SourceViewer exists; confirm the rename before acting.
- **File watcher:** absent. No `watchdog` / `notify` anywhere. Pull-based, confirmed.
- **LanceDB:** `uv.lock:711-713` = 0.30.2; `pyproject` floor `>=0.10.0`. Hold until the bug workstream closes. Verify `prefilter=True` semantics before any bump.
- **`crystal.wgsl`:** the removed variant gate is **deliberate and self-documented** at L59–61 — the renderer sorts instances by `type_hash % CRYSTAL_VARIANTS` and issues one draw per contiguous run. The shader is correct. If navigation broke, the picking pass must replicate the identical sort or picking IDs desync from draw order. **Check the picking pipeline, not the shader.**

---

## §I — Open decisions

**1. §1.4 batch size — take `64` now, or hold `512` until token-budget batching lands?**
*Recommendation: take `64` now.* It is the only open decision touching the next 15 days, and the asymmetry is lopsided. Holding `512` keeps a ~262k-token worst-case ceiling on a 4 GB-target product through the entire demo and paper-benchmark window. Taking `64` risks a throughput regression that the same-day benchmark will measure immediately, with a one-line revert if it disappoints. The plan above assumes `64`; if you rule the other way, P0-4 reduces to removing `intra_op_num_threads` and the arena test, and the paper's numbers get measured at `512`.

**2. §0.2 VLM OCR tier — opt-in above a measured floor, or cap the ladder at GLM-OCR?**
Blocks F.3 design, nothing before Aug 17. No action needed this sprint; needs an answer before OCR design starts. Baseline stays PP-OCRv6 mobile ONNX for everyone either way — VLM is never a default and never auto-selected.

**3. §2.3 VRAM detection — implement DXGI when detection is built, or ship NVIDIA-only first?**
Post-deadline; safe to defer. Noting for the record that "NVIDIA-only first" means AMD users get `None` → CPU-only, which §G's unknown-handling rule already makes *visible* rather than silent — so the fallback is honest even if incomplete. That makes shipping NVIDIA-only defensible if DXGI proves slow to land.

---

## §J — Definition of done for Aug 17

- [ ] Local-first chain reaches Ollama when Ollama is running (P0-1)
- [ ] `app/api/modules.py` contains no Creative/Houdini/Unreal code; `grep -ri "houdini\|unreal" app/` is clean (P0-2)
- [ ] EPUB spine order, PPTX tables + notes, CSV budgeted cap, each with a fixture test (P0-3)
- [ ] `embedding_batch_size` bounded; `intra_op_num_threads` removed; four benchmark numbers recorded in `05_CHANGELOGS.md` (P0-4)
- [ ] `grep -rn 'settings.json' app/` matches only `settings_store.py` (P1-1)
- [ ] Corrupt blobs log and return `""` instead of indexing a Python repr (P1-2)
- [ ] `/api/llm/chat` prefix, auth, and credential-leak audit documented (P1-3)
- [ ] Paper 1 placeholders replaced with measured numbers; 4-page ACM two-column limit met
