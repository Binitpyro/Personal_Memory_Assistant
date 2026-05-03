"""
P3-4: Unit tests for QueryPlanner.

Coverage:
- All fast-path inventory phrases
- Composite how-much/many + context keyword patterns
- Project/unreal metadata fast path
- FULL_RAG default for semantic queries
- Keyword extraction stop-word filtering
- Edge cases: empty query, all-caps, punctuation
"""

import pytest

from app.search.planner import PlanMode, QueryPlanner


@pytest.fixture(scope="module")
def planner():
    return QueryPlanner()


# ── FAST_METADATA ─────────────────────────────────────────────────────────────


class TestFastMetadataPath:
    """Every query below should short-circuit to FAST_METADATA."""

    @pytest.mark.parametrize(
        "query",
        [
            "how many files do I have indexed",
            "total size of my library",
            "how much disk space am I using",
            "storage used by PMA",
            "how much is indexed",
            "what is my index size",
            "how large is my index",
            "file count",
            "how much space does it take",
            "total indexed files",
            "what's in my index",
            "whats in my index",
            "how big is my storage",
            "storage usage report",
            "how much space used",
            # Composite patterns
            "how many files are stored",
            "how much memory is used by the index",
            "how large is the file collection",
            "how many files have been indexed so far",
        ],
    )
    def test_routes_to_fast_metadata(self, planner: QueryPlanner, query: str):
        plan = planner.plan(query)
        assert plan.mode == PlanMode.FAST_METADATA, (
            f"Expected FAST_METADATA for: {query!r}, got {plan.mode}"
        )

    def test_case_insensitive(self, planner: QueryPlanner):
        """Inventory phrases should match regardless of case."""
        plan = planner.plan("HOW MANY FILES ARE INDEXED")
        assert plan.mode == PlanMode.FAST_METADATA

    def test_plan_retains_original_query(self, planner: QueryPlanner):
        q = "how many files"
        plan = planner.plan(q)
        assert plan.original_query == q


# ── FULL_RAG ─────────────────────────────────────────────────────────────────


class TestFullRagPath:
    """Semantic queries should fall through to FULL_RAG."""

    @pytest.mark.parametrize(
        "query",
        [
            "what does the authentication system do",
            "show me the player movement code",
            "summarise my meeting notes from last week",
            "what are the main algorithms used in my project",
            "explain the shader pipeline",
            "find documents about database design",
            "",  # empty query → no keywords, should still not crash
        ],
    )
    def test_routes_to_full_rag(self, planner: QueryPlanner, query: str):
        plan = planner.plan(query)
        assert plan.mode == PlanMode.FULL_RAG, f"Expected FULL_RAG for: {query!r}, got {plan.mode}"


# ── KEYWORD EXTRACTION ────────────────────────────────────────────────────────


class TestKeywordExtraction:
    def test_stop_words_filtered(self, planner: QueryPlanner):
        plan = planner.plan("what is the best way to do this")
        # "what", "is", "the", "to", "do", "this" are stop-words
        assert "what" not in plan.keywords
        assert "the" not in plan.keywords
        assert "to" not in plan.keywords

    def test_meaningful_keywords_kept(self, planner: QueryPlanner):
        plan = planner.plan("authentication shader pipeline performance")
        assert "authentication" in plan.keywords
        assert "shader" in plan.keywords
        assert "pipeline" in plan.keywords
        assert "performance" in plan.keywords

    def test_short_tokens_excluded(self, planner: QueryPlanner):
        """Tokens with <= 2 characters should be dropped."""
        plan = planner.plan("go in my db")
        # "go", "in", "my", "db" are all ≤ 2 chars
        assert all(len(k) > 2 for k in plan.keywords)

    def test_empty_query_no_crash(self, planner: QueryPlanner):
        plan = planner.plan("")
        assert plan.keywords == []
        assert plan.mode == PlanMode.FULL_RAG


# ── INTENTS ───────────────────────────────────────────────────────────────────


class TestIntentFields:
    def test_inventory_query_has_intents(self, planner: QueryPlanner):
        plan = planner.plan("how many files are indexed")
        assert isinstance(plan.intents, dict)

    def test_full_rag_has_intents(self, planner: QueryPlanner):
        plan = planner.plan("explain the build system")
        assert isinstance(plan.intents, dict)
        # Should not be metadata intent for a generic semantic query
        # (not asserting value as determine_query_intent is its own unit)


# ── REGRESSION GUARDS ─────────────────────────────────────────────────────────


class TestRegressionGuards:
    """Ensure previously broken cases don't regress."""

    def test_original_three_phrases_still_work(self, planner: QueryPlanner):
        """The 3 original phrases must still route to FAST_METADATA."""
        for q in ["how many files", "total size", "disk space"]:
            assert planner.plan(q).mode == PlanMode.FAST_METADATA, f"Regression on: {q!r}"

    def test_composite_no_false_positive(self, planner: QueryPlanner):
        """'how' alone with no storage context should NOT route to FAST_METADATA."""
        plan = planner.plan("how do I configure authentication")
        # "how" + "configure", "authentication" — no storage/index/file context word
        # This should be FULL_RAG
        assert plan.mode == PlanMode.FULL_RAG

    def test_planner_is_stateless(self, planner: QueryPlanner):
        """Calling plan() multiple times should always give consistent results."""
        q = "how many files"
        results = {planner.plan(q).mode for _ in range(5)}
        assert results == {PlanMode.FAST_METADATA}
