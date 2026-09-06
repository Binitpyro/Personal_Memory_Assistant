import asyncio
import inspect
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.search import reranker as reranker_mod
from app.search import retrieval
from app.search.reranker import RerankerNotInstalledError, rerank

# Bound before any monkeypatching. `retrieval.asyncio` IS the asyncio module,
# so patching the attribute patches it globally - calling asyncio.wait_for from
# inside the replacement then recurses into the replacement. Caught by a
# RecursionError the first time this test ran.
_REAL_WAIT_FOR = asyncio.wait_for


async def _wait_for_briefly(aw, timeout):
    """Stand-in for asyncio.wait_for with the production 5.0 s collapsed.

    The value under test is the *behaviour* on expiry, not the duration; waiting
    the real five seconds in a unit test buys nothing.
    """
    return await _REAL_WAIT_FOR(aw, 0.05)


def test_query_heuristics_and_fts_sanitization():
    # Test new heuristics logic via intent determination
    from app.project_constants import determine_query_intent

    assert determine_query_intent("show me the latest files")["latest"]
    assert determine_query_intent("what is the biggest file")["largest"]

    # Terms are OR-ed, not implicitly AND-ed. The old expression required every
    # token to co-occur in one 512-character chunk, so real questions matched
    # nothing and the keyword leg contributed an empty list on every chat query.
    sanitized = retrieval._sanitize_fts_query('hello AND "world" * test')
    assert sanitized == '"hello" OR "test" OR "world"'

    # Sub-trigram terms are dropped: they cannot be indexed by a trigram
    # tokenizer, and under the old AND they constrained nothing at all.
    assert retrieval._sanitize_fts_query("3D pipeline") == '"pipeline"'

    # Nothing matchable -> empty expression, which _fts_search reports and
    # short-circuits rather than issuing a MATCH that cannot hit.
    assert retrieval._sanitize_fts_query('""') == ""
    assert retrieval._sanitize_fts_query("is a of") == ""

    # plan.keywords wins when supplied - it is the stop-word-stripped form.
    assert (
        retrieval._sanitize_fts_query("what is the turbulence model", ["turbulence", "model"])
        == '"model" OR "turbulence"'
    )


def test_compute_rrf_scores_and_filter_results(monkeypatch):
    monkeypatch.setattr(retrieval.settings, "rrf_k", 60)
    monkeypatch.setattr(retrieval.settings, "rrf_fts_weight", 1.0)
    monkeypatch.setattr(retrieval.settings, "rrf_semantic_weight", 1.0)

    fts = [{"id": "1"}, {"id": "2"}]
    sem = [{"id": "2"}, {"id": "3"}]
    ranked = retrieval._compute_rrf_scores(fts, sem, None, k=3)

    ids = [chunk_id for chunk_id, _ in ranked]
    assert "2" in ids
    assert len(ranked) == 3

    filtered = retrieval._filter_retrieved_results(
        [
            {"file_path": "a.py", "folder_tag": "A"},
            {"file_path": "b.md", "folder_tag": "B"},
        ],
        file_type=".py",
        folder_tag="A",
    )
    assert filtered == [{"file_path": "a.py", "folder_tag": "A"}]


@pytest.mark.asyncio
async def test_load_query_metadata_and_gather_full_inputs(monkeypatch):
    class FakeDB:
        async def get_all_folder_profiles(self):
            return [{"folder_tag": "A"}]

        async def get_file_stats_summary(self):
            return {"total_files": 1, "total_size_mb": 1.0, "by_type": [], "by_folder": []}

        async def get_folder_profiles_text(self):
            return "profiles text"

    db = FakeDB()
    profiles, stats = await retrieval._load_query_metadata(
        db,
        inventory=True,
        project=True,
    )
    assert profiles and stats

    async def fake_hybrid_retrieve(**_kwargs):
        return [{"file_path": "a.py", "text": "x", "folder_tag": "A"}]

    class FakeEmb:
        async def embed_query(self, q):
            return [0.1] * 384

    class FakeLanceDB:
        async def search_summaries(self, *args, **kwargs):
            return {"metadatas": [[]]}

    monkeypatch.setattr(retrieval, "hybrid_retrieve", fake_hybrid_retrieve)
    retrieved, out_stats, profiles_text = await retrieval._gather_full_rag_inputs(
        query="q",
        db=db,
        embedding_service=FakeEmb(),
        lancedb_client=FakeLanceDB(),
        k=3,
        inventory=True,
        project=True,
        cached_file_stats=stats,
        include_profiles_text=True,
    )
    assert retrieved and out_stats == stats and profiles_text == "profiles text"


