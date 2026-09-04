# PROGRAM — the brief for the research loop

Human-edited. This is the equivalent of `program.md` in karpathy/autoresearch:
the loop reads it to know what it is optimising and what it may not trade away.
`research/journal.jsonl` is the memory of what has already been tried; read it
before proposing anything, because a configuration already in there has already
been paid for.

---

## The objective

**Mean generation token-recall against the labelled answer span, per model arm.**
Higher is better. Produced by `scripts/eval_chunking.py --gen-models ...`,
summarised by `scripts/autoresearch.py`.

Recall, not F1, and `tests/eval/metrics.py` carries the reasoning: the system
prompt orders the model to be "detailed, comprehensive" and to emit
`[source_index]` citations, so precision's denominator is set by the prompt
rather than by anything under test. F1 is recorded for comparability with
published extractive-QA numbers and is not the thing to maximise.

**The detection threshold is ~0.06 answer-recall at three builds.** Measured
2026-09-03 from 9 runs of a configuration the knob under test could not change:
sd 0.036 per build, ~0.021 on a 3-build mean. Any delta smaller than that is not
a result. Screen candidates on the deterministic delivery metric first and spend
generation only where delivery already moved - a generation-only sweep of a small
effect will produce a trend that a control run alongside it would have destroyed.

**Always sweep with a control arm.** A model or class the knob cannot affect,
measured in the same runs. The `max_chunks_small` sweep looked like a clean
monotonic win on the affected model until the control moved further.

**When delivery is a valid screen, and when it is not.** Delivery coverage is
deterministic and ~4x cheaper than generation (86 s per run against 315 s), but
it is only a proxy for knobs that change **how much** text is delivered - not for
knobs that change **what** the text is. `context_snippet_head_share` changes
volume alone and delivery predicted generation closely. `chunk_size` also changes
semantic coherence, and there delivery ranked `3b_local` in the *opposite* order
from generation. Screen on delivery, confirm on generation, and never skip the
confirmation for a knob that alters chunk content.

**A null is only null for the configuration it was measured in.** `max_per_file`
swept flat while the snippet allocation was the binding constraint - everything
downstream of that ceiling was starved equally, so per-file dedup had nothing to
change. Re-running it after the allocation was fixed was the right call even
though the answer held. Journal rows carry `code` (short SHA, `+dirty` marker)
so a sweep spanning a code change is visible; `--resume` deliberately does NOT
key on it, because invalidating the journal on every commit would make the loop
useless rather than rigorous.

**Two nulls in a row mean the model of the system is wrong. Read, do not sweep.**
`max_chunks` and `max_per_file` both came back flat because the real ceiling was
the geometric budget split in `_format_snippets`, which neither knob touches.
Reading that function found in minutes what more sweeping would never have found.

**Rank on the floor across builds, never the mean.** Three independent builds
per configuration is the default because chunk ids are assigned in completion
order by a concurrent pipeline and ties resolve differently per build. CLAUDE.md
8.7d records one configuration measuring 0.509 and 0.634 on identical settings,
and a wrong conclusion drawn from a single run. A configuration is worth what its
worst build is worth.

---

## Rejection gates

A candidate is **discarded**, not penalised. Weighted sums introduce weights
nobody can justify, which is the 8.7d failure exactly.

1. **External validity.** SciFact nDCG@10 more than one standard deviation below
   the current best. 8 queries over 24 generated documents will be overfit inside
   ten iterations and nothing else detects it. *(Corpus materialised 2026-09-03 by
   `scripts/fetch_beir.py`: 5,183 documents, 300 judged queries, join verified.
   The gate arms once a baseline nDCG@10 has been recorded; until then treat every
   internal win as unvalidated.)*
2. **Latency.** p95 query wall-clock above budget. A configuration that wins by
   feeding a small model 8k tokens at 3 tok/s is not shippable.
3. **Correctness.** `ruff check .` repo-wide, plus the targeted test subset.
   Broken candidates are discarded before they cost an index build.

Diagnostics recorded but **never optimised against**: `char_precision`,
`chunk_precision`, context tokens, index build time, abstention count.

---

## Constraints that may not be traded away

- **Section 1.3 — license boundary.** No DCC, paid-module, or Creative code in
  this repo. Ever.
- **Section 1.4 — privacy defaults.** No configuration may route to a cloud
  provider by default, phone home, emit telemetry, or require network at first
  run. A cloud LLM judge is not an option; the labelled spans exist so that
  scoring is arithmetic.
