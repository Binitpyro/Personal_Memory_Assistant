# 08 — Next Updates: Source-Verified Research

**Status:** research document only. No source files were edited. No git operations were run beyond read-only (`rev-parse`, `log`, `diff --stat`, `check-ignore`, `ls-files`).

**Baseline established this session:**

```
$ git rev-parse --short HEAD
764d41e
$ git branch --show-current
updates
$ git log --oneline -4
764d41e Added tiered ocr
3555f6c Add privacy gates and extractor guards
ca755a2 Add local provider launch endpoints and UI
dbbcfe7 Merge fix/trust-surface into updates (P1-1, P1-2, P1-3)
$ git diff --stat dbbcfe7..HEAD | tail -1
120 files changed, 12528 insertions(+), 368 deletions(-)
```

`CLAUDE.md §8` records last verification at `dbbcfe7`. HEAD is **three commits and ~12.5k lines ahead** of that. §0 below reconciles the difference.

**Evidence classes used throughout:**

| Class | Meaning |
|---|---|
| `SRC` | Read at `764d41e` this session. `path:line` cited. |
| `EMP` | Reproduced by running code this session. Verbatim output included. |
| `EXT` | External source (web/paper). Confidence stated per item. |
| `INF` | Inferred from `SRC`/`EMP` but not directly executed. Marked as such. |

**Environment caveat on `EMP` results:** reproductions ran on Linux, `sqlite3.sqlite_version == 3.37.2`. The Windows host may ship a different SQLite. The semantics exercised (FTS5 implicit AND, contentless-table column reads, trigram minimum token length) are stable across FTS5 versions, but if any `EMP` item drives a change, re-run it on the Windows interpreter first.

---

## §0 — Corrections to `CLAUDE.md`

These are retractions and status changes required by what is actually in the tree at `764d41e`. Per `CLAUDE.md §1.1`, prior claims that turn out wrong are retracted explicitly.

### 0.1 — Retract: "fixed-length padding" (`§7`, bullet 2)

`CLAUDE.md §7` lists as a *verified technical fact*: "**Fixed-length padding** (not dynamic batch-longest) makes peak memory provably bounded and stabilizes batch shapes for `enable_mem_pattern`."

**This is false at HEAD.** Both tokenizers use dynamic batch-longest padding:

- `app/embeddings/service.py:112` — `self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")` — no `length=` argument.
- `app/search/reranker.py:46` — same call, same omission.

The codebase says so itself. `app/config.py:79-86`:

```python
# P0-4: the tokenizer pads each batch to its own longest sequence
# (enable_padding has no length=), so peak memory scales with
# batch_size * longest_seq_in_batch, not item count. ...
# The real fix is token-budget batching (post-deadline); this just bounds the
# blast radius until then.
embedding_batch_size: int = 64
```

What actually shipped instead is three different mitigations: `embedding_batch_size` lowered to 64, `enable_cpu_mem_arena=False` (`service.py:182`), and length-sorted batching (`service.py:29-59`). Those are real and measured. They are not fixed-length padding, and the memory bound they give is empirical, not provable. `§7` should be rewritten to describe what is there.

### 0.2 — Retract: "confidence-based cross-encoder reranker bypass" (`§5`)

Deleted. `app/search/retrieval.py:641-647` documents the removal in-place and states the heuristic "was never sound." Every `FULL_RAG` query now pays full cross-encoder cost. See item **P-6**.

### 0.3 — Retract: "FULL_RAG — the only mode that expands to bounded parent windows (3–5× child chunk size, hard token ceiling)" (`§5`)

**No parent-window expansion exists anywhere in `app/`.** Verified by exhaustive grep for `parent_window`, `expand_parent`, `window_expand`, `_expand_to_parent` — zero matches. The only `parent` hits in `app/` are `Path().parent`, folder-profile parent/child dedup, and AST scope parents. See item **F-1**; this is the single largest capability gap between the documented architecture and the shipped one.

### 0.4 — Defect status changes (`§8.1`)

| # | Defect | Status at `764d41e` | Evidence |
|---|---|---|---|
| 1 | `db.clear_all()` scope too broad | **CLOSED** | `app/storage/db.py:1718` `clear_vectors_only()` exists, with an explicit scope note that it does not touch LanceDB |
| 2 | Gemini free-tier region gating | **STILL OPEN** | `grep -ni "region\|eea\|gdpr\|free.tier" app/providers/gemini.py` → no matches |
| 3 | NVIDIA NIM missing `base_url` | **CLOSED** | `app/config.py:174` `nvidia_nim_base_url: str = ""` |
| 4 | DOCX headers/footers/footnotes dropped | **CLOSED** | `app/indexing/extractors/docx_extractor.py:19-53,144` — walks all six header/footer variants plus footnote/endnote parts |
| 5 | Zip-bomb guard missing on DOCX/PPTX | **CLOSED** | `app/indexing/extractors/_ooxml_guard.py`, imported by both `docx_extractor.py:6` and `pptx_extractor.py:5`. Guard documents its own limitation honestly (central-directory declared sizes only) |
| 6 | EPUB inline `<script>`/`<style>` leak | **CLOSED** | `app/indexing/extractors/epub_extractor.py:20,184` — `_SCRIPT_STYLE_RE` with `DOTALL` |

**Only defect 2 remains open.** It is also the only one that is a positioning problem rather than a code problem.

### 0.5 — Phase 1 retrieval-hardening status (`§8.4`)

"BFS CTE using `UNION ALL` without dedup" — **FIXED**. `app/storage/db.py:1085` and `:1092` both use `UNION`, with the reasoning documented at `:1073-1076`. Note `get_relational_paths` still uses `UNION ALL` at `db.py:1139`; that one is intentional (path enumeration, not node traversal) but has no cycle guard beyond `depth < max_depth`. See **H-4**.

"Missing `ORDER BY` in reindex pagination" — `get_chunk_ids_for_paths` has `ORDER BY f.path, c.id` (`db.py:1051`). I did not locate the specific reindex pagination call site the plan refers to; **unverified, re-check before closing.**

### 0.6 — OCR gate G4 (`§8.3`)

G4 requires "`index_images` and `ocr_enabled` both to `0`". `ocr_enabled: bool = False` at `config.py:113` ✅. **`index_images` does not exist anywhere in `app/`** — `grep -rn "index_images" app` returns nothing. Either the setting was never implemented or the gate names a setting that does not exist. G4 cannot be signed off as written.

---

## §1 — Ranked backlog

Ordering is by (silent-wrongness × user-visible impact) ÷ effort. Items above the P0/P1 line change what answers the system returns; items below change how fast or how pleasantly it returns them.

**Effort:** S ≤ 1 day · M = 2–5 days · L > 1 week.

### Tier P0 — silent correctness failures

| # | Item | Impact | Effort | Risk | Evidence |
|---|---|---|---|---|---|
| R-1 | FTS leg returns **zero results** for any multi-word natural-language query | Critical | S | Low | `EMP` + `SRC retrieval.py:156-161,561` |
| R-2 | Tokens < 3 chars ("AI", "3D", "Go", "ML") are **unfindable** by keyword search | High | M | Med | `EMP` + `SRC schema.sql:57-62` |
| R-3 | Semantic query cache **ignores `file_type`/`folder_tag`** — returns answers scoped to the wrong corpus | High | S | Low | `SRC retrieval.py:767-787,952-957,1244-1247` vs `:1109` |
| R-4 | Context relevance cutoff compares against **stale RRF score** after the reranker reordered on `rerank_score` | High | S | Low | `SRC context_builder.py:381-386` |
| R-5 | Agentic sufficiency floor defaults to `0.0`; RRF scores are always > 0 → **every sub-question marked satisfied**, "not found" list never fires | High | S | Low | `SRC agentic.py:293` + `config.py:238` |
| R-6 | Reranker ONNX session has **no `SessionOptions`** — runs with the exact CPU-arena config measured at 3848 MB in the embedder | High | S | Low | `SRC reranker.py:48-49` vs `service.py:170-187` |
| R-7 | Reranker model loads from relative `Path("models")` with **no checksum verification** — asymmetric with the pinned, gated, fail-closed embedder | High | M | Low | `SRC reranker.py:29-49` vs `service.py:141-160` |
| R-8 | **No way to stop a running generation**; server does not detect client disconnect | High | S | Low | `SRC useChatStream.ts:240,377`, `api.ts:518-586`, `api/search.py` (no `is_disconnected`) |

