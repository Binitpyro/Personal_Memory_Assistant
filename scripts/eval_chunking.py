"""Baseline runner for the chunking work.

Indexes `tests/eval/corpus_large` with the real pipeline and reports both metric
families side by side:

* **document-level** - recall/nDCG/MRR, what `tests/eval` already measured
* **span-level** - chunk precision and answer coverage, which is the half that
  can actually see chunking (CLAUDE.md 8.7 D2)

Run it before changing anything about chunking and keep the JSON. Every later
claim about chunk size, the code chunker or parent windows is a diff against
this file, and a claim without one is the thing section 8.4a warns about.

Reranker on *and* off, because they answer different questions. Off is the
clean read on candidate selection, which is what fusion ablations need. On is
what a user actually gets, and it had never been measured against labels at all
(D3) - `tests/eval/harness.py` hardcoded it off.

    .venv\\Scripts\\python.exe scripts/eval_chunking.py --json-out baseline_chunking.json

Needs `BAAI/bge-small-en-v1.5` on disk, and the cross-encoder for the
reranker-on arm; that arm is skipped with a note when it is missing rather than
reported as zeros.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from tests.eval import harness, metrics

DEFAULT_CORPUS = Path("tests/eval/corpus_large")
DEFAULT_QUERIES = Path("tests/eval/queries_large.json")

# Rebound from the CLI in main(). Separate from the DEFAULT_ constants so the
# argparse defaults are never the same names main() declares global, which
# reads to mypy as use-before-definition.
CORPUS = DEFAULT_CORPUS
QUERIES = DEFAULT_QUERIES

# Both are overridable from the CLI so this can also run the external corpus
# scripts/fetch_beir.py materialises. Queries without answer_spans - which is
# every BEIR query, since BEIR judges documents - make the span and delivery
# sections report nothing rather than a column of 0.000, which is the correct
# behaviour and is why those two paths skip unlabelled queries.

# prompts/rag_system.txt line 2 defines this exact escape hatch, so it is a
# reliable abstention signal rather than a heuristic. A model that says it has
# run out of evidence is behaving correctly; folding that into recall would
# score honesty as failure and reward confabulation.
_ABSTENTION = "don't have enough information"


def _provenance(k: int, gen_models: list[str], gen_max_tokens: int, gen_provider: str) -> dict:
    """Stamp what produced the numbers.

    Mirrors scripts/eval_retrieval.py. A retrieval measurement without the
    settings that produced it is not comparable to anything later, and the
    chunking settings are exactly what this workstream is about to change.
    """
    from app.config import settings

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "k": k,
        "corpus": str(CORPUS),
        "settings": {
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "chunk_boundary_lookback_share": settings.chunk_boundary_lookback_share,
            "embed_chunk_prefix": settings.embed_chunk_prefix,
            "embedding_model": settings.embedding_model,
            "embedding_batch_size": settings.embedding_batch_size,
            "embedding_batch_char_budget": settings.embedding_batch_char_budget,
            "rrf_fts_weight": settings.rrf_fts_weight,
            "rrf_semantic_weight": settings.rrf_semantic_weight,
            "rrf_summary_weight": settings.rrf_summary_weight,
            "rrf_k": settings.rrf_k,
            "retrieval_top_k": settings.retrieval_top_k,
            "fusion_balance_enabled": settings.fusion_balance_enabled,
            "context_max_tokens": settings.context_max_tokens,
            "parent_window_enabled": settings.parent_window_enabled,
            "parent_window_multiplier": settings.parent_window_multiplier,
        },
        "generation": {
            "provider": gen_provider,
            "models": gen_models,
            "model_classes": _classes_for(gen_models, gen_provider),
            "max_tokens": gen_max_tokens,
            "temperature": 0.0,
            "ollama_url": settings.ollama_url,
        },
    }


async def _delivery_scores(
    index, queries, k: int, use_reranker: bool, model_classes: tuple[str, ...] = ("cloud",)
) -> tuple[dict, dict]:
    """Score the context the model is actually handed, not the retrieval output.

    The retrieval metrics above stop at `hybrid_retrieve`. Production continues
    through `attach_parent_windows` and `build_context`, and that stretch can
    remove an answer retrieval already scored as found - measured at 2.4x
    overstatement at chunk_size=512 (CLAUDE.md 8.7e). Reporting both side by side
    is the point: a retrieval win that does not survive delivery is not a win.

    Returns ``(scores, contexts)``. ``scores`` is for ``model_classes[0]`` and is
    the figure that stays comparable to ``baseline_chunking.json``; ``contexts``
    maps model_class -> {query_id: assembled context} for the generation arm.

    **The model_class parameter is the fix for a blind spot this script had.**
    It called ``build_context`` without one, so it took the default, "cloud":
    an 8000-token budget with ``max_chunks=15`` and ``max_per_file=2``. Both
    production call sites (retrieval.py:1257, :1633) pass a real class, and for
    "3b_local" - the segment section 3 exists to serve - that is a 2,520 token
    budget with ``max_chunks=3``, ``max_per_file=1``, and folder profiles and
    graph paths blanked entirely. Those are different functions, not different
    parameters, so every delivery number behind chunk_size=2048 describes a
    configuration a local 3B user never receives.

    The "cloud" arm keeps the old budget rather than production's 98,520, so
    the recorded baseline stays comparable. Only the added arms are new.
    """
    from app.search import retrieval
    from app.search.context_builder import build_context, compute_context_budget

    out: dict[str, dict] = {cls: {} for cls in model_classes}
    contexts: dict[str, dict[str, str]] = {cls: {} for cls in model_classes}
    for q in queries:
        if not q.answer_spans:
            continue
        retrieval.clear_retrieval_cache()
        results = await retrieval.hybrid_retrieve(
            query=q.query,
            db=index.db,
            embedding_service=index.embeddings,
            lancedb_client=index.lancedb,
            k=k,
            use_reranker=use_reranker,
        )
        # The two production steps the harness never runs.
        await retrieval.attach_parent_windows(index.db, results)

        span = q.answer_spans[0]
        source = (CORPUS / span["file"]).read_text(encoding="utf-8")
        answer = source[int(span["start"]) : int(span["end"])]

        # Retrieval is class independent, so it runs once and every class is
        # assembled from the same ranked results.
        for cls in model_classes:
            budget = (
                settings.context_max_tokens if cls == "cloud" else compute_context_budget(cls, 0)
            )
            context, tokens = build_context(results, max_tokens=budget, model_class=cls)
            contexts[cls][q.id] = context
            out[cls][q.id] = {
                "context_coverage": metrics.context_answer_coverage(context, answer),
                "context_tokens": tokens,
                "answer_len": q.answer_len,
            }
    return out, contexts


def _format_delivery(delivery: dict) -> str:
    if not delivery:
        return ""
    lines = [
        f"{'query':<24} {'answer':<7} {'context_cov':>12} {'ctx_tokens':>11}",
        "-" * 58,
    ]
    for qid, d in delivery.items():
        lines.append(
            f"{qid:<24} {d['answer_len'] or '-':<7} "
            f"{d['context_coverage']:>12.3f} {d['context_tokens']:>11d}"
        )
    n = len(delivery)
    lines.append("-" * 58)
    lines.append(
        f"{'DELIVERED':<24} {'':<7} "
        f"{sum(d['context_coverage'] for d in delivery.values()) / n:>12.3f} "
        f"{sum(d['context_tokens'] for d in delivery.values()) / n:>11.0f}"
    )
    return "\n".join(lines)


def _classes_for(model_tags: list[str], provider: str = "ollama") -> dict[str, str]:
    """Map each model tag onto the context class production would give it.

    Goes through `LLMClient.get_model_class`, which is what both production call
    sites use, rather than reimplementing the rule - a second copy of a heuristic
    is how an evaluation quietly stops measuring the product.

    The provider matters and an earlier version ignored it. `get_model_class`
    parses a parameter count from the *name* only for local providers; for a
    cloud one it returns "cloud" outright. Passing a NIM tag such as
    `nvidia/llama-3.1-nemotron-70b-instruct` through the local rule would find
    "70b" and hand a cloud model a `7b_local`-shaped context - an 8,520 token
    budget instead of 98,520, which is not a configuration that can occur.
    """
    from app.search.llm_client import LLMClient

    client = LLMClient()
    return {tag: client.get_model_class(provider, tag) for tag in model_tags}


def _make_provider(provider_id: str, tag: str, timeout: float):
    """Build a provider for the generation arm.

    Deliberately NOT through `LLMClient._resolve_provider_by_id`. That path
    enforces `llm.cloud_privacy_consent`, which guards the PRODUCT: it exists so
    a user's indexed documents cannot reach a cloud endpoint without an explicit
    opt-in. This is a measurement script over
    `tests/eval/corpus_large`, which `scripts/generate_eval_corpus.py` generates
    from templates - fictional prose containing nothing personal. Flipping the
    persisted consent flag to run an experiment would change the app's privacy
    state as a side effect of a measurement, which is worse than not using it.

    Cloud keys come from the same keyring entry the app writes, so no key is
    stored in the repo, in a fixture, or on a command line.
    """
    from app.providers import create_provider

    if provider_id in ("ollama", "lm_studio"):
        base = settings.ollama_url if provider_id == "ollama" else settings.lm_studio_url
        return create_provider(provider_id, base_url=base, default_model=tag, timeout=timeout)

    # Same precedence as LLMClient._resolve_provider_by_id: the environment
    # (settings) first, keyring second. They can disagree - measured 2026-09-03,
    # when the keyring held a stale key that returned 403 while .env held a
    # working one - and reading only the keyring would have scored a live model
    # as unreachable.
    import keyring

    api_key = (getattr(settings, f"{provider_id}_api_key", "") or "").strip()
    source = "env"
    if not api_key:
        api_key = keyring.get_password("pma_backend", provider_id) or ""
        source = "keyring"
    if not api_key:
        raise RuntimeError(
            f"no API key for {provider_id!r} in settings or keyring. "
            "Add it through the app's provider settings; this script never stores one."
        )
    print(f"--- generation provider {provider_id}: key from {source} ---")
    return create_provider(provider_id, api_key=api_key, default_model=tag, timeout=timeout)


async def _generation_scores(
    contexts: dict[str, dict[str, str]],
    queries,
    model_tags: list[str],
    max_tokens: int,
    provider_id: str = "ollama",
) -> dict:
    """Score the answer the model writes from the delivered context.

    The stage after `_delivery_scores`, and the first measurement in this repo
    that scores the product rather than the pipeline. CLAUDE.md 8.7e closed with
    chunk_size=2048 delivering the whole answer at 2.4% character precision -
    97.6% of what the model reads is not the answer - and no instrument that
    could say whether that dilution costs anything. This is that instrument.

    Each tag is fed the context its own model_class would receive, which is the
    whole point: handing a 2.6B model a cloud-shaped 8000-token context measures
    a configuration that cannot occur.

    `generate_answer` is deliberately not used. It does not forward temperature,
    so the sampler would sit at the provider default of 0.2 - a variance source
    the three-build rule was never sized for. The *prompt* still comes from
    `LLMClient._build_prompt`, so the production prompt on the production
    context is what is measured; rebuilding the prompt here would be 8.7e Axis 1
    again, scoring a stage the product does not have.

    Three traps, measured on this machine on 2026-09-02 rather than assumed:

    1. **Thinking models return reasoning in a separate field.** Both gemma4
       tags advertise `thinking`; Ollama 0.32.6 puts it in `message.thinking`
       and `OllamaProvider.chat` reads only `message.content`. With a tight
       `num_predict` the reasoning consumes the whole budget and content comes
       back **empty** - measured: 120 tokens of thinking, `content == ""`. A
       400-token cap would have scored recall 0.0 on every arm and read exactly
       like "dilution destroys answers". Hence a generous default, and `empty`
       reported per query so a harness failure cannot pass for a model failure.
    2. **Cold model load is ~61s.** Model-major ordering, every query for one
       tag before the next, or reloads dominate the run.
    3. **Abstention is not failure.** Counted separately, never folded into
       recall: a model that says it lacks evidence is behaving correctly, and
       scoring that as a wrong answer would reward confabulation.
    """
    import time

    from app.search.llm_client import LLMClient

    client = LLMClient()
    classes = _classes_for(model_tags, provider_id)
    out: dict = {}

    for tag in model_tags:
        cls = classes[tag]
        per_class = contexts.get(cls)
        if per_class is None:
            out[tag] = {"skipped": f"no context built for model_class {cls}"}
            print(f"--- generation {tag}: SKIPPED, no context for class {cls} ---")
            continue

        provider = _make_provider(provider_id, tag, timeout=600.0)
        per_query: dict = {}
        try:
            for q in queries:
                context = per_class.get(q.id)
                if context is None:
                    continue
                span = q.answer_spans[0]
                source = (CORPUS / span["file"]).read_text(encoding="utf-8")
                reference = source[int(span["start"]) : int(span["end"])]

                prompt = client._build_prompt(q.query, context, None, supports_claims=False)
                started = time.perf_counter()
                try:
                    answer = await provider.chat(
                        client._build_messages(prompt, None),
                        model=tag,
                        temperature=0.0,
                        max_tokens=max_tokens,
                    )
                except Exception as exc:
                    # Recorded, never scored as 0.0. A provider that reports
                    # itself as a perfect miss is the failure the reranker arm
                    # above already guards against.
                    per_query[q.id] = {"error": f"{type(exc).__name__}: {exc}"}
                    continue

                per_query[q.id] = {
                    "recall": metrics.answer_token_recall(answer, reference),
                    "f1": metrics.answer_token_f1(answer, reference),
                    "abstained": _ABSTENTION in answer.lower(),
                    "empty": not answer.strip(),
                    "answer_chars": len(answer),
                    "seconds": round(time.perf_counter() - started, 2),
                    "answer_len": q.answer_len,
                }
        finally:
            await provider.close()

        errors = sum(1 for v in per_query.values() if "error" in v)
        if errors and errors == len(per_query):
            print(f"--- generation {tag}: FAILED, every query errored ---")
        out[tag] = {"model_class": cls, "per_query": per_query}
    return out


def _format_generation(generation: dict) -> str:
    if not generation:
        return ""
    lines = [
        f"{'model':<22} {'class':<10} {'recall':>7} {'rec_ans':>8} {'f1':>7} "
        f"{'abstain':>8} {'empty':>6} {'sec/q':>7}",
        "-" * 82,
    ]
    for tag, block in generation.items():
        if "skipped" in block:
            lines.append(f"{tag:<22} SKIPPED: {block['skipped']}")
            continue
        rows = [v for v in block["per_query"].values() if "error" not in v]
        if not rows:
            lines.append(f"{tag:<22} {block['model_class']:<10} all queries errored")
            continue
        n = len(rows)
        # `recall` counts an empty answer as 0.0 and is the primary scalar:
        # excluding empties would reward a configuration that induces more of
        # them. `rec_ans` excludes them, so the two together say whether a low
        # score is the pipeline or the token budget.
        answered = [r for r in rows if not r["empty"]]
        rec_ans = sum(r["recall"] for r in answered) / len(answered) if answered else 0.0
        lines.append(
            f"{tag:<22} {block['model_class']:<10} "
            f"{sum(r['recall'] for r in rows) / n:>7.3f} "
            f"{rec_ans:>8.3f} "
            f"{sum(r['f1'] for r in rows) / n:>7.3f} "
            f"{sum(r['abstained'] for r in rows):>8d} "
            f"{sum(r['empty'] for r in rows):>6d} "
            f"{sum(r['seconds'] for r in rows) / n:>7.1f}"
        )
    return "\n".join(lines)


async def _run(
    k: int,
    json_out: Path | None,
    gen_models: list[str],
    gen_max_tokens: int,
    gen_provider: str = "ollama",
) -> int:
    queries = harness.load_queries(QUERIES)
    index = await harness.EvalIndex(corpus_dir=CORPUS).build()

    # "cloud" stays first so payload["arms"][*]["delivery"] keeps meaning what it
    # meant in baseline_chunking.json. Every other class the product can produce
    # is measured alongside it rather than instead of it.
    #
    # Deriving the class list from `gen_models` was a bug: a delivery-only
    # screening run passes no models at all, so it silently measured "cloud"
    # alone - the one class no local user ever receives - and a sweep of a
    # 3b_local-only setting came back with nothing to compare. Delivery is cheap
    # (retrieval runs once and every class is assembled from the same results),
    # so there is no reason to make it conditional.
    extra = sorted(
        ({"3b_local", "7b_local"} | set(_classes_for(gen_models, gen_provider).values()))
        - {"cloud"}
    )
    model_classes = ("cloud", *extra)

    payload: dict = {
        "provenance": _provenance(k, gen_models, gen_max_tokens, gen_provider),
        "arms": {},
    }
    try:
        chunk_total = 0
        if index.db is not None:
            rows = await index.db.execute_query("SELECT COUNT(*) FROM chunks")
            chunk_total = rows[0][0] if rows else 0
        payload["provenance"]["chunks_indexed"] = chunk_total
        n_files = sum(1 for p in CORPUS.rglob("*") if p.is_file())
        print(f"indexed {chunk_total} chunks from {n_files} files\n")

        for arm, use_reranker in (("reranker_off", False), ("reranker_on", True)):
            if use_reranker:
                from app.search import reranker as rr

                rr.preload_reranker()
                if not rr.reranker_status()["available"]:
                    print(f"--- {arm}: SKIPPED, cross-encoder not on disk ---\n")
                    payload["arms"][arm] = {"skipped": "reranker model not available"}
                    continue

            run = await index.run(queries, k, use_reranker=use_reranker)
            delivery_by_class, contexts = await _delivery_scores(
                index, queries, k, use_reranker, model_classes
            )
            delivery = delivery_by_class[model_classes[0]]
            print(f"=== {arm} (k={k}) ===")
            print(harness.format_report(run, k))
            print()
            print(harness.format_chunk_report(run, k))
            print()
            for cls in model_classes:
                print(f"-- delivery, model_class={cls} --")
                print(_format_delivery(delivery_by_class[cls]))
                print()

            # Generation runs on the shipped configuration only. The
            # reranker-off arm exists to read candidate selection cleanly;
            # paying for a second full generation sweep to answer a question
            # nobody asked is exactly the kind of scope this project rejects.
            generation: dict = {}
            if gen_models and use_reranker:
                generation = await _generation_scores(
                    contexts, queries, gen_models, gen_max_tokens, gen_provider
                )
                print(_format_generation(generation))
                print()

            payload["arms"][arm] = {
                "document": harness.aggregate(run, k),
                "chunk": harness.aggregate_chunks(run, k),
                "chunk_short": harness.aggregate_chunks(run, k, "short"),
                "chunk_long": harness.aggregate_chunks(run, k, "long"),
                "per_query": {r.query.id: {**r.scores(k), **r.chunk_scores(k)} for r in run},
                "delivery": {
                    "context_coverage": (
                        sum(d["context_coverage"] for d in delivery.values()) / len(delivery)
                        if delivery
                        else 0.0
                    ),
                    "context_tokens": (
                        sum(d["context_tokens"] for d in delivery.values()) / len(delivery)
                        if delivery
                        else 0.0
                    ),
                    "per_query": delivery,
                },
                "delivery_by_class": {
                    cls: {
                        "context_coverage": (
                            sum(d["context_coverage"] for d in rows.values()) / len(rows)
                            if rows
                            else 0.0
                        ),
                        "context_tokens": (
                            sum(d["context_tokens"] for d in rows.values()) / len(rows)
                            if rows
                            else 0.0
                        ),
                        "per_query": rows,
                    }
                    for cls, rows in delivery_by_class.items()
                },
                "generation": generation,
            }
    finally:
        await index.close()

    if json_out:
        json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {json_out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Chunking baseline over corpus_large.")
    p.add_argument("-k", type=int, default=10, help="results per query (default 10)")
    p.add_argument("--json-out", type=Path, help="write the full result set here")
    p.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help="corpus directory to index (default tests/eval/corpus_large)",
    )
    p.add_argument(
        "--queries",
        type=Path,
        default=DEFAULT_QUERIES,
        help="labelled queries JSON (default tests/eval/queries_large.json)",
    )
    p.add_argument(
        "--gen-models",
        default="",
        help=(
            "comma separated Ollama tags to score generation with, e.g. "
            "gemma4-local,gemma2-2b. Empty (default) skips the generation arm "
            "entirely, which is what keeps this script runnable with no LLM up."
        ),
    )
    p.add_argument(
        "--gen-max-tokens",
        type=int,
        default=4096,
        help=(
            "num_predict for the generation arm. Defaults to 4096 to match the "
            "production default in BaseProvider.chat, and generous on purpose: a "
            "thinking model spends this budget on reasoning first and returns an "
            "EMPTY answer if it runs out. At 1024, one query in eight did exactly "
            "that and scored as a total miss."
        ),
    )
    p.add_argument(
        "--gen-provider",
        default="ollama",
        help=(
            "provider for the generation arm (default ollama). A cloud id such as "
            "nvidia_nim reads its key from the app keyring and is scored as "
            "model_class=cloud, which is a different context shape entirely."
        ),
    )
    args = p.parse_args()

    # Module-level because _delivery_scores and _generation_scores resolve
    # answer-span files against the corpus root. Rebinding here keeps the diff
    # to one place instead of threading a path through four signatures.
    global CORPUS, QUERIES
    CORPUS = args.corpus
    QUERIES = args.queries

    gen_models = [m.strip() for m in args.gen_models.split(",") if m.strip()]
    return asyncio.run(
        _run(args.k, args.json_out, gen_models, args.gen_max_tokens, args.gen_provider)
    )


if __name__ == "__main__":
    raise SystemExit(main())