- **Section 6 — hardware.** ~4GB VRAM, 250 MB idle ceiling, 1 GB ingestion
  transient, and the context budget is a binding constraint, not a dial to turn
  up. More context is not automatically better; that is the whole open question.
- **Section 4 — pins.** LanceDB 0.30.2, the model pins in `models.lock.json`,
  and the src-tauri MSRV do not move without sign-off.
- **No new dependencies.** Section 6's policy wants a measured bottleneck first.
- **Any new `1/(k + rank)` needs its own k, swept against its own list length.**
  8.7d: `rrf_k` is 60, tuned for three-signal chunk fusion over a recall window,
  and reusing it over a 40-item pool spread rank 1 to rank 40 by only 1.64x.

---

## What is known, so the loop does not rediscover it

Read CLAUDE.md 8.7 through 8.7e in full. The short version:

| settled | |
|---|---|
| `chunk_size` | 512 -> 2048 chars; delivery coverage 0.569 / 0.645 / 1.000 at `model_class="cloud"` |
| parent windows | built and shipped; earns its place most at small chunk sizes |
| BGE query prefix | shipped; identical in the reranker-on arm, kept on three other grounds |
| reranker | `time_budget_ms` deleted (enforced nothing); RRF fusion at k=10, chosen on the floor |
| `chunk_markdown` routing (A3) | measured worse twice, precision 0.163 -> 0.100. Needs a corpus where headings carry real signal |
| `max_chunks_small` | swept 3/5/8, flat. Does not bind. Do not retry |
| `max_per_file_small` | swept 1/2/3 TWICE - before and after the head-share fix. 1 is best in both eras (0.398 / 0.822 vs 0.359 / 0.743). Confirmed, do not retry |
| `context_ceiling_small` | swept 4000/4500/5000. +0.079 at ~1 sd, against silent head-first truncation risk. Deliberately NOT raised |
| `parent_window_multiplier` | swept 3/5/7. +0.080 then plateau, ~1 sd, and costs 7b_local 14% more tokens for nothing. Not changed |
| `reranker_rrf_fusion_weight` | swept 0.0/0.5/1.0. 3b coverage moves 0.02. Ranking is NOT the small class's constraint |
| `context_max_chunks_small` | re-swept post-fix. Still null - the head share starves the tail now, the allocation ceiling did before |
| `context_snippet_head_share` | **the real ceiling.** 0.34 caps 3 snippets at 71.2% of budget; raising to 0.7 takes gemma2-2b recall 0.317 -> 0.618 with the 7b control flat. Saturating. **Default unchanged pending sign-off** |

**Settled 2026-09-03 (CLAUDE.md 8.7f).** `chunk_size=2048` confirmed on
generation: gemma4-local 0.563 -> 0.729 across 512/1024/2048, decisive; gemma2-2b
0.267 -> 0.328, about 3 sd. Every delivery number behind the original 2048
decision had been measured at `model_class="cloud"`, which the segment section 3
serves never receives; the eval reports every class now.

**Open and live.** `3b_local` delivered coverage tops out at 0.484 while
`7b_local` reaches 1.000, and gemma2-2b scores 0.328 answer-recall against
gemma4-local's 0.729. What bounds it is **not** `max_chunks`: swept 3/5/8, tokens
flat at ~1,703, coverage non-monotonic. `max_per_file = 1` is the next candidate,
then `EFFECTIVE_CEILINGS`, which is still a function-local literal.

> **Two knobs named in the original plan are no-ops - do not sweep them.**
> `settings.context_max_tokens` is read at one site only
> (`context_builder.py`, the `max_tokens <= 0` fallback); production always
> passes an explicit budget, so it cannot move any local class.
> `settings.retrieval_top_k` is only a default argument value and every eval call
> site passes `-k` explicitly. With the reranker on, `score_multiplier` is also
> unused: `_apply_relevance_cutoff` switches to an absolute floor once every
> result carries a `rerank_score`.

---

## corpus_squad lifted the fixture limit - read this before sweeping anything else

Six of eight queries deliver the whole answer to `3b_local`. `geometry_cache`
(0.525) is starved of material; `turbulence` (0.059) has its answer at rank 4-10
where only the 15-slot class sees it. Both are single-query failures, so fixing
either moves the mean by ~0.12 against a delivery noise floor of sd 0.07-0.12.

Four knobs have now been swept against it and all four are null at ~1 sd. That is
not four failures, it is one result: **the tuning headroom on this corpus is
gone.** `head_share` and `max_chunks` are antagonistic - at 0.7 the first three
snippets take 97.3% of the budget, so more slots get ~80 tokens each - and with a
2,520 token budget *fewer and fuller* beats *more and thinner*, because an answer
span only counts if it arrives whole.