@pytest.mark.asyncio
async def test_rerank_empty_short_circuit():
    assert await rerank("query", []) == []


@pytest.mark.asyncio
async def test_rerank_with_mock_model(monkeypatch):
    import numpy as np

    class FakeSession:
        def run(self, output_names, inputs):
            return [np.array([[0.1], [0.9]])]

    class FakeEncoding:
        ids = [1]  # noqa: RUF012
        attention_mask = [1]  # noqa: RUF012
        type_ids = [0]  # noqa: RUF012

    class FakeTokenizer:
        def encode_batch(self, pairs):
            return [FakeEncoding(), FakeEncoding()]

    class FakeLoop:
        async def run_in_executor(self, _executor, fn):
            return fn()

    monkeypatch.setattr(
        "app.search.reranker._get_model_assets", lambda: (FakeSession(), FakeTokenizer())
    )
    monkeypatch.setattr("app.search.reranker.asyncio.get_running_loop", lambda: FakeLoop())
    # Isolates the cross-encoder path: scores reach the items and drive the
    # order. RRF fusion is a policy layer on top and is covered by
    # TestRerankerRrfFusion, so it is switched off here rather than silently
    # changing what this test asserts.
    #
    # It genuinely does change it: with two candidates whose RRF and
    # cross-encoder orders are exact opposites, the fused scores are symmetric,
    # the sort is stable, and the incoming order wins. A real candidate pool is
    # ~40 items where that cancellation does not arise.
    monkeypatch.setattr(reranker_mod.settings, "reranker_rrf_fusion_weight", 0.0)

    results = [
        {"text": "first", "file_path": "a.py"},
        {"text": "second", "file_path": "b.py"},
    ]
    ranked = await rerank("question", results, top_k=1, text_key="text")
    assert len(ranked) == 1
    assert ranked[0]["file_path"] == "b.py"
    assert ranked[0]["rerank_score"] == 0.9


def test_build_candidate_results_no_longer_deduplicates(monkeypatch):
    """Dedup moved out of the candidate stage.

    It used to run here on a MinHash of a 200-character middle slice, before
    the reranker, so it could drop a chunk the reranker would have promoted -
    and two chunks with similar middles but different heads and tails were
    treated as duplicates. There is now a single exact pass after reranking in
    ``context_builder._deduplicate_redundant``; this stage passes candidates
    through untouched.
    """
    base = "prefix" * 20  # 120 chars
    middle1 = " This is the target signature content that should match. " * 3  # ~150 chars
    suffix1 = " suffix1" * 10
    suffix2 = " suffix2" * 10

    text1 = base + middle1 + suffix1
    text2 = base + middle1 + suffix2  # same middle, different tail
    text3 = "different" * 50

    row_map = {
        1: (1, text1, "file1.txt", "tag1", 12345, 0, 10, "[]", "1.0", 11),
        2: (2, text2, "file2.txt", "tag2", 12345, 0, 10, "[]", "1.0", 12),
        3: (3, text3, "file3.txt", "tag3", 12345, 0, 10, "[]", "1.0", 13),
    }
    chunk_ids_ordered = [1, 2, 3]
    score_map = {1: 1.0, 2: 0.9, 3: 0.8}

    monkeypatch.setattr(retrieval.settings, "rrf_score_scale", 1.0)

    results = retrieval._build_candidate_results(chunk_ids_ordered, row_map, score_map)

    assert [r["chunk_id"] for r in results] == [1, 2, 3]
    assert [r["score"] for r in results] == [1.0, 0.9, 0.8]


