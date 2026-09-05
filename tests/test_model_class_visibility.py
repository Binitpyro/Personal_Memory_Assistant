"""The two changes a design review approved out of a larger, rejected proposal.

CLAUDE.md 8.7f proposed deriving the context budget from the provider's reported
window, deleting `EFFECTIVE_CEILINGS` and the name heuristic together. **That was
rejected on measured evidence**: Ollama reports `context_length: 8192` for
`gemma2-2b`, which 8.7f measured truncating at ~4,099 tokens - the declared
window is 2x what the model honours, so trusting it would have reproduced the
same silent truncation with more confidence behind it.

What survived is smaller and cannot regress a budget:

  D1  report the model's real declared window instead of a hardcoded 8192,
      for display and diagnostics only
  D3  say so when a model gets the large context class purely because nothing
      in its name parsed

These are deterministic and hit no network (CLAUDE.md section 11). The live
values they encode were verified against a running Ollama separately.
"""

import logging

import pytest

from app.providers.ollama import _FALLBACK_CONTEXT_LENGTH, _reported_context_length
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
