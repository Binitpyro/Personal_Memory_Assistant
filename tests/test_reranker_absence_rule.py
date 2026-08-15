"""
tests/test_reranker_absence_rule.py

Guards the rule that R-4, R-5 and the degraded badge all depend on:

    No cutoff, floor, or threshold may be expressed on the raw cross-encoder
    logit scale unless the code has established the score is present. Absence is
    a distinct third state - "not assessed" - which disables the threshold and
    is reported. It is never defaulted to 0.0 and never silently read as "below
    threshold."

``rerank_score`` is absent on five paths: use_reranker=False, reranker timeout,
reranker inference failure, packaged builds (PMA.spec bundles no models/), and
dev checkouts. A default of 0.0 would mark every sub-question satisfied on all
five; a default of -inf would report "nothing in your files" for every question
ever asked.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.search import agentic, retrieval
from app.search.context_builder import _apply_relevance_cutoff
from app.search.reranker import RerankerFailedError, RerankerNotInstalledError


def _res(cid, score=10.0, rerank_score=None):
    r = {"chunk_id": cid, "text": f"body {cid}" * 20, "score": score, "file_path": f"{cid}.md"}
    if rerank_score is not None:
        r["rerank_score"] = rerank_score
    return r


# ── _apply_reranker_if_needed: which failures count as degradation ────────────


class TestDegradedIsPerAnswerNotCapability:
    @pytest.mark.asyncio
    async def test_model_not_installed_is_not_degradation(self, monkeypatch):
        """Absent model is an install property, true of every query.

        Flagging it per-answer lights the badge on 100% of answers on a
        packaged build, which makes the badge worthless on the day it matters.
        """
        results = [_res(1), _res(2)]

        async def boom(*a, **kw):
            raise RerankerNotInstalledError("no model")

        monkeypatch.setattr(retrieval, "rerank", boom)
        out = await retrieval._apply_reranker_if_needed(results, "q", True, 5)

        assert all("_degraded" not in r for r in out)
        assert all("rerank_score" not in r for r in out)

    @pytest.mark.asyncio
    async def test_inference_failure_is_degradation(self, monkeypatch):
        results = [_res(1), _res(2)]

        async def boom(*a, **kw):
            raise RerankerFailedError("onnx blew up")

        monkeypatch.setattr(retrieval, "rerank", boom)
        out = await retrieval._apply_reranker_if_needed(results, "q", True, 5)

        assert all(r["_degraded"] for r in out)

    @pytest.mark.asyncio
    async def test_timeout_is_degradation(self, monkeypatch):
        results = [_res(1), _res(2)]

        async def slow(*a, **kw):
            await asyncio.sleep(10)

        monkeypatch.setattr(retrieval, "rerank", slow)
        monkeypatch.setattr(asyncio, "wait_for", AsyncMock(side_effect=TimeoutError))
        out = await retrieval._apply_reranker_if_needed(results, "q", True, 5)

        assert all(r["_degraded"] for r in out)

    @pytest.mark.asyncio
    async def test_degraded_flag_survives_reordering_and_truncation(self, monkeypatch):
        """The flag used to live only on results[0].

        Downstream, _rebalance_after_rerank reorders, forced chunks are
        prepended, and _filter_retrieved_results can drop the head outright -
        each of which silently lost the flag and reported mode "full_rag".
        """
        results = [_res(i) for i in range(5)]

        async def boom(*a, **kw):
            raise RerankerFailedError("x")

        monkeypatch.setattr(retrieval, "rerank", boom)
        out = await retrieval._apply_reranker_if_needed(results, "q", True, 5)

        reordered = list(reversed(out))[1:]  # reorder, then drop the old head
        assert any(r.pop("_degraded", False) for r in reordered)

    @pytest.mark.asyncio
    async def test_single_candidate_skips_the_reranker(self, monkeypatch):
        """P-6 cost guard: one candidate cannot be reordered."""
        called = MagicMock()

        async def spy(*a, **kw):
            called()
            return []

        monkeypatch.setattr(retrieval, "rerank", spy)
        out = await retrieval._apply_reranker_if_needed([_res(1)], "q", True, 5)

        called.assert_not_called()
        assert len(out) == 1


# ── R-4: the relevance cutoff must read the scale that ordered the list ───────


class TestRelevanceCutoff:
    def test_negative_logits_do_not_empty_the_context(self):
        """Cross-encoder logits are signed.

        A ratio cutoff multiplies a negative top score *upward*, so every chunk
        including the best one falls below it and the LLM gets an empty context.
        """
        results = [_res(1, rerank_score=-1.5), _res(2, rerank_score=-1.8)]
        out = _apply_relevance_cutoff(results, score_multiplier=0.2)
        assert out, "a healthy negative-logit result set must not be filtered away"
        assert out[0]["chunk_id"] == 1

    def test_below_floor_is_dropped(self):
        results = [_res(1, rerank_score=5.0), _res(2, rerank_score=-9.0)]
        out = _apply_relevance_cutoff(results, score_multiplier=0.2)
        assert [r["chunk_id"] for r in out] == [1]

    def test_unassessed_falls_back_to_the_rrf_ratio(self):
        results = [_res(1, score=10.0), _res(2, score=1.0)]
        out = _apply_relevance_cutoff(results, score_multiplier=0.5)
        assert [r["chunk_id"] for r in out] == [1]

    def test_mixed_scales_filter_nothing(self):
        results = [_res(1, score=10.0, rerank_score=3.0), _res(2, score=1.0)]
        out = _apply_relevance_cutoff(results, score_multiplier=0.5)
        assert len(out) == 2


# ── R-5: sufficiency, and what may enter the not-found list ──────────────────


class TestSufficiency:
    def _state(self, chunks):
        st = agentic.QueryState(query="q")
        st.subqueries = [agentic.SubQuery(text="sq")]
        st.evidence = [agentic.Evidence(subquery="sq", chunk=c, folder_tag="notes") for c in chunks]
        return st

    def test_unassessed_evidence_is_unverified_not_satisfied(self, monkeypatch):
        """The old floor of 0.0 vs strictly-positive RRF marked everything satisfied."""
        st = agentic.sufficiency_node(self._state([_res(1, score=25.0)]))
        assert st.subqueries[0].status == "unverified"

    def test_unverified_stays_out_of_the_not_found_list(self):
        st = agentic.sufficiency_node(self._state([_res(1, score=25.0)]))
        assert st.unanswered() == [], "must not claim nothing was found when nothing was judged"

    def test_below_floor_is_reported_as_not_found(self, monkeypatch):
        monkeypatch.setattr(agentic.settings, "agentic_evidence_score_floor", -2.0)
        st = agentic.sufficiency_node(self._state([_res(1, rerank_score=-8.0)]))
        assert st.subqueries[0].status == "unanswered"
        assert st.unanswered() == ["sq"]

    def test_above_floor_is_satisfied(self, monkeypatch):
        monkeypatch.setattr(agentic.settings, "agentic_evidence_score_floor", -2.0)
        st = agentic.sufficiency_node(self._state([_res(1, rerank_score=4.0)]))
        assert st.subqueries[0].status == "satisfied"
        assert st.unanswered() == []

    def test_no_evidence_at_all_is_not_found(self):
        st = agentic.sufficiency_node(self._state([]))
        assert st.subqueries[0].status == "unanswered"
        assert st.unanswered() == ["sq"]
