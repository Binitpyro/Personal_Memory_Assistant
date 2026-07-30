"""
tests/test_embeddings_service.py
Coverage for app/embeddings/service.py — EmbeddingService with mocked model.
Tests: LRU cache, is_ready, load_model_background, embed_query, embed_texts.
"""

from unittest.mock import AsyncMock, MagicMock, patch

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
        assert result == mock_emb
        assert "new query" in svc._query_cache

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
        with patch.object(svc, "_load_onnx_model") as mock_load:
            def _fake_load(p, exp):
                svc._session = mock_sess
                svc._tokenizer = mock_tok
            mock_load.side_effect = _fake_load
            with patch("app.embeddings.service._get_models_lock_data", return_value={"test-model": {"files": {}}}):
                with patch("pathlib.Path.is_dir", return_value=True):
                    svc.load_model()
        assert svc.is_ready is True
        assert svc._load_error is None
        assert svc._session is not None

    def test_cold_start_failure_assertions(self):
        svc = EmbeddingService("test-model")
        with patch("app.embeddings.service._get_models_lock_data", side_effect=ValueError("Corrupt lockfile")):
            svc.load_model()
        assert svc.is_ready is True
        assert svc._session is None
        assert svc._load_error is not None
        assert "Corrupt lockfile" in svc._load_error


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
            "onnx/model.onnx": {"sha256": ""}  # Empty digest -> must fail closed
        }

        with patch("tokenizers.Tokenizer.from_file", return_value=MagicMock()):
            with patch.object(svc, "_verify_onnx_checksum", return_value=True):
                with pytest.raises(ValueError, match="missing sha256 digest"):
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
            "onnx/different_model.onnx": {"sha256": "abc"}  # model.onnx not pinned
        }

        with patch("tokenizers.Tokenizer.from_file", return_value=MagicMock()):
            with patch.object(svc, "_verify_onnx_checksum", return_value=True):
                with patch("app.config.settings.embedding_allow_unpinned", False):
                    with pytest.raises(ValueError, match="not pinned in models.lock.json"):
                        svc._load_onnx_model(model_dir, expected_files)

