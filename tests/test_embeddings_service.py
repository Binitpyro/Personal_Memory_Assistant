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