### Tier P1 — performance and resource budget

| # | Item | Impact | Effort | Risk | Evidence |
|---|---|---|---|---|---|
| P-1 | `query_cache` and `pma_summaries` have **no vector index** and `query_cache` grows unbounded — brute-force scan on every query, degrading with use | High | S | Low | `SRC lancedb_client.py:421-515`, `indexing/service.py:363`, `ocr/manager.py:243` |
| P-2 | Two unbounded-by-design caches consume a large fraction of the stated 60 MB RAM budget | High | S | Low | `SRC context_builder.py:33`, `service.py:78` |
| P-3 | `_mean_pooling` upcasts to **float64**, doubling the largest transient allocation in the embed path | Med | S | Low | `SRC service.py:353` |
| P-4 | MinHash dedup runs **twice**, the second time shingling full chunk text — CPU hotspot on the query path | Med | S | Low | `SRC retrieval.py:443-453` + `context_builder.py:120-124` |
| P-5 | `asyncio.wait_for` over `run_in_executor` **does not cancel the worker thread**; the in-code comment claiming it does is wrong | Med | S | Low | `SRC retrieval.py:649-655` |
| P-6 | Reranker now runs unconditionally — bypass deleted with no replacement | Med | M | Med | `SRC retrieval.py:641-647` |

### Tier P2 — capability gaps

| # | Item | Impact | Effort | Risk | Evidence |
|---|---|---|---|---|---|
| F-1 | **Parent-window expansion does not exist** despite being documented as core architecture | Critical | M | Med | `SRC` exhaustive grep, zero matches |
| F-2 | Chunks are **512 *characters* (~128 tokens)** — the embedder's 512-token window is ~75% unused; `chunk_size` means chars in the prose path and tokens in the code path | High | M | Med | `SRC service.py:160-176,266`, `config.py:90` |
| F-3 | Context budget — `CLAUDE.md §6`'s "binding constraint" — is set by **substring matching on a model name**. `1b`, `4b`, `14b`, `32b` all fall through to `7b_local` | High | M | Low | `SRC llm_client.py:126-166`, `context_builder.py:296-303` |
| F-4 | **Contextual retrieval** (ingest-time situating strings) — highest-evidence external technique available; already on `§9` horizon list | High | L | Med | `EXT` (high confidence, published numbers) |
| F-5 | Query planner routes on brittle substrings; `"my index"`, `"the index"`, `"give me a summary"` hijack content queries into `FAST_METADATA` | Med | M | Med | `SRC planner.py:77,89-90,160-166` |
| F-6 | Binary quantization + float rescoring, and/or Matryoshka truncation, for the 4 GB budget | Med | M | Med | `EXT` (high confidence) + `SRC` (embeddings stored float32) |
| F-7 | PMA-as-MCP-server (Coder module) — highest-leverage *demand-validated* direction | Strategic | L | High | `CLAUDE.md §9` + `EXT` landscape |

### Tier P3 — UI/UX

| # | Item | Impact | Effort | Risk | Evidence |
|---|---|---|---|---|---|
| U-1 | Accessibility is effectively absent: **6 `aria-*` attributes across 13,457 lines**, two files total; no keyboard path into the Dreamscape canvas | High | L | Low | `SRC` grep counts |
| U-2 | Native `confirm()` / `alert()` used, while `sonner` is a declared dependency imported nowhere | Low | S | Low | `SRC SearchPage.tsx:85,92` + grep |
| U-3 | **Two complete renderer implementations** — 1,401-line hand-written WebGPU and 781-line three.js WebGL2 — with no shared scene description | Med | L | High | `SRC WebGL2Renderer.ts:26`, `package.json` |
| U-4 | No retrieval-transparency surface reaches users: the agentic trace exists end-to-end but `agentic_enabled` is `False` | Med | S | Low | `SRC config.py:235`, `useChatStream.ts` `SET_TRACE` |
| U-5 | File-tree polled every 15 s regardless of activity | Low | S | Low | `SRC SearchPage.tsx:33` |

### Tier H — hygiene

| # | Item | Evidence |
|---|---|---|
| H-1 | Contentless FTS5 `SELECT cf.chunks_text` always yields `NULL`; the value is bound into a dict field and never read | `EMP` + `SRC retrieval.py:186-193` |
| H-2 | `dist/sidecar/PMA/_internal/app/` holds a stale copy of `app/` in the worktree (gitignored) — poisons grep-based audits | `SRC` `wc -l` output |
| H-3 | ~57 MB of scan reports in the worktree root (`dependency-check-report.html` 41 MB, `.json` 16 MB) | `SRC ls -la` |
| H-4 | `get_relational_paths` is unidirectional while `bfs_from_chunks` is bidirectional; no cycle guard beyond depth | `SRC db.py:1101-1154` |

---

## §2 — Appendix: per-item detail

### R-1 — The FTS leg silently returns nothing for real queries

`_sanitize_fts_query` (`app/search/retrieval.py:156-161`) wraps **every** token in double quotes and joins with spaces:

```python
def _sanitize_fts_query(query: str) -> str:
    cleaned = FTS5_OPERATOR_RE.sub(" ", query)
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    if not tokens:
        return '"' + query.replace('"', "") + '"'
    return " ".join(f'"{t}"' for t in tokens)
```

In FTS5, adjacent quoted terms are an **implicit AND**. So a ten-word question requires all ten words to co-occur in a single 512-character chunk. Stop-words are not removed: `planner.py:250-253` has `_extract_keywords`, which strips them — but `retrieval.py:561` passes the **raw** `query` to `_fts_search`, never the extracted keywords.

Reproduced against a schema matching `app/storage/schema.sql:57-62`:

```
--- B) implicit AND across quoted tokens (simulating _sanitize_fts_query) ---
  query='turbulence'
    match="turbulence"
    -> [(3,), (1,)]
  query='how is curl noise turbulence applied to the velocity field'
    match="how" "is" "curl" "noise" "turbulence" "applied" "to" "the" "velocity" "field"
    -> []
  query='what does the solver do with turbulence'
    match="what" "does" "the" "solver" "do" "with" "turbulence"
    -> []
```

Document 1 in that fixture is *"Curl noise turbulence is applied to the velocity field before advection in the solver."* — a near-verbatim match. The keyword leg returns nothing for both natural-language phrasings.

**Consequence.** `rrf_fts_weight = 0.4` (`config.py:210`). For the entire class of queries a chat UI produces, 40% of the fusion weight contributes an empty list, and the system silently degrades from three-signal hybrid to two-signal. Nothing logs it. `_fts_search`'s `except` at `:194` only catches exceptions, and an empty result is not an exception.

