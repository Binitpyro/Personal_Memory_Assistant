"""
tests/test_chunker.py
Tests for Rust Chunking Offload, CJK Character Offsets, Sentence Offsets Gating,
and StreamChunker Infinite Loop Guard.
"""

import logging
import time
from unittest.mock import MagicMock

import rust_core

import app.indexing.service as service
from app.indexing.service import IndexingService, StreamChunker, _get_sentence_offsets


def test_rust_cjk_offsets():
    """
    Assert that the start_offset and end_offset returned from Rust's create_chunks
    exactly map to Python's character slices (text[start:end]).
    """
    text = (
        "你好，世界！这是一个测试。我们需要确保中文字符的偏移量在 Rust 和 Python 之间是完全一致的。"  # noqa: RUF001
    )
    chunk_size = 10
    chunk_overlap = 2
    prefix = "[test] "

    chunks = rust_core.create_chunks(text, chunk_size, chunk_overlap, prefix, 0)
    assert len(chunks) > 0

    for chunk in chunks:
        start = chunk["start_offset"]
        end = chunk["end_offset"]
        text_preview = chunk["text_preview"]

        # Verify that Python character slice text[start:end] matches text_preview
        expected_chunk_text = text[start:end]
        actual_chunk_text = text_preview[len(prefix) :]
        assert actual_chunk_text == expected_chunk_text


def test_rust_markdown_cjk_offsets():
    """
    Assert that chunk_markdown also respects correct character offsets with CJK text.
    """
    text = "# 你好\n世界！\n## 这是一个测试\n我们需要确保。"  # noqa: RUF001
    prefix = "[test] "
    chunks = rust_core.chunk_markdown(text, 10, 2, prefix)
    assert len(chunks) > 0
    for chunk in chunks:
        start = chunk["start_offset"]
        end = chunk["end_offset"]
        text_preview = chunk["text_preview"]

        expected_chunk_text = text[start:end].strip()
        actual_chunk_text = text_preview[len(prefix) :].strip()
        assert actual_chunk_text == expected_chunk_text


def test_stream_chunker_pathological_input_no_hang():
    """
    Verify that a 100KB pathological input with no delimiters is processed
    quickly and does not hang.
    """
    pathological_input = "a" * 100000
    chunker = StreamChunker(chunk_size=1000, chunk_overlap=100, prefix="")

    start_time = time.time()
    chunks = chunker.process(pathological_input)
    chunks.extend(chunker.finalize())
    duration = time.time() - start_time

    assert duration < 1.0
    assert len(chunks) > 0


def test_stream_chunker_loop_guard_trigger(caplog):
    """
    Force trigger the infinite loop guard using a custom pathological string subclass
    and verify that it logs the guard error and terminates without hanging.
    """

    class PathologicalString(str):
        def __len__(self):
            return 100

        def __getitem__(self, item):
            return PathologicalString("a" * 100)

        def __add__(self, other):
            return PathologicalString("a" * 100)

    chunker = StreamChunker(chunk_size=10, chunk_overlap=2, prefix="")
    chunker.buffer = PathologicalString("a" * 100)

    with caplog.at_level(logging.ERROR):
        _ = chunker.process("")

    assert any("Infinite loop guard triggered" in record.message for record in caplog.records)


def test_sentence_offsets_gating(monkeypatch):
    """
    Verify that PMA_SENTENCE_OFFSETS environment variable gates the sentence offsets behavior.
    """
    text = "Hello world. This is a test. Another sentence."

    # 1. With PMA_SENTENCE_OFFSETS="1" (default/enabled)
    monkeypatch.setenv("PMA_SENTENCE_OFFSETS", "1")
    offsets_enabled = _get_sentence_offsets(text)
    assert len(offsets_enabled) > 0

    # 2. With PMA_SENTENCE_OFFSETS="0" (disabled)
    monkeypatch.setenv("PMA_SENTENCE_OFFSETS", "0")
    offsets_disabled = _get_sentence_offsets(text)
    assert offsets_disabled == []

    # Test Rust gating directly
    monkeypatch.setenv("PMA_SENTENCE_OFFSETS", "1")
    rust_enabled = rust_core.create_chunks(text, 100, 10, "", 0)
    assert len(rust_enabled) > 0
    assert rust_enabled[0]["sentence_offsets"] != "[]"

    monkeypatch.setenv("PMA_SENTENCE_OFFSETS", "0")
    rust_disabled = rust_core.create_chunks(text, 100, 10, "", 0)
    assert len(rust_disabled) > 0
    assert rust_disabled[0]["sentence_offsets"] == "[]"


def test_create_chunks_routing(monkeypatch):
    """
    Test routing of _create_chunks to either Rust functions or Python fallback chunker.
    """
    called = {}

    def mock_chunk_markdown(text, size, overlap, prefix):
        called["markdown"] = True
        return [{"text_preview": "md_chunk"}]

    def mock_create_chunks(text, size, overlap, prefix, base_offset):
        called["txt"] = True
        return [{"text_preview": "txt_chunk"}]

    if service.RUST_CORE_AVAILABLE:
        monkeypatch.setattr(service.rust_core, "chunk_markdown", mock_chunk_markdown)
        monkeypatch.setattr(service.rust_core, "create_chunks", mock_create_chunks)

    db = MagicMock()
    emb = MagicMock()
    ldb = MagicMock()
    idx_service = IndexingService(db, emb, ldb)

    # 1. Test markdown routing
    called.clear()
    idx_service._create_chunks("some text", "test.md")
    if service.RUST_CORE_AVAILABLE:
        assert called.get("txt") is True
        assert not called.get("markdown")

    # 2. Test plain text routing
    called.clear()
    idx_service._create_chunks("some text", "test.txt")
    if service.RUST_CORE_AVAILABLE:
        assert called.get("txt") is True
        assert not called.get("markdown")

    # 3. Test code routing (should go to code_chunker, not rust_core)
    called.clear()
    monkeypatch.setattr(
        idx_service.code_chunker,
        "chunk_code",
        lambda *args, **kwargs: [{"text_preview": "py_chunk"}],
    )
    idx_service._create_chunks("def hello(): pass", "test.py")
    assert not called.get("markdown")
    assert not called.get("txt")