**That was true of `corpus_large`, and it was fixed the same day.**
`scripts/fetch_squad_corpus.py` builds a span-labelled corpus from SQuAD - 48 real
Wikipedia articles of ~33k characters, 899 chunks, 100 queries, every span
verified to slice back to its answer text (6,361 candidates, 0 mismatches).

**The delivery noise floor drops from sd 0.069-0.137 to sd 0.0053-0.0096** - a
7-14x cut, so effects about 7x smaller become decidable. Sweep against
`--corpus tests/eval/corpus_squad --queries tests/eval/queries_squad.json`.

**Re-run the nulls there.** `context_ceiling_small`, `parent_window_multiplier`
and `max_per_file_small` were all ~1 sd on the old fixture and would be many sigma
on this one. Those nulls were a property of the fixture, not of the knobs.

**One is done and it confirmed the whole argument.** `max_per_file_small` 1 -> 2
measured +0.079 at ~1 sd on `corpus_large` and was recorded as a null; on
`corpus_squad` it measures **+0.063 with completely disjoint per-value ranges**
(1's best build 0.8467, 2's worst 0.8976), saturating at 2, with the `7b_local`
control flat within 0.0034. **Shipped** - `3b_local` delivered coverage
0.842 -> 0.904 against cloud's 0.909.

Two remain, and they are not equally worth the runs:

- **`parent_window_multiplier` 3/5/7 - run this one.** It was declined purely on
  significance (+0.080 then plateau, ~1 sd, and it cost `7b_local` 14% more
  tokens for nothing). Better power can genuinely flip that. It also has to be
  re-run rather than merely re-scored, because it was swept at
  `max_per_file_small = 1` and that default has since changed - both knobs move
  how much material reaches the small class, so the old rows are not a
  comparison against shipped code. `_code_version` on each row is what makes
  that checkable.
- **`context_ceiling_small` 4000/4500/5000 - measure if convenient, but the
  decision probably does not move.** It was declined on an asymmetric-risk
  argument, not a significance one: overrunning a model's real window truncates
  **head first and silently**, costing the system instructions before it costs
  evidence, and `3b_local` is assigned by parsing the model NAME, so headroom
  measured on gemma2-2b is not headroom for anything else landing in that class.
  Better statistics do not answer that; deriving the budget from the provider's
  reported `context_length` does, and that is the real fix.

**Keep all three corpora, they measure different things.** `corpus_squad` for
power (short answers only), `corpus_large` for long-answer delivery, SciFact for
externally comparable document ranking.

## Traps that have already cost time

- **Prove a sweep actually varies what it claims, before spending the runs.**
  Twice in one session a sweep was nearly a structural no-op. `PMA_*` -> settings
  mapping was checked first and was fine. The class list was not: `eval_chunking`
  derived it from `--gen-models`, so a delivery-only screening run built only the
  `"cloud"` class and a sweep of a `3b_local`-only setting produced nine runs with
  nothing to compare. Fixed, but the habit is the point - one cheap assertion
  before a long run. A leaderboard cannot tell you the knob did nothing; it will
  happily rank identical configurations.

- **Thinking models return reasoning in `message.thinking`, not `content`.**
  `OllamaProvider.chat` reads only `content`. A tight `num_predict` spends the
  whole budget on reasoning and returns an **empty** answer, which scores as a
  total miss and reads exactly like "dilution destroys answers". Keep
  `--gen-max-tokens` generous; `empty` is reported per query for this reason.
- **`_classify_local_model` guesses from the model *name*.** Every locally
  imported GGUF here is named `*-local` with no parameter count.
  `gemma4-local` (7.5B) lands on `7b_local` by fallback, correctly, by luck. A
  small custom-named model would land on `7b_local`, receive an 8,520-token
  budget, and be silently truncated. `settings.model_class_overrides` is the fix
  and it is empty.
- **Use Ollama's HTTP API, never the CLI.** `ollama list` hung past 120 s on this
  machine while `/api/tags` answered in 4 ms.
- **Cold model load is ~61 s.** Generation is model-major for that reason.
- **`PMA_LANCEDB_MODE=portable`.** `.env` here sets `split_brain`, which a default
  install never does. `autoresearch.py` sets portable already.
- **Never `pathlib.write_text` an untracked file in place.** It truncates on
  open; CLAUDE.md was destroyed that way. Temp file plus `os.replace`.
- **`D:` is exFAT with no journal.** Interrupted builds leave corrupt directory
  entries. `--resume` exists because of this.