**Fix direction (needs sign-off before code):**
1. Feed `plan.keywords` rather than the raw query — cheapest change, immediately restores multi-word recall.
2. Move from implicit-AND to `OR` across terms, or an `OR`-with-`AND`-boost two-pass. FTS5 supports `term1 OR term2`; BM25 then ranks by how many matched.
3. Add a regression test that asserts non-empty FTS results for a ≥8-word query whose terms all exist in the corpus. The absence of such a test is why this survived.

Option 1 and 3 are ~1 hour. Option 2 changes ranking behaviour and must bump `FUSION_VERSION` (`project_constants.py:47`).

### R-2 — Short tokens are unfindable

`schema.sql:60` sets `tokenize="trigram"`. A trigram tokenizer indexes 3-character windows; a query term shorter than 3 characters produces no trigram and therefore matches nothing:

```
--- C) short token (<3 chars) behaviour on trigram ---
  "of" -> []
  "a" -> []
  "in" -> []
```

For a *personal* knowledge tool this is a live usability bug, not a corner case: `AI`, `3D`, `ML`, `UI`, `UX`, `OS`, `Go`, `C`, `R`, `k8s`-adjacent short tokens, ticket IDs, and initials are all invisible to the keyword leg. The semantic leg partially covers this, but the semantic leg is exactly the one that is *bad* at rare literal tokens.

The trigram choice buys real things — substring/infix matching (valuable for code identifiers, `snake_case` fragments, and non-space-delimited scripts) — so this is a genuine tradeoff, not a mistake. But it was likely chosen without measuring the cost. Two directions:

- **Dual index.** Keep `chunk_fts` (trigram) for substring/code matching, add `chunk_fts_words` using `unicode61` + `porter` for prose BM25, fuse as a fourth RRF list or pick per-query. Costs index size and write throughput on ingest.
- **Measure first.** Build the same corpus under `trigram` vs `unicode61 remove_diacritics 2` + `porter`, compare index size on disk and nDCG@10 on `tests/eval/queries.json` (which already exists — 127 lines, added in the last three commits). This is the cheaper first step and it produces a number for Paper 1's results section, which `CLAUDE.md §9` records as still needing real quantitative data.

**Do the measurement before touching the schema.** A tokenizer change is a full reindex for every user.

### R-3 — Semantic cache ignores retrieval filters

Two caches, two different key policies:

- `_check_rag_response_cache` (`retrieval.py:752-764`) keys on `(query, file_type, folder_tag, hist_key, _index_generation)`. Correct.
- `_check_semantic_query_cache` (`retrieval.py:767-787`) keys on **nothing but the query embedding**. `lancedb_client.search_cache` (`:458-488`) takes no `where_filter` and applies none.

Call sites: `full_rag` at `:952-957` and `stream_rag` at `:1244-1247`. Both guard only on `if not history`.

So: ask a question with `folder_tag="work"`, then ask the same question with `folder_tag="personal"`, and the second call returns the first answer — built from documents the user explicitly scoped out. For a product whose pitch is privacy and control over a personal corpus, silently crossing a user-set boundary is worse than a wrong answer.

Also note the writer (`add_query_cache`, `:421-456`) stores only `query_text`, `response_text`, `timestamp`, `vector`. There is no filter column to key on even if `search_cache` wanted one.

**Fix:** add `file_type` / `folder_tag` columns at write time and a `where` clause at read time; or simply skip the semantic cache whenever either filter is set. The second is one line and strictly safe.

### R-4 — Relevance cutoff reads the wrong score

`build_context` (`context_builder.py:381-386`):

```python
top_score = deduplicated[0].get("score", 1.0)
if top_score > 0:
    score_threshold = top_score * score_multiplier
    deduplicated = [r for r in deduplicated if r.get("score", 1.0) >= score_threshold]
```

`score` is the **RRF** score, written at `retrieval.py:472`. The cross-encoder writes a *separate* key, `rerank_score` (`reranker.py:122`), and re-sorts on it (`reranker.py:124`). `score` is never updated.

So by the time `build_context` runs, list *order* is by `rerank_score` while the *cutoff* is computed from `deduplicated[0]`'s RRF score. Two failure modes:

- Top-reranked chunk happened to have a **low** RRF rank → threshold is low → the filter does nothing, and 3B-class context (`max_chunks = 3`, `score_multiplier = 0.4`) fills with weak chunks.
- Top-reranked chunk had a **high** RRF rank → threshold is high → strongly-reranked chunks below it get dropped for having low RRF scores, which is exactly what the reranker was hired to override.

The same class of bug appears in `_deduplicate_by_file` (`context_builder.py:184`), whose docstring says "the list is assumed to be pre-sorted by score" — it is pre-sorted by `rerank_score`.

**Fix:** filter on `rerank_score` when present, falling back to `score`. One conditional. Both thresholds need re-tuning afterwards, since the two scores are on completely different scales (RRF × 1000 ≈ single digits; cross-encoder logits ≈ −10…+10).

### R-5 — The "not found" capability is disabled by its own default

`agentic.py` opens with a strong claim (`:24-26`):

> An explicit not-found list. Sub-questions that finish unsatisfied are reported as such. A system that says "nothing in your research notes on this" is doing something a chatbot with search cannot.

`sufficiency_node` (`:284-300`):

```python
floor = settings.agentic_evidence_score_floor
...
if any(h.chunk.get("score", 0.0) > floor for h in hits):
    sq.status = "satisfied"
```

`agentic_evidence_score_floor: float = 0.0` (`config.py:238`). RRF scores are strictly positive — sums of `weight / (rrf_k + rank + 1)`, scaled by `rrf_score_scale = 1000` (`config.py:221`). **Any hit at all clears the floor.** A sub-question is only ever "unanswered" when retrieval returns literally nothing.

Two knock-on effects:
- The differentiating feature never fires in practice.
- `run_agentic_loop` (`:323-356`) breaks with `all_satisfied` after iteration 1, so `agentic_max_iterations: int = 2` is dead — the "bounded loop" is a single fan-out.

And as in **R-4**, sufficiency judges on RRF `score`, not `rerank_score` — the weaker signal.

This matters beyond the feature: `CLAUDE.md §9` blocks Paper 4 on Paper 1 results, and Paper 1 needs quantitative data. Sufficiency detection is named in `§6` as one of the three capability gaps LangGraph was rejected for not solving. It is currently unsolved *in the code that claims to solve it*.

**Fix:** the floor must be expressed on the cross-encoder scale, not RRF. `reranker.py`'s own comment at `retrieval.py:745-746` already asserts "`cross-encoder/ms-marco-MiniLM-L-6-v2` logits < -2.0 means very poor match" — that constant should be the floor's basis. Calibrate on `tests/eval/queries.json` before setting a number.

### R-6 — The reranker session is unbounded

`app/embeddings/service.py:170-187` carries the measurement that justified its own configuration:

```python
# P0-4: measured on a variable-length synthetic corpus (mixed
# 15-400 word texts, batch_size=64) that deliberately stresses
# BatchLongest padding - the arena never shrinks, so wide shape
# variance compounds its growth: enable_cpu_mem_arena=True peaked
# at 3848 MB vs 172 MB with it off (22x), for a 9% throughput cost
```

`app/search/reranker.py:48-49`:

```python
providers = ["CPUExecutionProvider"]
_session = ort.InferenceSession(str(onnx_file), providers=providers)
```

No `SessionOptions`. So the reranker runs with `enable_cpu_mem_arena` at ORT's default (`True`) — **the exact configuration measured at 3848 MB** — while padding to batch-longest (`reranker.py:46`, no `length=`).

The reranker's shape variance is *worse* than the embedder's, not better: it encodes `[query, chunk]` pairs (`reranker.py:95`) where chunk lengths vary across the whole candidate pool, batch size is `min(len(results), top_k * 4)` (`:86`) — up to 100 at `retrieval_top_k = 15` — and it runs on the interactive query path where a memory spike is user-visible.

