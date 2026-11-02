"""`eval_chunking.py` generation-arm selection: which model runs, on which context.

Pure helpers, so no index, model or network. The A/B between a local and a
cloud model is only paired if both run in one invocation, and only isolates the
model if both read the same context - each test guards one of those.
"""

from __future__ import annotations

import asyncio

import pytest

from scripts.eval_chunking import (
    _CLOUD_SHIPPED,
    _fed_class,
    _load_step,
    _parse_gen_models,
    _parse_pipeline,
    _run_pipeline,
    _variant_runs,
)


def test_provider_prefix_splits_per_model():
    pairs = _parse_gen_models(
        "ollama:gemma4-local, nvidia_nim:nvidia/nemotron-3-ultra-550b-a55b", "ollama"
    )
    assert pairs == [
        ("ollama", "gemma4-local"),
        ("nvidia_nim", "nvidia/nemotron-3-ultra-550b-a55b"),
    ]


def test_bare_and_colon_tags_keep_the_default_provider():
    # An Ollama tag's own colon must not be read as a provider prefix.
    assert _parse_gen_models("gemma2:2b,gemma4-local,", "ollama") == [
        ("ollama", "gemma2:2b"),
        ("ollama", "gemma4-local"),
    ]


def test_forced_class_overrides_every_model():
    assert _fed_class("cloud", "7b_local") == "7b_local"
    assert _fed_class("7b_local", "7b_local") == "7b_local"


def test_shipped_gives_cloud_the_production_budget_only():
    assert _fed_class("cloud", "shipped") == _CLOUD_SHIPPED
    assert _fed_class("7b_local", "shipped") == "7b_local"


def test_unset_keeps_old_behaviour():
    assert _fed_class("cloud", None) == "cloud"


def test_prompt_suffix_pairs_each_model_with_a_variant_run():
    runs = _variant_runs([("ollama", "g"), ("nvidia_nim", "n")], "  BE BRIEF. ", "af")
    # Same tag back to back: one cold load, identical context for both arms.
    assert runs == [
        ("ollama", "g", "g", ""),
        ("ollama", "g", "g|af", "\n\nBE BRIEF.\n"),
        ("nvidia_nim", "n", "n", ""),
        ("nvidia_nim", "n", "n|af", "\n\nBE BRIEF.\n"),
    ]


def test_no_suffix_runs_each_model_once_on_the_shipped_prompt():
    assert _variant_runs([("ollama", "g")], None) == [("ollama", "g", "g", "")]


def test_pipeline_spec_parses_label_and_steps():
    assert _parse_pipeline("refine=@baseline, mp/refine.txt") == (
        "refine",
        ["@baseline", "mp/refine.txt"],
    )
    for bad in ("refine", "=a.txt", "refine="):
        with pytest.raises(ValueError):
            _parse_pipeline(bad)


def _recorder(sent: list[str], thinks: list | None = None):
    async def chat(text: str, think: bool | None = None) -> str:
        sent.append(text)
        if thinks is not None:
            thinks.append(think)
        return f"out{len(sent)}"

    return chat


def test_each_pass_reads_the_previous_pass():
    sent: list[str] = []
    fields = {"context": "C", "query": "Q", "shipped": "S"}
    steps: list[tuple[str, bool | None]] = [
        ("{shipped}|{query}", None),
        ("{context}<{prev}>", None),
    ]
    passes = asyncio.run(_run_pipeline(steps, fields, _recorder(sent), None))
    assert sent == ["S|Q", "C<out1>"]
    assert [p["text"] for p in passes] == ["out1", "out2"]


def test_nothink_step_turns_reasoning_off_for_that_pass_only(tmp_path):
    (tmp_path / "q1.txt").write_text("copy", encoding="utf-8")
    (tmp_path / "q2.txt").write_text("answer {prev}", encoding="utf-8")
    _, raw = _parse_pipeline(f"quote=nothink:{tmp_path / 'q1.txt'},{tmp_path / 'q2.txt'}")
    steps = [_load_step(s) for s in raw]
    assert steps == [("copy", False), ("answer {prev}", None)]
    thinks: list = []
    asyncio.run(_run_pipeline(steps, {}, _recorder([], thinks), None))
    assert thinks == [False, None]


def test_baseline_step_reuses_the_draft_without_a_call():
    sent: list[str] = []
    base = {"answer": "draft", "seconds": 9.0}
    passes = asyncio.run(
        _run_pipeline(
            [("@baseline", None), ("fix {prev}", None)],
            {"context": "", "query": "", "shipped": ""},
            _recorder(sent),
            base,
        )
    )
    # One call, not two: the refine arm pays only for the pass it adds.
    assert sent == ["fix draft"]
    assert passes[0] == {"text": "draft", "seconds": 9.0}


def test_baseline_step_without_a_baseline_answer_raises():
    for base in (None, {"error": "502"}):
        with pytest.raises(RuntimeError):
            asyncio.run(_run_pipeline([("@baseline", None)], {}, _recorder([]), base))