def test_build_candidate_results_short_text():
    # Chunks < 50 chars should be skipped
    row_map = {
        1: (1, "too short", "file1.txt", "tag1", 12345, 0, 10, "[]", "1.0", 11),
        2: (2, "A" * 60, "file2.txt", "tag2", 12345, 0, 10, "[]", "1.0", 12),
    }
    chunk_ids_ordered = [1, 2]
    score_map = {1: 1.0, 2: 0.9}

    results = retrieval._build_candidate_results(chunk_ids_ordered, row_map, score_map)

    assert len(results) == 1
    assert results[0]["chunk_id"] == 2


class TestRerankerDeadline:
    """The reranker's *real* deadline, which had no coverage at all.

    `rerank()` runs to completion and has no internal budget - the removed
    `time_budget_ms` never enforced one (see its docstring). What actually
    guards the interactive path is `_apply_reranker_if_needed`'s
    `asyncio.wait_for(..., timeout=5.0)`: on expiry the answer is served in RRF
    order and flagged degraded.

    That behaviour is the whole justification for deleting the parameter, so it
    is asserted here rather than assumed.
    """

    RESULTS = (
        {"chunk_id": 1, "text": "alpha", "file_path": "a.md"},
        {"chunk_id": 2, "text": "beta", "file_path": "b.md"},
    )

    def _results(self):
        return [dict(r) for r in self.RESULTS]

    @pytest.mark.asyncio
    async def test_timeout_serves_rrf_order_and_flags_every_result(self, monkeypatch):
        async def never_finishes(*_a, **_kw):
            await asyncio.sleep(3600)

        monkeypatch.setattr(retrieval, "rerank", never_finishes)
        # Keep the test fast: the production 5.0 is the value under test in
        # principle, not the number worth waiting for in a unit test.
        monkeypatch.setattr(retrieval.asyncio, "wait_for", _wait_for_briefly)

        results = self._results()
        out = await retrieval._apply_reranker_if_needed(results, "q", True, k=2)

        assert [r["chunk_id"] for r in out] == [1, 2], "must fall back to RRF order"
        assert all(r.get("_degraded") for r in out), (
            "every result must be flagged - the head of the list gets reordered "
            "and can be dropped downstream, so a flag on results[0] is lost"
        )
        assert not any("rerank_score" in r for r in out)

    @pytest.mark.asyncio
    async def test_success_is_not_flagged_degraded(self, monkeypatch):
        async def reranked(query, results, top_k, text_key):
            out = list(reversed(results))
            for i, r in enumerate(out):
                r["rerank_score"] = float(i)
            return out

        monkeypatch.setattr(retrieval, "rerank", reranked)

        out = await retrieval._apply_reranker_if_needed(self._results(), "q", True, k=2)
        assert [r["chunk_id"] for r in out] == [2, 1]
        assert not any(r.get("_degraded") for r in out)

    @pytest.mark.asyncio
    async def test_missing_model_is_capability_state_not_per_answer_degradation(self, monkeypatch):
        """An uninstalled reranker is true of every query on the install.

        Flagging it would light a degraded badge on 100% of answers and make the
        badge meaningless - the same "a signal that always fires is not a
        signal" reasoning that removed the 500 ms warning.
        """

        async def not_installed(*_a, **_kw):
            raise RerankerNotInstalledError("no model on disk")

        monkeypatch.setattr(retrieval, "rerank", not_installed)

        out = await retrieval._apply_reranker_if_needed(self._results(), "q", True, k=2)
        assert [r["chunk_id"] for r in out] == [1, 2]
        assert not any(r.get("_degraded") for r in out)

    @pytest.mark.asyncio
    async def test_rerank_takes_no_time_budget_argument(self):
        """Locks the deletion. Re-adding a budget here would re-add a parameter
        that cannot enforce anything from inside a running executor call."""
        assert "time_budget_ms" not in inspect.signature(rerank).parameters