This is a copy-paste of `service.py:170-187` into `reranker.py`. Low risk, immediate. It is the highest value-per-line change in this document.

### R-7 — Reranker model loading is unpinned and unverified

`reranker.py:29-49` versus `service.py:141-160`:

| | Embedder | Reranker |
|---|---|---|
| Path resolution | `models.lock.json` → `snapshot_download(repo_id, revision)` | `Path("models") / "cross-encoder_ms-marco-MiniLM-L-6-v2"` — **relative to CWD** |
| Integrity | SHA-256 via `verify_file_sha256`, **fails closed** (`ValueError`) | none |
| Unpinned files | rejected unless `embedding_allow_unpinned` | n/a — no pinning exists |
| Download gate | `embedding_allow_download` | n/a |

Two distinct problems:

1. **Supply chain.** `CLAUDE.md §8.2` records "ONNX checksum verification — now real `hmac.compare_digest`, fails closed" as a closed defect. It is closed for the embedder only. The reranker will load any `model.onnx` placed at that path. For a product positioned on privacy and local trust, one of two ONNX models on the inference path having no integrity check is a defect, not a gap.
2. **Portability.** `Path("models")` is CWD-relative. `PMA.spec` builds a PyInstaller sidecar; a packaged app's CWD is not reliably the install root. If it resolves wrong, `_load_onnx_model` raises `FileNotFoundError` (`:42`), `preload_reranker` swallows it (`:66`), and `_apply_reranker_if_needed`'s `try` catches only `TimeoutError` (`:656`) — so the `_get_model_assets` failure propagates out of `rerank`, gets caught by `rerank`'s own `except Exception` at `:116`, and returns `results[:top_k]` in RRF order. **Reranking silently disappears** with one `logger.error` and no `_degraded` flag. Users on a packaged build could be getting un-reranked results and no signal says so.

**Fix:** add the reranker to `models.lock.json`, route it through `app/utils/model_integrity.py` (`verify_file_sha256` at `:80`, `hmac.compare_digest` at `:94`), resolve paths from `_BASE_DIR` the way `main.py:44` does, and set `_degraded` on the reranker-unavailable path so the UI can show it.

### R-8 — No stop button, and the server keeps generating

The transport already supports cancellation. `api.ts:518-586` — `subscribeQuery` creates an `AbortController`, passes `controller.signal` to `fetch`, and **returns** `() => controller.abort()`.

`useChatStream.ts:240` calls `subscribeQuery(...)` and **discards the return value**. The hook's public surface is `return { messages, executeSearch, resetChat };` (`:377`) — no `stop`. `SearchPage.tsx:227-233` renders a spinner in place of the send button while `isSearching`, with no cancel affordance.

Server side: `grep -n "is_disconnected" app/api/search.py` → no matches. So even if the client aborted, `stream_rag` keeps pulling tokens.

Impact scales with exactly the hardware PMA targets. A 7B model at 4 GB VRAM producing ~3 tok/s on a 600-token answer is ~3 minutes with no exit. On the cloud providers in the 9-provider abstraction, an abandoned generation keeps billing.

**Fix (≈1 hour):** store the unsubscribe in a ref, expose `stopStream`, render a stop button; add `if await request.is_disconnected(): break` to the `stream_results` generator in `api/search.py:145`.

---

### P-1 — The semantic cache gets slower the more the product is used

`create_hnsw_index` is called exactly twice, both times with `"pma_chunks"`:

```
app/indexing/service.py:363:  await self.lancedb_client.create_hnsw_index("pma_chunks")
app/ocr/manager.py:243:       await self.lancedb_client.create_hnsw_index("pma_chunks")
```

`pma_summaries` and `query_cache` therefore have **no vector index** and are searched by exhaustive scan.

- `pma_summaries` is scanned once per query (`retrieval.py:577-578`). Cost is O(number of indexed files).
- `query_cache` is scanned once per query (`retrieval.py:770` and `:1247`), *before* retrieval, on the fast path everything else waits behind.

And `query_cache` is **append-only with no eviction**: `add_query_cache` (`lancedb_client.py:421-456`) writes one row per successful non-history RAG answer, forever. Nothing prunes it. `clear_all` (`:517-534`) drops the whole table, which is the only removal path.

So the semantic cache — a latency optimization — becomes a growing linear scan on the critical path. It is negative-value past some corpus-and-usage point, and nothing measures where that point is.

There is also a privacy dimension worth deciding deliberately: `query_cache` persists full question and answer text with no TTL and no per-entry delete. `SearchPage.tsx:84-94` offers "CLEAR HISTORY", which calls `clearQueryHistory()` → SQLite `query_history` only. **The LanceDB semantic cache survives it.** A user who clears their history reasonably believes their questions are gone; they are not.

**Fix:** (a) index `pma_summaries` and `query_cache` at the same points `pma_chunks` is indexed; (b) bound `query_cache` by row count or age with LRU eviction on `timestamp`; (c) make "clear history" clear both stores, or say plainly in the UI that it does not.

### P-2 — Two caches against a 60 MB budget

`CLAUDE.md §6` sets a **60 MB RAM budget** as a first-class, non-negotiable constraint. Two caches are sized without reference to it.

`context_builder.py:33-39`:

```python
@functools.lru_cache(maxsize=1024)
def _get_tokens(text: str) -> list[int]:
```

