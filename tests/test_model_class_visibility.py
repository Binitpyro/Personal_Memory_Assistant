"""Provider metadata that PMA was guessing instead of reading.

Three changes, all the same shape: Ollama already reports the answer in the
`/api/tags` response `list_models` fetches, and PMA was inferring it from the
model name instead.

  D1  the declared context window, instead of a hardcoded 8192 for every model
      (display and diagnostics only - it is not a budget)
  D3  a warning when a model lands in the large context class purely because
      nothing in its name parsed
  --  `family` from reported `capabilities` instead of a substring match, which
      was calling two text-only models "vision" and offering them to OCR

They came out of a design review that REJECTED a larger proposal - deriving the
context budget from the declared window - because `model_class` also selects a
delivery shape that a window size cannot inform, and collapsing the two would
have invalidated the 8.7f-8.7h measurements.

> **One premise of that review was itself wrong, and is retracted.** It rested
> partly on 8.7f reading `gemma2-2b`'s ~4,099-token truncation as a property of
> the model. It is not: the 4,096 cliff is Ollama's default `num_ctx`, and
> `gemma4-local` hits it identically with a 131,072-token window. See
> `_required_num_ctx` and TestNumCtxIsSetSoOllamaStopsDiscardingContext. The
> review's conclusion still stands on its other two objections.

Deterministic, no network (CLAUDE.md section 11). The live values encoded here
were verified against a running Ollama separately.
"""

import logging

import pytest

from app.providers.ollama import _FALLBACK_CONTEXT_LENGTH, _family, _reported_context_length
from app.search import llm_client
from app.search.llm_client import _classify_local_model


class TestReportedContextLength:
    """D1. Measured live: gemma4-local 131072, gemma2-2b 8192, 12B 262144."""

    def test_it_reads_the_window_ollama_reports(self):
        assert _reported_context_length({"details": {"context_length": 131072}}) == 131072
        assert _reported_context_length({"details": {"context_length": 262144}}) == 262144

    def test_a_small_model_is_not_inflated_to_the_old_literal(self):
        """8192 must be a value it can report, not a floor it gets rounded up to."""
        assert _reported_context_length({"details": {"context_length": 2048}}) == 2048

    @pytest.mark.parametrize(
        "item",
        [
            {},
            {"details": {}},
            {"details": {"context_length": None}},
            {"details": {"context_length": 0}},
            {"details": {"context_length": -1}},
            {"details": {"context_length": "131072"}},  # string, not int
        ],
        ids=["no-details", "empty", "null", "zero", "negative", "string"],
    )
    def test_it_degrades_to_the_fallback_rather_than_raising(self, item):
        """An older server or a hand-built manifest may omit or mangle the field.

        Dropping the model from the listing would be worse than showing a
        conservative number, so every unusable shape lands on the fallback.
        """
        assert _reported_context_length(item) == _FALLBACK_CONTEXT_LENGTH

    def test_the_listing_shape_is_unchanged(self):
        """`ModelInfo.context_length` is typed int and rendered with .toLocaleString()
        at ProvidersPage.tsx:700, so None would crash the picker."""
        assert isinstance(_reported_context_length({"details": {}}), int)