class TestParentWindows:
    """Small-to-big expansion. See CLAUDE.md 8.7 A5 - section 5 claimed this
    shipped for a long time while no such code existed anywhere."""

    SOURCE = "".join(f"sentence {i:02d} of the document. " for i in range(60))
    PREFIX = "[MD: doc.md] "
    CHUNK = 200
    OVERLAP = 40

    def _chunks(self):
        """Overlapping chunks in the shape the indexer stores them."""
        out, start = [], 0
        while start < len(self.SOURCE):
            end = min(start + self.CHUNK, len(self.SOURCE))
            out.append((self.PREFIX + self.SOURCE[start:end], start, end))
            if end >= len(self.SOURCE):
                break
            start = end - self.OVERLAP
        return out

    def _db(self):
        rows = self._chunks()

        class FakeDB:
            async def execute_query(self, sql, params):
                _fid, lo, hi = params
                return [r for r in rows if r[2] > lo and r[1] < hi]

        return FakeDB()

    def _result(self, idx):
        text, s, e = self._chunks()[idx]
        return {"chunk_id": idx, "file_id": 1, "text": text, "start_offset": s, "end_offset": e}

    @pytest.mark.asyncio
    async def test_window_is_wider_than_the_child_and_contains_it(self, monkeypatch):
        monkeypatch.setattr(retrieval.settings, "parent_window_enabled", True)
        monkeypatch.setattr(retrieval.settings, "chunk_size", self.CHUNK)
        monkeypatch.setattr(retrieval.settings, "parent_window_multiplier", 3)

        results = [self._result(3)]
        await retrieval.attach_parent_windows(self._db(), results)

        parent = results[0]["parent_text"]
        child_body = results[0]["text"][len(self.PREFIX) :]
        assert len(parent) > len(child_body)
        assert child_body.strip() in parent, "the matched passage must survive expansion"

    @pytest.mark.asyncio
    async def test_overlap_is_not_duplicated(self, monkeypatch):
        """The whole reason stitching is offset-aware.

        Chunks overlap by `chunk_overlap`, so concatenating them whole repeats
        that text - burning context budget on duplication, which is exactly what
        _deduplicate_redundant exists to prevent.
        """
        monkeypatch.setattr(retrieval.settings, "parent_window_enabled", True)
        monkeypatch.setattr(retrieval.settings, "chunk_size", self.CHUNK)
        monkeypatch.setattr(retrieval.settings, "parent_window_multiplier", 3)

        results = [self._result(3)]
        await retrieval.attach_parent_windows(self._db(), results)
        parent = results[0]["parent_text"]

        # Reconstructed text must be a contiguous slice of the original source,
        # which is only true if every overlap was removed exactly.
        assert parent in self.SOURCE, "stitched window is not a slice of the source"
        assert self.PREFIX not in parent, "chunk prefix leaked into the window"

    @pytest.mark.asyncio
    async def test_disabled_setting_leaves_results_untouched(self, monkeypatch):
        monkeypatch.setattr(retrieval.settings, "parent_window_enabled", False)
        results = [self._result(3)]
        await retrieval.attach_parent_windows(self._db(), results)
        assert "parent_text" not in results[0]

    @pytest.mark.asyncio
    async def test_child_text_is_preserved_for_citation(self, monkeypatch):
        """`text` is what the citation UI shows and what scoring ranked. Widening
        it in place would make the source panel claim a passage that did not
        match."""
        monkeypatch.setattr(retrieval.settings, "parent_window_enabled", True)
        monkeypatch.setattr(retrieval.settings, "chunk_size", self.CHUNK)
        monkeypatch.setattr(retrieval.settings, "parent_window_multiplier", 3)

        results = [self._result(3)]
        before = results[0]["text"]
        await retrieval.attach_parent_windows(self._db(), results)
        assert results[0]["text"] == before