Keyed on full chunk text, valued with a Python `list[int]`. Each entry retains both. A 512-char chunk plus ~130 token ids as boxed `int` objects (28 B each above CPython's small-int cache, plus 8 B list slots) is on the order of 5–6 KB; 1024 entries ≈ **5–6 MB**, and it is never cleared for the process lifetime.

`service.py:76-78`:

```python
self._query_cache: OrderedDict[str, list[float]] = OrderedDict()
self._max_cache_size = 2000
```

Values are `list[float]` of dimension 384 (`service.py:72`). A Python `float` is 24 B plus an 8 B list slot → ~12.3 KB per entry → 2000 entries ≈ **24 MB**. That is ~40% of the entire stated budget for a query-embedding cache.

`INF`: these are structural size estimates from CPython object sizes, not measured. **Measure with `tracemalloc` before acting** — but the direction is clear and the fix is cheap: store query embeddings as a single `np.ndarray` (384 × 4 B = 1.5 KB, a 8× reduction) and drop `_max_cache_size` to something proportional to the budget. `_get_tokens` should cache the *count*, not the token list, since `_token_count` (`:42-47`) is the only consumer that needs it and `_truncate_to_tokens` (`:59-68`) re-encodes anyway.

### P-3 — float64 upcast in the embed path

`service.py:351-356`:

```python
input_mask_expanded = np.expand_dims(attention_mask, -1).astype(float)
return np.sum(token_embeddings * input_mask_expanded, 1) / np.maximum(...)
```

`np.float` is `float64`. `token_embeddings` is `float32`; the multiply promotes to `float64`, allocating a transient of `batch × seq_len × 384 × 8 B`. At `embedding_batch_size = 64` and a 512-token batch that is ~100 MB, versus ~50 MB in `float32`. It is then immediately downcast on assignment into the `float32` `out_array` (`:398,425`), so the precision is discarded anyway.

`.astype(np.float32)` — one word. Halves the largest transient in the batch loop, on a product whose entire memory story is bounded peaks. This deserves a before/after `tracemalloc` number; that number is also usable in Paper 1.

### P-4 — MinHash runs twice per query

Two independent dedup passes on the same result list:

- `retrieval.py:443-453`: MinHash over 3-shingles of a **200-character middle slice** (`:439-440`), `num_perm=128`, threshold `0.85`.
- `context_builder.py:120-124`: MinHash over 3-shingles of the **full chunk text**, `num_perm=128`, threshold `0.85`.

The second is the expensive one. `{text[j:j+3] for j in range(len(text)-2)}` on a 512-char chunk yields ~500 shingles, each fed to `m.update()`, each computing 128 hashes → ~64k hash operations per chunk. Over the up-to-100-chunk cap (`context_builder.py:111`) that is ~6.4 M hash ops, on the interactive query path, on CPU, on 4 GB-class hardware.

The first pass has a correctness wrinkle too: a signature taken from the *middle* 200 characters means two chunks with similar middles and different heads and tails are treated as duplicates and one is dropped, before the reranker ever sees it (`:464-465`). With `chunk_overlap = 50` on `chunk_size = 512`, adjacent chunks share ~10% — the case this is defending against — but the middle-slice signature is a poor discriminator for it.

**Fix direction:** one dedup pass, positioned after reranking (so the reranker's own judgement is preserved), operating on offsets rather than text where possible — `start_offset` / `end_offset` / `file_id` are already carried through `retrieval.py:473-487` and make overlap detection exact and free. Content-based MinHash is only needed for near-duplicate chunks in *different* files.

### P-5 — A false claim in a code comment

`retrieval.py:648-655`:

```python
try:
    # rerank() already offloads CPU inference via loop.run_in_executor, so
    # asyncio.wait_for can cancel it correctly - no to_thread needed.
    results = await asyncio.wait_for(rerank(...), timeout=5.0)
except TimeoutError:
```

`asyncio.wait_for` cancels the awaiting *coroutine*. A `concurrent.futures.Future` already running in a `ThreadPoolExecutor` **cannot be cancelled** — `Future.cancel()` returns `False` once the worker has started. The ONNX `session.run` call at `reranker.py:109` runs to completion regardless.

Practical effect: on timeout, the request returns in RRF order (correct behaviour), but the default executor thread stays occupied until inference finishes. Under concurrent queries — which the agentic fan-out (`agentic.py:247-250`, `asyncio.gather` over sub-queries) deliberately creates — threads are consumed faster than they are released, and `run_in_executor(None, ...)` shares the single default executor with `embed_texts` (`service.py:429`) and every LanceDB call (`lancedb_client.py:255,333,359,379,...`). Head-of-line blocking across the whole I/O layer.

`to_thread` would not help either; the same limitation applies. The real options are a bounded, dedicated executor for ONNX work, or an in-loop deadline check inside `_run_rerank` between batches. **At minimum, correct the comment** — it currently tells the next reader that a guarantee exists which does not.

### P-6 — Reranker now runs on every query

The bypass removal (`retrieval.py:641-647`) is well-argued: it compared a summary-boosted score against an unboosted one and assumed a single retrieval pass. Both objections are correct.

But the replacement is "always run it," and the cost is real: cross-encoder inference over up to `min(len(results), top_k * 4)` pairs (`reranker.py:86`) — up to 60 at `retrieval_top_k = 15` — on CPU, with the unbounded arena from **R-6**, with a 5-second ceiling (`retrieval.py:654`) and a 500 ms soft budget that is only logged, never enforced (`reranker.py:74,127-134`).

A sound bypass is still possible; it just has to key on something stable. Candidates, in increasing order of work:

1. **Plan mode.** `FAST_METADATA` and `FAST_PROJECT` already skip retrieval entirely. `GRAPH_SEARCH` seeds with `k=3` (`retrieval.py:810-820`) — reranking 3 candidates is close to pointless.
2. **Candidate-count floor.** If the fused pool is ≤ `k`, the reranker cannot change the answer set, only its order. Cheap and provably safe.
3. **Margin on the fused score after balancing**, recomputed per pass rather than assumed once. Sound, but must be calibrated against `tests/eval/`, and it re-opens what was just closed — do not attempt it without the eval harness producing numbers first.

Ship 1 and 2. Treat 3 as a research question, not a fix.

---

### F-1 — Parent-window expansion is documented but absent

`CLAUDE.md §5` describes `FULL_RAG` as "the **only** mode that expands to bounded parent windows (3–5× child chunk size, hard token ceiling)." Grep finds no such code. The retrieved chunk text is what `_build_candidate_results` read from `chunks.text_preview` (`retrieval.py:596`), unexpanded, straight into `build_context`.

This is the small-to-big / parent-document retrieval pattern, and it is the single most direct answer to `CLAUDE.md §6`'s framing: *"Context budget is the binding constraint. Feeding large full documents to a local LLM devours the token budget."* Parent-window expansion is precisely the technique that resolves that tension — retrieve on small chunks (precise embeddings), generate on a bounded neighbourhood (coherent context) — without "just pass more context."

Everything needed is already stored. `chunks` carries `file_id`, `start_offset`, `end_offset` (`schema.sql:30-31`), and `retrieval.py:473-487` already carries all three into results. Expansion is: for each surviving chunk, fetch sibling chunks of the same `file_id` whose offsets fall within `±N` of the hit, stitch by offset, cap total tokens.

**Blocker, and it is real.** With `chunk_size = 512` **characters**, a "3–5× parent window" is 1,536–2,560 characters ≈ 400–650 tokens. Against `3b_local`'s 4,000-token ceiling (`context_builder.py:300`) minus fixed costs, that is roughly one and a half parent windows. **F-1 and F-2 must be sized together** — settle chunk units first, then window multiplier. Doing F-1 alone at the current chunk size produces windows too small to matter; doing it after F-2 without re-tuning could blow the 3B budget on a single document.

### F-2 — Chunk size means characters here and tokens there

`StreamChunker` (`service.py:160-248`) is character-based throughout: `while len(self.buffer) > self.chunk_size` (`:176`), `raw_end = self.chunk_size` (`:186`), `overlap_start = max(0, end - self.chunk_overlap)` (`:205`). With `chunk_size: int = 512` (`config.py:90`) that is **512 characters ≈ 100–130 English tokens**.

Meanwhile `service.py:266` constructs `CodeChunker(max_tokens=512)`. Same conceptual setting, different unit, 4× different in practice. `config.py:90` documents neither.

Three consequences:

1. **The embedder is 75% idle.** `bge-small-en-v1.5` is truncated at 512 tokens (`service.py:111`) and fed ~130. A dense retriever's discrimination comes from having enough text to situate a passage; ~130 tokens is barely more than a long sentence.
2. **Chunk count is ~4× higher than necessary.** That multiplies LanceDB rows, FTS rows, embedding compute at ingest, and the `chunks` table — the dominant on-disk cost.
3. **Every chunk carries a repeated prefix.** `_build_context_prefix` (`service.py:1303-1305`) returns `"[MD: notes.md] "` and it is prepended to every chunk (`:191`, `:224`). At ~20 characters against 512, that is ~4% of every embedding spent on a constant string shared by all chunks of a file — it pulls same-file chunk vectors toward each other, reducing intra-document discrimination, which is exactly what the summary-routing leg (`rrf_summary_weight`) is supposed to handle at the document level instead.

`_find_boundary` (`:239-248`) also only searches the last 100 characters for a sentence delimiter and hard-cuts mid-word otherwise — at 512 characters that fires often.

**Before changing anything:** this is a full reindex for every existing user, and it invalidates any stored eval baseline. Sequence it as: (1) rename/derive the setting so units are explicit; (2) run `tests/eval/harness.py` at 512 / 1024 / 2048 characters; (3) pick from the numbers; (4) then do F-1 on top.

### F-3 — The binding constraint is set by string matching

`llm_client.get_model_class` (`:126-166`) classifies by substring:

```python
if "3b" in model_lower or "2b" in model_lower or "mini" in model_lower:
    return "3b_local"
if "7b" in model_lower or "8b" in model_lower:
    return "7b_local"
return "7b_local"
```

That class is the *only* input to `compute_context_budget` (`context_builder.py:296-303`), which sets `EFFECTIVE_CEILINGS = {"cloud": 100_000, "7b_local": 10_000, "3b_local": 4_000}` — and it also drives `max_per_file`, `score_multiplier`, and `max_chunks` in `build_context` (`:344-353`), and disables folder profiles and graph paths entirely for `3b_local` (`:345-346`).

Misclassifications, all falling through to `7b_local` and a 10,000-token budget:

| Model | Actual | Classified |
|---|---|---|
| `llama3.2:1b` | 1B | `7b_local` |
| `qwen2.5:0.5b` | 0.5B | `7b_local` |
| `gemma3:4b` | 4B | `7b_local` |
| `qwen2.5:14b` | 14B | `7b_local` |
| `qwen2.5:32b` | 32B | `7b_local` |

The 1B and 0.5B cases hand a 10k budget to a model that cannot use it — the failure mode `CLAUDE.md §6` explicitly rejects. The 14B/32B cases under-use available capacity. And parameter count is a poor proxy for context window regardless: `llama3.1:8b` has 128k context and is classified into a 10k ceiling.

**What to do.** Query the provider for the actual context length rather than guessing from a name.

`EXT`, **confidence: low, must be verified before building.** Community reports indicate Ollama's `/api/tags` response carries `context_length` inside `details`, and that `/api/show` historically did not expose it. I have not confirmed either against a running Ollama or against `ollama/docs/api.md` at a specific commit. **Do not write code against this until it is checked directly against the installed Ollama version.** LM Studio's OpenAI-compatible `/v1/models` surface needs the same check.

Fallback if the API does not expose it: a small explicit table in `config.py` keyed on model name, user-overridable in Settings, with the substring heuristic as last resort. That is honest about being a heuristic, which the current code is not.

### F-4 — Contextual retrieval

`EXT`, **confidence: high.** Anthropic's published contextual-retrieval results, widely reproduced: prepending a 50–100 token LLM-generated situating string to each chunk before indexing reduced top-20 retrieval failure rate by 35% (5.7% → 3.7%) for contextual embeddings alone; 49% (→ 2.9%) combined with contextual BM25; 67% (→ 1.9%) with reranking added.

PMA is unusually well-positioned to adopt it, and unusually well-positioned to *have a problem with it*:

**Positioned for it.** The hybrid + rerank stack the numbers were measured on is already built. The prefix mechanism already exists (`service.py:1303-1305`) — it just currently carries a filename instead of a situating sentence. And `app/indexing/summarizer.py` already computes a per-file structural summary (`generate_deep_summary`, `max_chars=300`) that could seed the context without any LLM call at all.

**The problem.** Anthropic's method calls an LLM once per chunk at ingest time. On the target hardware, indexing a 10,000-chunk corpus means 10,000 local-LLM round trips. At 3B-class speeds that is hours to days. This is the same class of objection that got VLM-OCR and AirLLM rejected on hardware grounds (`CLAUDE.md §6`).

**Three tiers worth evaluating in this order:**

1. **Free.** Prepend the existing `generate_deep_summary` output — already computed, no new compute. Not what Anthropic measured, but it is the same *shape* of signal, and it is nearly free.
2. **Cheap.** Prepend structural position — nearest preceding markdown heading, section path, enclosing function/class for code. `graph_extractor.py` already builds AST scope chains (`:71-87`) for Python. Deterministic, no LLM, respects the privacy-first constraint absolutely.
3. **Expensive, opt-in.** True LLM-generated context, run as background work through the existing durable-queue pattern the OCR pipeline already established (`app/ocr/queue.py`), off by default, with an honest time estimate in the UI.

Tier 1 is days of work and testable against `tests/eval/`. **Do tier 1, measure, and only then decide whether tier 3 earns its cost.** If tier 1 recovers most of the gain, that is a genuinely publishable finding for Paper 1 — "structural context recovers most of generative contextual retrieval's benefit at zero inference cost" is a result that speaks directly to the local-first thesis.

### F-5 — Planner routing is substring-fragile

`_INVENTORY_PHRASES` (`planner.py:40-92`) contains, among 50+ entries:

```python
"give me a summary",
"show me a summary",
...
"my index",
"the index",
```

`plan()` (`:138,160-166`) routes to `FAST_METADATA` if **any** phrase appears as a substring. `FAST_METADATA` returns a canned file-count string (`retrieval.py:143-146`) and never touches document content.

Consequences:

- *"give me a summary of my thesis notes"* → `FAST_METADATA` → "You currently have 4,102 indexed files taking up a total of 812 MB."
- *"what does the index say about turbulence"* → contains `"the index"` → same.
- *"how much space does the renderer use in the frame budget"* → `has_composite_inventory` fires on `"how much"` + `"space"` (`:140-155`) → same.

Compounding it, `determine_query_intent` (`project_constants.py:104`) sets `"project": "project" in q or "overview" in q or "summary" in q` — so the word "summary" anywhere flags project intent, which then feeds `include_profiles_text` (`retrieval.py:990`) and consumes context budget on folder profiles for queries that have nothing to do with projects.

The fast paths are the right idea — they are the reason simple questions are instant. The routing predicate is the problem: it is unigram substring matching against an unbounded hand-maintained phrase list, with no negative evidence and no confidence.

**Options, cheapest first:**
1. **Remove the over-broad entries** (`"my index"`, `"the index"`, `"give me a summary"`, `"show me a summary"`) and require an inventory *verb* plus an inventory *noun* rather than either alone. Hours of work, removes the worst cases.
2. **Add a bail-out:** if the query also contains content-bearing terms that appear in the corpus (one cheap FTS probe), do not take the fast path.
3. **Learn the router** from `query_history` + user corrections. This is a research direction, not a fix, and it needs a "this was the wrong answer" signal that does not exist in the UI yet — see **U-4**.

Do 1 now. 2 is a good second step. 3 only if it has a paper attached.

### F-6 — Quantization for the memory budget

`EXT`, **confidence: high.** Binary quantization of normalized embeddings (threshold at 0) gives 32× memory and storage reduction; a rescoring pass — comparing the float32 *query* vector against binary *document* vectors by dot product over a candidate set — recovers roughly 96% of retrieval performance while retaining the 32× space reduction and up to 32× speedup. Matryoshka representation learning is complementary: it makes embedding prefixes usable at reduced dimensionality, so the two compose.

Fit against PMA's constraints:

- Embeddings are already L2-normalized (`service.py:358-360`), which is the precondition for threshold-at-zero binarization.
- LanceDB stores `float32` fixed-size lists (`lancedb_client.py:241-243`). At 384 dims that is 1,536 B per chunk; binary is 48 B. On a 500k-chunk corpus: ~768 MB → ~24 MB.
- The rescoring pass needs the float vectors somewhere. `chunk_embeddings` in SQLite (`schema.sql:49-53`) already holds them, gated behind `sqlite_embedding_backup` (`config.py:44`). That is the rescore source, and it is already built.
- `bge-small-en-v1.5` is **not** a Matryoshka-trained model. Truncating its dimensions is not free the way it is for MRL models. Binary quantization on the current model is the tractable half; Matryoshka would require a model change.

**Sequencing note:** this interacts with **F-2**. Changing chunk size is a reindex; changing vector encoding is a reindex. If both are going to happen, they should happen in one migration, not two.

### F-7 — PMA-as-MCP-server

`CLAUDE.md §9` already ranks this above being an MCP client, and `§3` names the single most likely failure mode as *"zero demand validation before significant build investment."*

This is the one item in this document where that framing points *toward* building rather than away, and the reason is specific: **it is the only item whose demand can be validated before the build.** Every other item requires shipping the feature to learn whether anyone wanted it. An MCP server has an existing, observable, addressable population — people already running Claude Code, Cowork, and other MCP clients who already complain about context limits — and the integration surface is small enough that a thin prototype (expose `hybrid_retrieve` and `retrieve_only` over MCP, nothing else) is days rather than weeks.

`retrieve_only` (`retrieval.py:1133-1185`) already exists and already returns chunks without an LLM call. That is essentially the MCP tool surface already written.

**The counter-argument, stated plainly.** `CLAUDE.md §5` records Zeni's central finding: it contacts Core at two endpoints and never queries the personal corpus, making it "a scene-only RAG chatbot, not the corpus-joined system the vision describes." An MCP server risks the same shape — a retrieval endpoint that other tools call, with PMA's own differentiating work (the planner, the fusion balancing, the sufficiency loop) bypassed because the calling agent does its own orchestration. If the MCP surface is just "vector search over my files," PMA is competing with a hundred other MCP servers on convenience, not on the thing it is actually better at.

The test to run before building: does the MCP surface expose *retrieval quality* (planner, fusion, balance, not-found reporting) or just *retrieval access*? If only the latter, this is commodity work and the moat argument does not apply.

---

### U-1 — Accessibility

Measured across `frontend/src`, excluding tests:

```
=== a11y signals ===
./pages/SearchPage.tsx
./pages/ExplorerPage.tsx
--- counts ---
./pages/SearchPage.tsx:4
./pages/ExplorerPage.tsx:2
```

**Six `aria-*` attributes in two files, out of 13,457 lines of frontend source.** Keyboard handling is three call sites total (`ModelPicker.tsx:148`, `SearchPage.tsx:221`, `ExplorerPage.tsx:55`).

The structural problem is worse than the count. PMA's primary navigation surface is a WebGPU canvas (`WebGPURenderer.ts`, 1,401 lines) driven by `NavigationController.ts` (446 lines). A canvas exposes nothing to assistive technology by default, and there is no parallel DOM tree or keyboard path into it. For a user who cannot use a mouse, the Dreamscape is not degraded — it is absent.

`ExplorerPage.tsx` is a conventional tree and is the natural place for an accessible equivalent. It already has `tabIndex={0}` and Enter/Space handling at `:52-55`. Making it a complete, keyboard-navigable, ARIA-tree-role peer of the Dreamscape — rather than a secondary view — is the tractable version of this work.

Two reasons to care beyond the obvious one. First, enterprise and public-sector procurement asks for a VPAT, and "we have a WebGPU canvas" is not an answer. Second — `INF`, **verify with counsel before relying on it** — EU accessibility obligations for consumer software have been tightening, and PMA is sold into the EU via Gumroad. I have not verified applicability, thresholds, or micro-enterprise exemptions. Treat this as a question to ask, not a finding.

### U-2 — Native dialogs, and an unused toast dependency

`SearchPage.tsx:85` uses `confirm()`, `:92` uses `alert()`. In a Tauri v2 webview these render as platform dialogs that do not match the app's visual language, and `confirm()` blocks the event loop.

`sonner` is declared in `frontend/package.json` dependencies. `grep -rn "from 'sonner'" frontend/src` returns **no matches** — it is installed and never imported. So the toast library that would replace these dialogs is already paid for and unused. Either wire it up or drop the dependency; shipping both an unused toast library and blocking native dialogs is the worst of both.

### U-3 — Two renderers

`WebGPURenderer.ts` (1,401 lines, 12 WGSL shaders, moment-based OIT) and `WebGL2Renderer.ts` (781 lines) are separate implementations of the same scene. `WebGL2Renderer.ts:26` is the *only* `import * as THREE from 'three'` in the tree — so three.js (plus `@types/three`) exists solely for the fallback path.

Two consequences, and the second is the one that bites:

1. Two renderers means every visual change is implemented twice or the fallback diverges. There is no shared scene-graph abstraction between them.
2. **The fallback is the path users on the target hardware actually take.** `CLAUDE.md §6` names GTX 1650 / RX 580 as the hardware target. WebGPU availability on those, through Tauri's webview, on Windows, is exactly the case most likely to fall back. So the less-maintained renderer is probably the one the core user segment sees.

**Do not refactor this.** It is high-risk, high-effort, and speculative. **Do measure it:** instrument which renderer initializes (`WebGPUFallback.tsx` already detects capability, 552 lines) and report it in the existing local telemetry (`pma_metrics`, `schema.sql:116-133`). If the fallback rate is high, that reorders the whole frontend roadmap. If it is near zero, three.js and 781 lines can be deleted. Either answer is worth having and the measurement is a day.

### U-4 — No trust surface

`useChatStream.ts` has a `SET_TRACE` action and `Message.trace?: TraceEvent[]`; `agentic.py:381-383` serializes the trace; `retrieval.py:1102-1103` attaches it. The whole pipe is built. It is dark because `agentic_enabled: bool = False` (`config.py:235`).

Meanwhile the things a user most needs to see — *which of the three signals found this chunk*, *was the reranker skipped or degraded*, *was this answer served from cache*, *which sub-questions found nothing* — are computed and then discarded:

- `_degraded` is set (`retrieval.py:659`), popped (`:1082`), and surfaces only as a `mode` string.
- `cache_hit` is set on the non-streaming path (`:762`) but the streaming path (`:1251-1258`) yields a cached answer with `sources: []` and **no cache indicator at all** — indistinguishable from a fresh answer with no sources.
- Per-leg provenance (FTS vs semantic vs summary) is known inside `_compute_rrf_scores` and thrown away at `:255`.

This is not cosmetic. `CLAUDE.md §3` frames the core goal as making small local models punch above their weight. The honest version of that promise is that the user can *see when it did not work*. A cached answer silently presented as fresh is the opposite.

Cheapest meaningful version: carry a `signals: ["fts", "semantic"]` list per result through `_compute_rrf_scores`, and always emit a cache/degraded badge on the stream. Small change, and it gives the eval harness something to assert on.

### U-5 — Background polling

`SearchPage.tsx:33`: `refetchInterval: isSearching ? 0 : 15_000` for the file tree. Suspended during search, which is the important part, but otherwise a fixed 15-second poll for data that changes only on indexing. `subscribeProgress` (`api.ts:442-476`) already provides an SSE channel for index events — invalidate on those instead.

---

## §3 — Explicitly not recommended

Stated so they are not re-proposed:

- **Do not swap the embedding model yet.** `EXT` says EmbeddingGemma-300M and nomic-embed-text outperform `bge-small-en-v1.5` on MTEB, and EmbeddingGemma reportedly runs under 200 MB quantized. But: `bge-small` is pinned with SHA-256 in `models.lock.json`, the pin is load-bearing for the integrity gate, a swap is a full reindex, and **R-1 through R-4 mean current retrieval quality is not model-limited.** Fixing a model that is being fed empty FTS results and a broken relevance cutoff measures nothing. Revisit after P0 lands and the eval harness produces a baseline.
- **Do not lift the LanceDB pin** to get vector indexes on `pma_summaries` / `query_cache` (**P-1**). `create_index` is available at the current pin; the fix is call sites, not a version bump. `CLAUDE.md §4`'s `prefilter=True` re-verification requirement stands.
- **Do not adopt a retrieval framework** to solve F-1/F-5. `CLAUDE.md §6` rejected LangGraph on dependency grounds and the reasoning holds. Parent-window expansion is a SQL query against columns that already exist.
- **Do not re-open the MIT-vs-AGPL question.** `CLAUDE.md §10` records the decision; nothing found this session bears on it.

---

## §4 — Decisions needed before any of this becomes code

Per `CLAUDE.md §1.5`, these are architecture decisions that get resolved before implementation, not during.

1. **Reindex budget.** F-2 (chunk units), F-6 (binary quantization), and R-2 (tokenizer) are each a full reindex. Is there one migration window, or none? This determines whether they are sequenced together or deferred wholesale.
2. **What is the eval baseline?** `tests/eval/harness.py`, `metrics.py`, and `queries.json` were added in the last three commits and I did not run them. Every "measure first" recommendation above assumes they work. **Run them at `764d41e` and record the numbers before changing anything** — otherwise there is no before.
3. **Is Paper 1 driving the roadmap, or the product?** R-5 (sufficiency), F-4 (contextual retrieval), and F-5 (learned routing) are all publishable. R-1, R-3, R-6, R-8 are not, and are strictly more urgent for users. `CLAUDE.md §9` blocks Paper 4 on Paper 1 results; that pressure argues for the research items, and it should be an explicit choice rather than drift.
4. **Does the OCR workstream continue first?** `CLAUDE.md §8.3` lists G4, G5, G6, and T2 as blocking, and §0.6 above shows G4 references a setting that does not exist. `CLAUDE.md §1.6` is one workstream at a time. Nothing in this document should start while OCR gates are open, unless OCR is being parked.

---

## §5 — External sources

Each with the confidence level I would defend it at.

| Claim | Confidence | Source |
|---|---|---|
| Contextual retrieval: 35% / 49% / 67% failure-rate reductions | High — published, widely reproduced | [Claude Cookbook: contextual embeddings](https://platform.claude.com/cookbook/capabilities-contextual-embeddings-guide) · [freeCodeCamp explainer](https://www.freecodecamp.org/news/how-contextual-embeddings-and-hybrid-search-fix-retrieval-failures/) |
| Binary quantization: 32× memory reduction, ~96% performance retained with rescoring | High — vendor-documented with method detail | [Sentence Transformers: Embedding Quantization](https://sbert.net/examples/sentence_transformer/applications/embedding-quantization/README.html) · [Towards Data Science](https://towardsdatascience.com/649627-2/) |
| Combined dimensionality reduction + quantization tradeoffs | Medium — recent preprint, not independently replicated | [arXiv 2606.01074](https://arxiv.org/html/2606.01074v1) |
| EmbeddingGemma-300M: on-device, <200 MB quantized, competitive MTEB | Medium — secondary sources, no first-party benchmark read | [BentoML open-source embedding guide](https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models) · [Milvus 2026 comparison](https://milvus.io/blog/choose-embedding-model-rag-2026.md) |
| Windows Recall opt-in by default; security researchers still extracting data without admin rights; Microsoft "pulling back" Copilot/Recall push Jan 2026 | Medium — press reporting, not primary | [GeekWire](https://www.geekwire.com/2026/one-year-after-its-rocky-launch-microsofts-windows-recall-still-raises-security-red-flags/) · [Windows Central](https://www.windowscentral.com/software-apps/windows-11/microsoft-says-windows-recall-will-enter-public-preview-in-october) |
| Apple rebuilt Spotlight/Mail search infra; WWDC26 shipped a developer API for on-device LLM semantic search (`SpotlightSearchTool` + `LanguageModelSession`) | Medium-high — Apple developer session exists | [WWDC26: LLM search using Core Spotlight](https://developer.apple.com/videos/play/wwdc2026/246/) · [Eclectic Light Company](https://eclecticlight.co/2026/06/28/last-week-on-my-mac-spotlight-on-semantics/) |
| Ollama `/api/tags` carries `context_length` in `details`; `/api/show` historically did not | **Low — do not build on this without direct verification** | [ollama/ollama#2732](https://github.com/ollama/ollama/issues/2732) · [docs.ollama.com](https://docs.ollama.com/api-reference/show-model-details) |

### Landscape read

The two OS incumbents `CLAUDE.md §3` names as commoditization risks have diverged, and the divergence is useful.

**Microsoft** is the weaker threat than the roadmap assumes. Recall is opt-in, Copilot+ hardware-gated, still generating security coverage a year past launch, and reportedly being scaled back. It also solves a different problem — screen capture over time, not document corpus retrieval.

**Apple** is the stronger threat and it is under-weighted in `CLAUDE.md §3`. Shipping a *developer API* for on-device semantic search over app content is precisely PMA's shape, with OS-level indexing, zero install friction, and no model management. The mitigation is not technical — it is that PMA runs on Windows and Linux, where no equivalent exists, and targets 4 GB-class hardware Apple does not sell. **That is a real moat and it is narrower than the one `CLAUDE.md` describes.** The defensible statement is "the only local-first corpus RAG on commodity Windows/Linux hardware," not "local-first is structurally incompatible with cloud business models."

`CLAUDE.md §3` already flags the 4 GB segment as shrinking. Nothing found this session contradicts that. It remains the largest strategic risk in the project and it is not addressed by anything in this backlog.

---

## §6 — What was not examined

Stated so this document's coverage is not overestimated:

- `app/ocr/**` (2,700+ lines, ~1,600 lines of new tests) — read only for defect-status confirmation. The active workstream deserves its own pass.
- `app/providers/**` beyond `get_model_class` and the config surface. `launcher.py` (364 lines) is entirely new since `dbbcfe7`.
- `app/insights/**`, `app/scanner/ntfs_mft.py`, `app/indexing/watcher.py`, `app/indexing/folder_profiler.py`, `app/indexing/code_chunker.py`.
- `rust_core` — not located in the tree during this pass; `frontend/src-tauri/src/lib.rs` was not read.
- **No tests were run.** Every "measure this" recommendation is unexecuted. `tests/eval/harness.py` in particular is cited repeatedly and was never invoked.
- No profiling. P-2, P-3, and P-4 are structural estimates from source reading, explicitly marked `INF`, and should be confirmed with `tracemalloc` / `cProfile` before any of them justifies a change.

---

## §7 — Citation verification

Every `path:line` in this document was re-resolved against `764d41e` after drafting, by printing the cited line and comparing it to the claim. All anchors matched except one, corrected in place: `_BASE_DIR` is `app/main.py:44`, not `:43`.

Reproduce with:

```bash
git rev-parse --short HEAD   # expect 764d41e
sed -n '156,161p' app/search/retrieval.py     # R-1  _sanitize_fts_query
sed -n '381,386p' app/search/context_builder.py  # R-4  stale-score cutoff
sed -n '48,49p'   app/search/reranker.py      # R-6  no SessionOptions
sed -n '170,187p' app/embeddings/service.py   # R-6  the config it should copy
sed -n '293p'     app/search/agentic.py       # R-5  sufficiency floor
grep -n "agentic_evidence_score_floor" app/config.py
grep -rn "create_hnsw_index" app --include=*.py   # P-1  pma_chunks only
grep -rn "parent_window\|expand_parent" app       # F-1  expect no output
grep -rn "index_images" app                       # §0.6 expect no output
grep -rn "from 'sonner'" frontend/src             # U-2  expect no output
```

The `EMP` reproductions in R-1 and R-2 are a standalone script against an in-memory SQLite table matching `app/storage/schema.sql:57-62`; re-run on the Windows interpreter before acting on either, since the host SQLite version was not the one tested.
