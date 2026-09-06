"""The generation metrics, which are the only ones with no index behind them.

Not marked ``eval``: these are string functions. They need no model, no corpus
and no network, so they belong in the default suite where they can catch drift
in the thing every chunking decision will be argued from.

The case that matters most is ``test_verbose_answer_keeps_recall_and_loses_f1``.
prompts/rag_system.txt instructs the model to be "detailed, comprehensive", so
every real answer is a superset of its reference. If F1 were read as the primary
signal, that instruction - not chunking - would set the score.
"""

from __future__ import annotations

from tests.eval.metrics import answer_token_f1, answer_token_recall, boundary_margin

REFERENCE = "The geometry cache writes one file per frame to the scratch volume."


def test_exact_match_scores_one():
    assert answer_token_recall(REFERENCE, REFERENCE) == 1.0
    assert answer_token_f1(REFERENCE, REFERENCE) == 1.0


def test_verbose_answer_keeps_recall_and_loses_f1():
    """The whole reason recall is primary and F1 is only reported."""
    verbose = (
        "Based on the indexed sources [1], the geometry cache writes one file "
        "per frame to the scratch volume. This is configured in the farm "
        "submission template and can be overridden per shot, though most "
        "facilities leave the default in place for cache coherence reasons."
    )
    assert answer_token_recall(verbose, REFERENCE) == 1.0
    assert answer_token_f1(verbose, REFERENCE) < 0.6


def test_unrelated_answer_scores_zero():
    unrelated = "Curl noise is divergence free."
    assert answer_token_recall(unrelated, REFERENCE) == 0.0
    assert answer_token_f1(unrelated, REFERENCE) == 0.0


def test_partial_answer_scores_between():
    partial = "The geometry cache writes one file."
    recall = answer_token_recall(partial, REFERENCE)
    assert 0.0 < recall < 1.0
    assert 0.0 < answer_token_f1(partial, REFERENCE) < 1.0


def test_empty_inputs_score_zero():
    assert answer_token_recall("", REFERENCE) == 0.0
    assert answer_token_recall(REFERENCE, "") == 0.0
    assert answer_token_f1("", REFERENCE) == 0.0
    assert answer_token_f1(REFERENCE, "") == 0.0
    assert answer_token_f1("", "") == 0.0


def test_articles_and_punctuation_are_normalised_away():
    """SQuAD normalisation. Without it, formatting differences read as errors."""
    loose = "geometry cache, writes a file!"
    assert answer_token_recall(loose, "the geometry cache writes file") == 1.0
    assert answer_token_f1("GEOMETRY -- CACHE", "the geometry cache") == 1.0


def test_repeated_reference_tokens_are_counted_as_a_multiset():
    """Set intersection would call the half-answer a full hit."""
    assert answer_token_recall("cache", "cache cache") == 0.5
    assert answer_token_recall("cache cache", "cache cache") == 1.0


class TestBoundaryMargin:
    """Pins the stratifier behind the chunk_overlap decision (CLAUDE.md D2), so
    the recorded 19/100 is reproducible rather than a number in a write-up."""

    @staticmethod
    def _chunks(*bounds):
        return [{"start_offset": s, "end_offset": e} for s, e in bounds]

    def test_margin_is_the_distance_to_the_nearer_edge(self):
        chunks = self._chunks((0, 1000))
        # 40 from the left edge, 860 from the right
        assert boundary_margin(chunks, {"start": 40, "end": 140}) == 40
        # 30 from the right edge, 870 from the left
        assert boundary_margin(chunks, {"start": 870, "end": 970}) == 30

    def test_a_span_reachable_at_one_overlap_is_not_at_a_smaller_one(self):
        """The contract the stratification depends on: reachable at overlap v
        means margin < v, so shrinking v must shrink the population."""
        chunks = self._chunks((0, 1000))
        margin = boundary_margin(chunks, {"start": 60, "end": 160})
        assert margin == 60
        assert margin < 102 and margin < 205, "counted in the 102 and 205 subsets"
        assert not margin < 51, "must NOT be counted in the 51 subset"

    def test_centre_of_a_chunk_is_unreachable(self):
        chunks = self._chunks((0, 1000))
        assert boundary_margin(chunks, {"start": 480, "end": 520}) == 480

    def test_none_when_no_chunk_contains_the_span(self):
        assert boundary_margin(self._chunks((0, 100)), {"start": 500, "end": 600}) is None

    def test_uses_the_first_containing_chunk_when_a_span_straddles(self):
        """Straddling is ~0 on corpus_squad at overlap=0, but corpus_large's long
        answers do it, so the branch must be defined rather than incidental."""
        chunks = self._chunks((0, 1000), (900, 1900))
        assert boundary_margin(chunks, {"start": 950, "end": 1050}) == -50