class TestUnparsedModelNameIsAnnounced:
    """D3. This library's models are all custom `*-local` imports with no digits,
    so the silent fallback is the normal path here, not an edge case."""

    @pytest.fixture(autouse=True)
    def _clear_warned_names(self):
        llm_client._UNPARSED_MODEL_NAMES.clear()
        yield
        llm_client._UNPARSED_MODEL_NAMES.clear()

    def test_a_nameless_size_warns_and_names_the_remedy(self, caplog):
        with caplog.at_level(logging.WARNING, logger=llm_client.__name__):
            assert _classify_local_model("gemma4-local") == "7b_local"

        assert len(caplog.records) == 1
        msg = caplog.records[0].message
        assert "gemma4-local" in msg
        # The warning is only useful if it says what to do about it.
        assert "MODEL_CLASS_OVERRIDES" in msg
        assert "truncated head-first" in msg

    def test_it_warns_once_per_model_not_once_per_query(self, caplog):
        """`get_model_class` runs on every query; an ungated warning would be one
        log line per question asked."""
        with caplog.at_level(logging.WARNING, logger=llm_client.__name__):
            for _ in range(5):
                _classify_local_model("gemma4-local")
        assert len(caplog.records) == 1

        with caplog.at_level(logging.WARNING, logger=llm_client.__name__):
            _classify_local_model("another-custom-model")
        assert len(caplog.records) == 2, "a different model deserves its own warning"

    @pytest.mark.parametrize("name", ["llama3.2:1b", "qwen2.5:7b", "phi3-mini", "tinyllama-small"])
    def test_a_confident_classification_stays_silent(self, name, caplog):
        """Parsed sizes and the mini/small conventions are not guesses."""
        with caplog.at_level(logging.WARNING, logger=llm_client.__name__):
            _classify_local_model(name)
        assert caplog.records == []

    def test_an_override_suppresses_the_warning(self, caplog, monkeypatch):
        """The warning tells the user to set an override; having set one, they
        must not keep being told."""
        from app.config import settings

        monkeypatch.setattr(settings, "model_class_overrides", {"gemma4-local": "3b_local"})
        with caplog.at_level(logging.WARNING, logger=llm_client.__name__):
            assert _classify_local_model("gemma4-local") == "3b_local"
        assert caplog.records == []

    def test_classification_is_unchanged_by_the_warning(self):
        """D3 is observability only. It must not move a single boundary."""
        assert _classify_local_model("gemma4-local") == "7b_local"
        assert _classify_local_model("llama3.2:1b") == "3b_local"
        assert _classify_local_model("qwen2.5:7b") == "7b_local"
        assert _classify_local_model("") == "7b_local"
        assert _classify_local_model(None) == "7b_local"


class TestFamilyComesFromReportedCapabilities:
    """`looks_like_vision_model` lists "gemma4" as a vision fragment, and it is
    wrong in the DANGEROUS direction for two of this machine's six models.

    Its own docstring names the asymmetry: "silently running OCR through a
    text-only model produces confident hallucinated page text that lands in the
    search index." Measured live 2026-09-04:

        glm-ocr            vision,completion,tools      guess vision   correct
        gemma4-local       completion,tools,thinking    guess vision   FALSE POS
        gemma4-12B-local   completion,tools,thinking    guess vision   FALSE POS
    """

    def test_reported_capabilities_beat_the_name(self):
        """The regression under test: the name says vision, the server says no."""
        assert (
            _family(
                {"name": "gemma4-local:latest", "capabilities": ["completion", "tools", "thinking"]}
            )
            == "chat"
        )
        assert (
            _family({"name": "gemma4-12B-local:latest", "capabilities": ["completion", "tools"]})
            == "chat"
        )

    def test_a_real_vision_model_is_still_found(self):
        """The fix must not trade false positives for false negatives."""
        assert (
            _family({"name": "glm-ocr:latest", "capabilities": ["vision", "completion"]})
            == "vision"
        )
        # A vision model whose NAME gives nothing away is the case only the
        # reported capabilities can catch.
        assert _family({"name": "my-custom-import:latest", "capabilities": ["vision"]}) == "vision"

    @pytest.mark.parametrize(
        "item",
        [
            {"name": "llava:7b"},
            {"name": "llava:7b", "capabilities": []},
            {"name": "llava:7b", "capabilities": None},
            {"name": "llava:7b", "capabilities": "vision"},  # str, not list
        ],
        ids=["absent", "empty", "null", "wrong-type"],
    )
    def test_it_falls_back_to_the_heuristic_when_nothing_is_reported(self, item):
        """An older Ollama reports no capabilities, and LM Studio never will."""
        assert _family(item) == "vision"

    def test_the_fallback_is_still_only_a_guess(self):
        """Pins that the fallback is the OLD behaviour, warts included - a
        model named `gemma4-*` with no reported capabilities is still guessed
        vision. That is the heuristic's problem, and the fix is to report
        capabilities, not to keep patching the fragment list."""
        assert _family({"name": "gemma4-local:latest"}) == "vision"

    def test_a_name_with_no_capabilities_and_no_match_is_chat(self):
        assert _family({"name": "qwen-coder-local:latest"}) == "chat"
        assert _family({}) == "chat"
