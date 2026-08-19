"""
tests/test_embeddings_service.py
Coverage for app/embeddings/service.py — EmbeddingService with mocked model.
Tests: LRU cache, is_ready, load_model_background, embed_query, embed_texts.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.embeddings.service import EmbeddingService


class TestEmbeddingServiceInit:
    def test_defaults(self):
        svc = EmbeddingService(model_name="test-model")
        assert svc.model_name == "test-model"
        assert svc._session is None
        assert not svc.is_ready

    def test_is_ready_false_before_load(self):
        svc = EmbeddingService(model_name="test")
        assert not svc.is_ready


class TestIsReady:
    def test_is_ready_after_event_set(self):
        svc = EmbeddingService("model")
        svc._session = MagicMock()
        svc._ready.set()
        assert svc.is_ready

    def test_wait_until_ready_timeout(self):
        svc = EmbeddingService("model")
        result = svc.wait_until_ready(timeout=0.01)
        assert result is False

    def test_wait_until_ready_success(self):
        svc = EmbeddingService("model")
        svc._ready.set()
        result = svc.wait_until_ready(timeout=1.0)
        assert result is True


class TestLoadModelBackground:
    def test_starts_thread(self):
        svc = EmbeddingService("model")
        with patch.object(svc, "load_model") as _mock_load:
            svc.load_model_background()
            # Allow thread to start
            import time

            time.sleep(0.05)
        # Should have been called on background thread

    def test_no_double_load(self):
        svc = EmbeddingService("model")
        svc._loading = True
        with patch.object(svc, "load_model") as _mock_load:
            svc.load_model_background()
        _mock_load.assert_not_called()

    def test_no_load_if_model_exists(self):
        svc = EmbeddingService("model")
        svc._session = MagicMock()
        with patch.object(svc, "load_model") as _mock_load:
            svc.load_model_background()
        _mock_load.assert_not_called()


class TestEmbedQuery:
    @pytest.mark.asyncio
    async def test_embed_query_uses_cache(self):
        svc = EmbeddingService("model")
        mock_emb = [0.1] * 384
        svc._query_cache["hello"] = mock_emb
        result = await svc.embed_query("hello")
        # Should come from cache
        assert result == mock_emb

    @pytest.mark.asyncio
    async def test_embed_query_populates_cache(self):
        svc = EmbeddingService("model")
        mock_emb = [0.2] * 384
        with patch.object(svc, "embed_texts", AsyncMock(return_value=[mock_emb])):
            result = await svc.embed_query("new query")
        # Stored as float32 - the model already emits float32, so this only
        # narrows a float64 test fixture, and it is what makes the cache 8x
        # smaller than a list of boxed Python floats.
        assert result == pytest.approx(mock_emb, rel=1e-6)
        assert "new query" in svc._query_cache
        assert svc._query_cache["new query"].dtype == np.float32

    @pytest.mark.asyncio
    async def test_embed_query_lru_eviction(self):
        svc = EmbeddingService("model")
        svc._max_cache_size = 2
        svc._query_cache["a"] = [0.1] * 384
        svc._query_cache["b"] = [0.2] * 384
        mock_emb = [0.3] * 384
        with patch.object(svc, "embed_texts", AsyncMock(return_value=[mock_emb])):
            await svc.embed_query("c")  # Should evict "a"
        assert len(svc._query_cache) <= 2


class TestEmbedTexts:
    @pytest.mark.asyncio
    async def test_raises_if_model_unavailable(self):
        svc = EmbeddingService("model")
        # Model stays None, ready event is set (simulate failed load)
        svc._ready.set()
        with pytest.raises(RuntimeError, match="Embedding model"):
            await svc.embed_texts(["test"])

    @pytest.mark.asyncio
    async def test_deduplication(self):
        svc = EmbeddingService("model")
        import numpy as np

        mock_session = MagicMock()
        mock_session.run = MagicMock(return_value=[np.array([[0.1] * 384])])
        svc._session = mock_session

        mock_tokenizer = MagicMock()
        mock_encoded = MagicMock()
        mock_encoded.ids = [1, 2, 3]
        mock_encoded.attention_mask = [1, 1, 1]
        mock_encoded.type_ids = [0, 0, 0]
        mock_tokenizer.encode_batch = MagicMock(return_value=[mock_encoded])
        svc._tokenizer = mock_tokenizer

        with patch("app.indexing.service.progress") as mock_progress:
            mock_progress.set_current_file = MagicMock()
            result = await svc.embed_texts(["hello", "hello"])  # Two identical

        # _session.run should be called once since there's 1 unique text
        assert mock_session.run.call_count == 1
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_custom_batch_size(self):
        svc = EmbeddingService("model")
        import numpy as np

        mock_session = MagicMock()
        mock_session.run = MagicMock(return_value=[np.array([[0.1] * 384])])
        svc._session = mock_session

        mock_tokenizer = MagicMock()
        mock_encoded = MagicMock()
        mock_encoded.ids = [1]
        mock_encoded.attention_mask = [1]
        mock_encoded.type_ids = [0]
        mock_tokenizer.encode_batch = MagicMock(return_value=[mock_encoded])
        svc._tokenizer = mock_tokenizer

        with patch("app.indexing.service.progress") as mock_progress:
            mock_progress.set_current_file = MagicMock()
            result = await svc.embed_texts(["text1"], batch_size=1)
        assert len(result) == 1


class TestColdStart:
    def test_cold_start_success_assertions(self, tmp_path):
        from pathlib import Path

        svc = EmbeddingService("test-model")
        mock_sess = MagicMock()
        mock_tok = MagicMock()

        local_dir = tmp_path / "model_dir"
        local_dir.mkdir()
        (local_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        (local_dir / "onnx").mkdir()
        (local_dir / "onnx" / "model_quantized.onnx").write_text("fake", encoding="utf-8")

        with patch.object(svc, "_load_onnx_model") as mock_load:

            def _fake_load(p, exp, *args, **kwargs):
                svc._session = mock_sess
                svc._tokenizer = mock_tok

            mock_load.side_effect = _fake_load
            with patch.object(Path, "expanduser", return_value=local_dir):
                svc.load_model()

        assert svc.is_ready is True
        assert svc.has_failed is False
        assert svc.load_error is None
        assert svc._session is not None

    def test_cold_start_failure_assertions(self):
        svc = EmbeddingService("test-model")
        with patch(
            "app.embeddings.service._get_models_lock_data",
            side_effect=ValueError("Corrupt lockfile"),
        ):
            svc.load_model()
        assert svc.is_ready is False
        assert svc.has_failed is True
        assert svc.wait_until_ready(timeout=0) is True
        assert svc._session is None
        assert svc.load_error is not None
        assert "Corrupt lockfile" in svc.load_error


class TestIntegrityFailClosed:
    def test_missing_sha256_digest_raises_valueerror(self, tmp_path):
        svc = EmbeddingService("test-model")
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        (model_dir / "onnx").mkdir()
        (model_dir / "onnx" / "model.onnx").write_text("fake_onnx", encoding="utf-8")

        expected_files = {
            "tokenizer.json": {"sha256": "fake_hash"},
            "onnx/model.onnx": {"sha256": ""},  # Empty digest -> must fail closed
        }

        with (
            patch("tokenizers.Tokenizer.from_file", return_value=MagicMock()),
            patch.object(svc, "_verify_onnx_checksum", return_value=True),
            pytest.raises(ValueError, match="missing sha256 digest"),
        ):
            svc._load_onnx_model(model_dir, expected_files)

    def test_unpinned_file_raises_valueerror(self, tmp_path):
        svc = EmbeddingService("test-model")
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        (model_dir / "onnx").mkdir()
        (model_dir / "onnx" / "model.onnx").write_text("fake_onnx", encoding="utf-8")

        expected_files = {
            "tokenizer.json": {"sha256": "fake_hash"},
            "onnx/different_model.onnx": {"sha256": "abc"},  # model.onnx not pinned
        }

        with (
            patch("tokenizers.Tokenizer.from_file", return_value=MagicMock()),
            patch.object(svc, "_verify_onnx_checksum", return_value=True),
            patch("app.config.settings.embedding_allow_unpinned", False),
            pytest.raises(ValueError, match=r"not pinned in models\.lock\.json"),
        ):
            svc._load_onnx_model(model_dir, expected_files)


class TestRepoIdOverrideAndExceptions:
    def test_repo_id_override_passed_to_snapshot_download(self, tmp_path):
        svc = EmbeddingService("BAAI/bge-small-en-v1.5")
        mock_lock_data = {
            "BAAI/bge-small-en-v1.5": {
                "repo_id": "Xenova/bge-small-en-v1.5",
                "revision": "ea104dacec62c0de699686887e3f920caeb4f3e3",
                "files": {},
            }
        }
        with (
            patch("app.embeddings.service._get_models_lock_data", return_value=mock_lock_data),
            patch("huggingface_hub.snapshot_download", return_value=str(tmp_path)) as mock_download,
            patch.object(svc, "_load_onnx_model"),
        ):
            svc.load_model()
            assert mock_download.call_count >= 1
            kwargs = mock_download.call_args[1]
            assert kwargs["repo_id"] == "Xenova/bge-small-en-v1.5"
            assert kwargs["revision"] == "ea104dacec62c0de699686887e3f920caeb4f3e3"

    def test_revision_not_found_error_raises_runtime_error(self):
        from huggingface_hub.errors import LocalEntryNotFoundError, RevisionNotFoundError

        svc = EmbeddingService("BAAI/bge-small-en-v1.5")
        mock_lock_data = {
            "BAAI/bge-small-en-v1.5": {
                "repo_id": "Xenova/bge-small-en-v1.5",
                "revision": "invalid_rev",
                "files": {},
            }
        }

        def _side_effect(*args, **kwargs):
            if kwargs.get("local_files_only"):
                raise LocalEntryNotFoundError("Not in cache")
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_resp.headers = {}
            raise RevisionNotFoundError("Revision invalid_rev not found", response=mock_resp)

        with (
            patch("app.embeddings.service._get_models_lock_data", return_value=mock_lock_data),
            patch("huggingface_hub.snapshot_download", side_effect=_side_effect),
        ):
            svc.load_model()
            assert svc.has_failed is True
            assert "does not resolve on HuggingFace" in svc.load_error


class TestBatchSizeAndSessionOptions:
    """P0-4: previously zero coverage for either - intra_op_num_threads
    could have been set to 999, or embedding_batch_size read from the wrong
    field, and the suite would stay green."""

    def test_optimal_batch_size_matches_settings(self):
        from app.config import settings

        svc = EmbeddingService("test-model")
        assert svc.optimal_batch_size == settings.embedding_batch_size
        assert svc.optimal_batch_size == 64

    def test_session_options_configured_correctly(self, tmp_path):
        """Exercises the real _load_onnx_model SessionOptions block via a
        stub SessionOptions object (not a MagicMock) so unset attributes
        keep their initialized value instead of auto-vivifying - that's
        what lets this assert intra_op_num_threads was never touched,
        rather than just that *some* value was read off it."""
        svc = EmbeddingService("test-model")
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        (model_dir / "onnx").mkdir()
        (model_dir / "onnx" / "model.onnx").write_text("fake_onnx", encoding="utf-8")

        class _StubSessionOptions:
            def __init__(self):
                # ORT's real pybind11 defaults.
                self.intra_op_num_threads = 0
                self.inter_op_num_threads = 0
                self.enable_cpu_mem_arena = True
                self.enable_mem_pattern = True

        stub_options = _StubSessionOptions()
        mock_session = MagicMock()
        mock_session.get_inputs.return_value = []
        mock_session.run.return_value = [np.array([[0.1] * 384])]

        with (
            patch("tokenizers.Tokenizer.from_file", return_value=MagicMock()),
            patch("onnxruntime.SessionOptions", return_value=stub_options),
            patch("onnxruntime.InferenceSession", return_value=mock_session),
        ):
            svc._load_onnx_model(model_dir, {})

        # Left at ORT's default (0), which resolves to physical core count
        # WITH thread affinitization - min(4, cpu_count-1) discarded that.
        assert stub_options.intra_op_num_threads == 0
        assert stub_options.inter_op_num_threads == 2
        # Explicitly disabled - measured 22x peak-RSS reduction (3848 MB ->
        # 172 MB) on a variable-length corpus, for a 9% throughput cost.
        assert stub_options.enable_cpu_mem_arena is False
        # Left on - disabling it too gave no further memory benefit but
        # roughly halved throughput.
        assert stub_options.enable_mem_pattern is True

    def test_session_run_call_count_matches_batch_count(self):
        """encode_batch's mock must scale with input size - the existing
        cross-repo convention (test_deduplication, test_custom_batch_size)
        always returns a 1-element list regardless of batch size, which
        makes batching itself untestable."""
        svc = EmbeddingService("model")
        svc._session = MagicMock()
        svc._session.run = MagicMock(return_value=[np.array([[0.1] * 384])])

        def _encode_batch(batch, **kwargs):
            encodings = []
            for _ in batch:
                enc = MagicMock()
                enc.ids = [1, 2, 3]
                enc.attention_mask = [1, 1, 1]
                enc.type_ids = [0, 0, 0]
                encodings.append(enc)
            return encodings

        svc._tokenizer = MagicMock()
        svc._tokenizer.encode_batch = MagicMock(side_effect=_encode_batch)

        import math

        for n_texts, batch_size in [(10, 4), (64, 64), (65, 64), (128, 64)]:
            svc._session.run.reset_mock()
            texts = [f"unique text {i}" for i in range(n_texts)]
            asyncio.run(svc.embed_texts(texts, batch_size=batch_size))
            assert svc._session.run.call_count == math.ceil(n_texts / batch_size)


class TestLengthSortedBatching:
    """Grouping similar-length texts collapses tokenizer padding waste.

    The correctness property is that every embedding still lands on its own
    text: batches are no longer contiguous slices, so a missing scatter-back
    would silently pair every chunk with the wrong vector.
    """

    def test_batches_partition_every_index_exactly_once(self):
        from app.embeddings.service import _length_sorted_batches

        texts = ["a" * 100, "b" * 5, "c" * 50, "d", "e" * 200]
        batches = _length_sorted_batches(texts, 2)

        flat = [i for group in batches for i in group]
        assert sorted(flat) == list(range(len(texts)))
        assert len(flat) == len(set(flat)), "an index appeared in two batches"
        assert all(len(g) <= 2 for g in batches)

    def test_batches_are_ordered_by_length(self):
        from app.embeddings.service import _length_sorted_batches

        texts = ["a" * 100, "b" * 5, "c" * 50, "d", "e" * 200]
        order = [i for group in _length_sorted_batches(texts, 1) for i in group]

        lengths = [len(texts[i]) for i in order]
        assert lengths == sorted(lengths)

    def test_degenerate_batch_size_does_not_hang(self):
        from app.embeddings.service import _length_sorted_batches

        assert _length_sorted_batches(["a", "b"], 0) == [[0], [1]]
        assert _length_sorted_batches([], 4) == []

    @pytest.mark.asyncio
    async def test_embeddings_stay_matched_to_their_own_text(self):
        """Nearest-neighbour identity, not float equality.

        Padding shifts values slightly - a text's padding depends on which
        batch it lands in, which was true before this change too. What must
        hold is that each returned row is still closest to its own text.
        """
        from app.embeddings.service import EmbeddingService

        svc = EmbeddingService()
        svc.load_model()

        # Deliberately length-varied so sorting reorders them, plus a duplicate.
        texts = [
            "short",
            "x " * 400,
            "a medium length sentence about caching",
            "short",
            "y " * 200,
            "tiny",
        ]

        batched = await svc.embed_texts(texts, batch_size=2)
        assert batched.shape == (len(texts), 384)

        singles = np.vstack([(await svc.embed_texts([t], batch_size=1))[0] for t in texts])

        # Row i of the batched result must be nearest to single-embedded text i.
        # Compared by text content, not index: "short" appears twice and its two
        # rows are identical, so argmax legitimately returns the first of them.
        similarity = batched @ singles.T
        nearest = [texts[j] for j in np.argmax(similarity, axis=1)]
        assert nearest == texts, "an embedding was scattered back to the wrong text"

        # Deduplication must still collapse identical inputs.
        assert np.allclose(batched[0], batched[3])

    def test_sync_path_stays_matched_to_its_own_text(self):
        from app.embeddings.service import EmbeddingService

        svc = EmbeddingService()
        svc.load_model()

        texts = ["short", "z " * 300, "another sentence entirely", "tiny"]
        batched = svc.embed_texts_sync(texts, batch_size=2)
        singles = np.vstack([svc.embed_texts_sync([t], batch_size=1)[0] for t in texts])

        similarity = batched @ singles.T
        assert list(np.argmax(similarity, axis=1)) == list(range(len(texts)))


class TestBatchCharBudget:
    """`char_budget` caps rows x width-of-widest-row, not rows alone.

    A row cap does not bound peak embedding memory: the tokenizer pads each
    batch to its longest member, so cost tracks `rows * width` - measured at
    0.140 MB per (row x token) on bge-small. That is how a 403 MB PDF corpus of
    full-width chunks reached 1307 MB above idle while a 1.3 GB fixture of small
    files stayed at 426 MB. See CLAUDE.md section 6.

    The partitioning property is the one that must not break: callers scatter
    results back by position, so a dropped or duplicated index silently pairs a
    chunk with the wrong vector.
    """

    def test_wide_texts_are_narrowed_to_respect_the_budget(self):
        from app.embeddings.service import _length_sorted_batches

        texts = ["x" * 500] * 64
        batches = _length_sorted_batches(texts, 64, char_budget=2000)

        assert all(len(g) * 500 <= 2000 for g in batches), "a batch exceeded the budget"
        assert max(len(g) for g in batches) == 4
        flat = [i for g in batches for i in g]
        assert sorted(flat) == list(range(64))

    def test_texts_well_under_the_budget_batch_exactly_as_before(self):
        """Only asserts the far end of the range, and deliberately so.

        An earlier version of this test used 10-char texts and was read as
        "short corpora are unaffected". That is false at realistic widths: a
        full chunk is ~560 chars including the context prefix, so the default
        10240 budget caps *every* corpus at ~18 rows, not just PDF-heavy ones.
        Measured consequence is a memory drop on both corpora and a ~16%
        throughput *gain* (171.3 vs 148.1 texts/s), so the narrowing is wanted -
        but do not read this test as evidence that nothing changes.
        """
        from app.embeddings.service import _length_sorted_batches

        texts = ["x" * 10] * 64
        with_budget = _length_sorted_batches(texts, 64, char_budget=10240)
        without = _length_sorted_batches(texts, 64, char_budget=0)

        assert with_budget == without
        assert len(with_budget) == 1

    def test_realistic_chunk_width_is_narrowed(self):
        """The case that actually occurs: a full chunk plus its context prefix."""
        from app.embeddings.service import _length_sorted_batches

        texts = ["[PDF: lecture.pdf] " + "w" * 512] * 64
        batches = _length_sorted_batches(texts, 64, char_budget=10240)

        widest = max(len(g) for g in batches)
        assert widest == 19, f"expected ~19 rows at 531 chars, got {widest}"
        assert all(len(g) * 531 <= 10240 for g in batches)

    def test_budget_partitions_every_index_exactly_once(self):
        from app.embeddings.service import _length_sorted_batches

        texts = [("z" * (i * 7 + 1)) for i in range(50)]
        batches = _length_sorted_batches(texts, 8, char_budget=64)

        flat = [i for g in batches for i in g]
        assert sorted(flat) == list(range(50))
        assert len(flat) == len(set(flat))
        assert all(g for g in batches), "emitted an empty batch"

    def test_single_text_wider_than_the_budget_still_ships(self):
        """A chunk larger than the whole budget must not produce an empty batch
        or an infinite loop - it goes alone and costs what it costs."""
        from app.embeddings.service import _length_sorted_batches

        texts = ["x" * 5000, "y" * 3]
        batches = _length_sorted_batches(texts, 64, char_budget=100)

        assert sorted(i for g in batches for i in g) == [0, 1]
        assert all(len(g) >= 1 for g in batches)

    def test_zero_budget_restores_fixed_size_batching(self):
        from app.embeddings.service import _length_sorted_batches

        texts = [("q" * (i + 1)) for i in range(20)]
        assert _length_sorted_batches(texts, 6, char_budget=0) == _length_sorted_batches(texts, 6)
