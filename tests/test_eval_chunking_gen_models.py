"""`eval_chunking.py` generation-arm selection: which model runs, on which context.

Pure helpers, so no index, model or network. The A/B between a local and a
cloud model is only paired if both run in one invocation, and only isolates the
model if both read the same context - each test guards one of those.
"""

from __future__ import annotations

from scripts.eval_chunking import _CLOUD_SHIPPED, _fed_class, _parse_gen_models, _variant_runs


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