class TestRerankerRrfFusion:
    """The cross-encoder's ordering is fused with the incoming one, not swapped in.

    It replaced RRF outright for a long time. That is net-positive on ranking -
    measured over tests/eval/corpus_large it promotes the answer-bearing chunk on
    6 of 8 queries, once from rank 25 to 2 - but a rare demotion across the k
    boundary costs that query its whole answer, and coverage is a threshold
    metric so it only ever sees the tail. Fusing recovered answer coverage
    0.509 -> 0.634 at chunk_size=512 with document nDCG unchanged (three builds
    per arm). See settings.reranker_rrf_fusion_weight.
    """

    @staticmethod
    def _assets(scores):
        session = MagicMock()
        session.run = MagicMock(return_value=[np.array([[s] for s in scores], dtype=np.float32)])
        enc = MagicMock()
        enc.ids, enc.attention_mask, enc.type_ids = [1, 2, 3], [1, 1, 1], [0, 0, 0]
        tok = MagicMock()
        tok.encode_batch = MagicMock(side_effect=lambda pairs: [enc] * len(pairs))
        return session, tok

    async def _run(self, monkeypatch, weight, scores, top_k):
        monkeypatch.setattr("app.search.reranker._get_model_assets", lambda: self._assets(scores))
        monkeypatch.setattr(reranker_mod.settings, "reranker_rrf_fusion_weight", weight)
        cands = [{"text": f"chunk {i}", "chunk_id": i} for i in range(len(scores))]
        out = await rerank("q", cands, top_k=top_k, text_key="text")
        return [c["chunk_id"] for c in out]

    @pytest.mark.asyncio
    async def test_weight_zero_reproduces_pure_cross_encoder_order(self, monkeypatch):
        """The old behaviour has to stay reachable, or the change is unfalsifiable."""
        scores = [-5.0] + [1.0] * 10 + [9.0]
        got = await self._run(monkeypatch, 0.0, scores, top_k=3)
        assert got[0] == 11, "highest cross-encoder score must lead at weight 0"

    @pytest.mark.asyncio
    async def test_fusion_protects_a_chunk_the_cross_encoder_demotes(self, monkeypatch):
        """The whole point: an RRF-leading chunk the model dislikes must survive.

        Chunk 0 arrives first (best RRF rank) and is scored worst by the model.
        Under pure cross-encoder ordering it falls to last; fusion has to keep it
        inside a modest window.
        """
        scores = [-5.0] + [1.0] * 10 + [9.0]
        pure = await self._run(monkeypatch, 0.0, scores, top_k=12)
        fused = await self._run(monkeypatch, 0.5, scores, top_k=12)
        assert pure.index(0) > fused.index(0), (
            f"fusion did not rescue the RRF-leading chunk: pure={pure} fused={fused}"
        )

    @pytest.mark.asyncio
    async def test_fusion_still_promotes_what_the_model_likes(self, monkeypatch):
        """Fusion must not become 'ignore the cross-encoder'.

        Chunk 11 arrives last on RRF and is scored best; it should still climb.
        """
        scores = [1.0] * 11 + [9.0]
        fused = await self._run(monkeypatch, 0.5, scores, top_k=12)
        assert fused.index(11) < 11, f"model's favourite did not climb: {fused}"

    @pytest.mark.asyncio
    async def test_every_returned_item_still_carries_a_rerank_score(self, monkeypatch):
        """Fusion happens before the top_k cut for this reason.

        `_apply_relevance_cutoff` (app/search/context_builder.py) branches on
        whether rerank_score is present on ALL results or only some, and the
        partial case is the "mixed scales" path that drops nothing. Fusing at the
        caller could surface an unscored item and silently disable the cutoff.
        """
        monkeypatch.setattr(
            "app.search.reranker._get_model_assets",
            lambda: self._assets([1.0, 2.0, 3.0, 4.0]),
        )
        monkeypatch.setattr(reranker_mod.settings, "reranker_rrf_fusion_weight", 0.5)
        cands = [{"text": f"c{i}", "chunk_id": i} for i in range(4)]
        out = await rerank("q", cands, top_k=2, text_key="text")
        assert all("rerank_score" in r for r in out)


