# EDITABLE — what the code-space loop may change

Phase 5 only. Phases 1–4 are config space: `PMA_*` environment variables and
nothing else. This file exists because a loop that may edit anything will
eventually edit the thing that makes the measurement lie.

**Isolation is mandatory.** Code-space runs happen in a git worktree on branch
`research/auto`, never on `updates`:

```
git worktree add ../pma-research research/auto
```

The loop commits kept candidates to that branch. **Merging is manual and outside
the loop's reach.** Section 3: there is no second reviewer here, and this phase
writes code without one.

---

## In scope

| file | what may change |
|---|---|
| `app/search/context_builder.py` | `EFFECTIVE_CEILINGS`, `max_chunks`, `max_per_file`, `score_multiplier`, the per-snippet budget in `_format_snippets` |
| `app/search/retrieval.py` | fusion weighting, `attach_parent_windows`, `_bounded_candidates`, the sub-50-character drop in `_build_candidate_results` |
| `app/indexing/service.py` | `_create_chunks` and the chunker dispatch only |

**Start with `context_builder.py`, and start with `3b_local`.** Measured
2026-09-02 at the shipped `chunk_size=2048`, off identical retrieval: delivered
coverage 1.000 for `cloud`, 1.000 for `7b_local`, **0.437 for `3b_local`**, with
delivered tokens pinned at ~1,796 on every query — a hard truncation ceiling, not
a content-driven size. Five of eight queries deliver 0.017–0.069 of their answer.

The knobs that cause it are **not reachable from config space**, which is the
whole reason this phase exists:

- `EFFECTIVE_CEILINGS` is a function-local literal inside
  `compute_context_budget`. `settings.context_max_tokens` reaches only the
  `max_tokens <= 0` fallback, so `PMA_CONTEXT_MAX_TOKENS` cannot move any local
  class.
- `max_chunks = 3` and `max_per_file = 1` for `3b_local` are literals in
  `build_context`.
- `settings.retrieval_top_k` is only a default argument value, and every eval
  call site passes `-k` explicitly.

Generation recall tracks delivered coverage closely on the 3B arm (coverage
0.017–0.069 produced recall 0.065–0.172), so improving delivery for that class is
expected to move the objective directly rather than through a proxy.

## Out of scope

| | why |
|---|---|
| **Anything under `tests/eval/`** | the instrument. A loop that may edit its own scorer will optimise the scorer. Corpus, spans, metrics and harness are frozen. |
| **`app/scanner/rust_core/`** | needs `maturin develop --release` per experiment, which blows the wall-clock budget — and section 13's trap is a correctness hazard: a failed build leaves the *previous* module installed, so the experiment silently scores unchanged code. |
| **`app/config.py` defaults** | that is config space. Sweep it, do not edit it. A default changed in code and also swept from the environment is a confound. |
| **`prompts/rag_system.txt`** | changes the objective's denominator. Legitimate research, but it is a separate experiment from chunking and must not move underneath one. |
| **`app/providers/`, `app/storage/`, `app/api/`, anything frontend** | not on the retrieval path. |
| **Pins** | LanceDB 0.30.2, `models.lock.json`, src-tauri MSRV, `rust-toolchain.toml`. Section 4. |

## Gate before scoring

In order. A candidate that fails any of these is discarded **before** it costs an
index build:

1. `ruff check .` — repo-wide, not per-file. Section 13 records the gate going
   red twice from per-file checking.
2. `ruff format --check .`
3. `mypy .`
4. `pytest tests/test_eval_metrics.py tests/test_autoresearch_journal.py
   tests/test_eval_corpus_spans.py tests/test_retrieval_hardening.py -q`

The full gate (`scripts\run_ci_checks.bat`) runs before any merge to `updates`,
never per candidate — it takes minutes and would dominate the loop.

## Known first targets

- **`3b_local` delivery**, above. The open question is whether `max_chunks=3` is
  simply wrong, or right at a smaller `chunk_size`.
- **A3 — `chunk_markdown` routing.** Measured worse twice, precision 0.163 to
  0.100. Needs a corpus where headings carry real signal; do not retry it on
  `corpus_large` and expect a different answer.
- **The reranker's 750–940 ms/query.** Inside the 5 s guard, so latency not
  correctness — a gate-2 concern, not an objective one.