class TestChunkPrefixConvention:
    """One text convention below storage. CLAUDE.md D4.

    `text_preview` is stored as `[EXT: name] ` + body. That prefix used to reach
    the cross-encoder and the citation panel while `attach_parent_windows`
    stripped it from the delivered window - so the text that got RANKED and the
    text that got READ differed, per chunk, decided by a length comparison that
    the prefix itself biased.
    """

    PREFIX = "[MD: notes.md] "
    PATH = "/corpus/notes.md"

    def _row(self, body, start=0):
        """A chunks row in the shape _build_candidate_results consumes."""
        preview = self.PREFIX + body
        return (
            7,
            preview,
            self.PATH,
            "docs",
            1700000000,
            start,
            start + len(body),
            "[]",
            "py_v1",
            3,
        )

    def test_candidate_text_carries_no_prefix(self):
        body = "the quick brown fox jumps over the lazy dog, repeatedly and at length."
        out = retrieval._build_candidate_results([7], {7: self._row(body)}, {7: 1.0})
        assert out[0]["text"] == body
        assert self.PREFIX not in out[0]["text"]
        # The panel still names the file; the tag in the body was a duplicate.
        assert out[0]["file_path"] == self.PATH

    def test_length_floor_still_measures_the_stored_preview(self):
        """The 50-char floor must keep reading `text_preview`, not the body.

        Moving it onto the stripped body would silently change WHICH chunks are
        dropped, which would confound every retrieval measurement this change is
        supposed to leave alone.
        """
        body = "x" * 40  # 40 < 50, but 40 + len(PREFIX) == 55 >= 50
        assert len(self.PREFIX + body) >= retrieval._MIN_CANDIDATE_CHARS
        out = retrieval._build_candidate_results([7], {7: self._row(body)}, {7: 1.0})
        assert len(out) == 1, "a chunk kept before this change must still be kept"
        assert out[0]["text"] == body

    def test_exact_strip_beats_length_arithmetic_when_they_disagree(self):
        """CodeChunker bodies are line-joined, so `end - start` can be off by the
        trailing newline. The path-based strip must win; the arithmetic must not
        be allowed to eat a real character."""
        body = "def f():" + chr(10) + "    return 1"
        preview = self.PREFIX + body
        # span claims one MORE char than the body holds - arithmetic alone would
        # strip len(PREFIX) - 1 and leave a stray "]", or worse.
        assert retrieval._chunk_body(preview, 0, len(body) + 1, self.PATH) == body
        # ...and with no path, the documented fallback still applies unchanged.
        assert retrieval._chunk_body(preview, 0, len(body)) == body

    def test_chunk_body_is_identity_when_the_prefix_is_absent(self):
        assert retrieval._chunk_body("no tag here", file_path=self.PATH) == "no tag here"

    @pytest.mark.asyncio
    async def test_graph_leg_results_carry_no_prefix(self, monkeypatch):
        """The graph leg builds its own result dicts from its own SELECT, which
        returns f.path and no offsets - a separately revertable call site."""
        body = "cache lookups are memoised per process."

        async def _seeds(*a, **k):
            return [{"chunk_id": 7, "score": 2.0}]

        monkeypatch.setattr(retrieval, "hybrid_retrieve", _seeds)

        class FakeDB:
            async def bfs_from_chunks(self, ids, max_depth, limit):
                return [8]

            async def get_relational_paths(self, ids, max_depth, limit):
                return ["a -> b"]

            async def execute_query(self, sql, params):
                return [
                    (
                        8,
                        TestChunkPrefixConvention.PREFIX + body,
                        "/corpus/notes.md",
                        "docs",
                        1700000000,
                        3,
                    )
                ]

        plan = MagicMock()
        plan.original_query = "connection between a and b"
        results, _ctx = await retrieval._execute_graph_plan(
            plan, FakeDB(), MagicMock(), MagicMock(), k=5
        )
        assert results is not None
        assert results[0]["text"] == body
