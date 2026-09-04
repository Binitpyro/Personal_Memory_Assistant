"""
tests/test_chunker.py
Tests for Rust Chunking Offload, CJK Character Offsets, Sentence Offsets Gating,
and StreamChunker Infinite Loop Guard.
"""

import logging
import time
from unittest.mock import MagicMock

import pytest
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

    # 1. Markdown routes to the SECTION-aware chunker.
    #
    # This assertion used to be the other way round - .md was expected to reach
    # create_chunks, the generic sliding window - which locked in the defect
    # CLAUDE.md 8.7 A3 describes: chunk_markdown had no caller anywhere, and the
    # test said that was correct.
    called.clear()
    idx_service._create_chunks("some text", "test.md")
    if service.RUST_CORE_AVAILABLE:
        assert called.get("markdown") is True
        assert not called.get("txt")

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


def test_chunk_markdown_merges_small_sections_up_to_the_budget():
    """chunk_size must act as a floor as well as a ceiling.

    The first version applied it only as a maximum, so a section shorter than it
    became a chunk of that length however short it was - chunk size followed
    heading density rather than any budget. Measured consequence on a
    heading-dense corpus: 589 chunks became 1060 and every retrieval metric moved
    the wrong way (CLAUDE.md 8.7 A3).
    """
    if not service.RUST_CORE_AVAILABLE:
        pytest.skip("rust_core not built")

    # Twenty tiny sections. Un-merged that is twenty chunks of ~40 characters.
    text = "".join(f"### Heading {i}\nA short line of body text here.\n\n" for i in range(20))
    chunks = service.rust_core.chunk_markdown(text, 512, 51, "[MD: t.md] ")

    spans = [c["end_offset"] - c["start_offset"] for c in chunks]
    assert len(chunks) < 20, f"small sections were not merged: {len(chunks)} chunks for 20 sections"
    # Everything except the final remainder should be a substantial fraction of
    # the budget rather than one stray heading.
    assert max(spans) <= 512, "a merged run exceeded the budget"
    assert max(spans) > 200, f"merging produced nothing near the budget: spans={spans}"


def test_chunk_markdown_span_matches_the_text_it_stores():
    """`retrieval._chunk_body` recovers a chunk's prefix length as
    `len(text_preview) - (end_offset - start_offset)` when stitching parent
    windows. A span that disagrees with its own stored text silently mis-slices
    that window, so the trim has to be reflected in the offsets."""
    if not service.RUST_CORE_AVAILABLE:
        pytest.skip("rust_core not built")

    prefix = "[MD: t.md] "
    text = "# Title\n\nBody one.\n\n## Second\n\nBody two is a little longer.\n\n"
    for c in service.rust_core.chunk_markdown(text, 512, 51, prefix):
        span = c["end_offset"] - c["start_offset"]
        assert len(c["text_preview"]) - span == len(prefix), (
            f"span {span} disagrees with stored text {c['text_preview']!r}"
        )


class TestBoundaryLookback:
    """The sentence-snap window is a setting now, not a 100-character literal.

    Why it needed to move (measured 2026-09-04 on tests/eval/corpus_squad, 48
    real Wikipedia articles): `_find_boundary` returns `pos` unchanged when no
    sentence or paragraph end sits inside the window, which is a blind
    mid-sentence cut. At 100 characters that happened on ~40% of splits, and
    -- the part that made it invisible -- the rate is flat across chunk_size
    (39.8% at 512, 39.4% at 1024, 40.5% at 2048) because the window is absolute
    while a sentence is ~100 characters. Halving chunk_size does not improve the
    rate, it doubles the number of cuts, because it doubles how often you split.

    `tests/eval/corpus_large` cannot show any of this: templated prose with
    frequent blank lines cuts blind only 8.6% of the time.
    """

    @staticmethod
    def _prose(n: int) -> str:
        # Sentences longer than the old 100-char window, with no blank lines,
        # which is the case the literal could not handle.
        return " ".join(
            f"Sentence number {i} runs on at some length so that the terminator "
            f"lands well beyond a hundred characters from any arbitrary offset "
            f"chosen inside it by the chunker."
            for i in range(n)
        )

    def test_a_wider_window_reaches_a_stronger_rung(self):
        """The window decides WHICH rung of the ladder is reachable.

        This asserted `narrow == 1024` - a blind cut - until the word rung
        landed, and then failed, which is the ladder working: 20 characters
        cannot span one of these sentences but always contains a space. The
        claim worth pinning is not "narrow cuts blind", it is that a wider
        window buys a structurally stronger boundary.
        """
        text = self._prose(40)
        narrow = StreamChunker._find_boundary(text, 1024, 20)
        wide = StreamChunker._find_boundary(text, 1024, 256)

        assert text[narrow - 1] == " ", "narrow window should still land on a word break"
        assert text[narrow - 2 : narrow] != ". ", "20 chars cannot reach a sentence end here"
        assert text[wide - 2 : wide] == ". ", "256 chars should reach a sentence end"
        assert wide < narrow, "the stronger rung sits further back"

    def test_no_split_ever_lands_inside_a_word(self):
        """The bottom rung's whole job. 70 chunks on corpus_squad did this."""
        text = "x" * 300 + " " + "y" * 300  # no sentence or line break anywhere
        pos = 400
        b = StreamChunker._find_boundary(text, pos, 256)
        assert b < pos, "expected the space rung to fire"
        assert text[b - 1] == " "
        assert not (text[b - 1 : b].strip() and text[b : b + 1].strip())

    def test_the_setting_reaches_the_chunker(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "chunk_boundary_lookback_share", 0.25)
        assert StreamChunker(1024, 102, "").boundary_lookback == 256
        monkeypatch.setattr(settings, "chunk_boundary_lookback_share", 0.5)
        assert StreamChunker(1024, 102, "").boundary_lookback == 512

    def test_it_never_degenerates_to_zero(self, monkeypatch):
        """A zero lookback would make `region` empty and every split blind."""
        from app.config import settings

        monkeypatch.setattr(settings, "chunk_boundary_lookback_share", 0.0)
        assert StreamChunker(1024, 102, "").boundary_lookback == 1

    def test_wider_lookback_cuts_mid_sentence_endings(self, monkeypatch):
        """The end-to-end claim, on the real chunker rather than the helper."""
        from app.config import settings

        text = self._prose(400)

        def ends_mid_sentence(share: float) -> float:
            monkeypatch.setattr(settings, "chunk_boundary_lookback_share", share)
            ch = StreamChunker(1024, 102, "")
            chunks = ch.process(text) + ch.finalize()
            bad = sum(1 for c in chunks if not c["text_preview"].rstrip().endswith("."))
            return bad / len(chunks)

        narrow = ends_mid_sentence(100 / 1024)
        wide = ends_mid_sentence(0.25)
        # Bounds, not exact rates: the narrow figure tracks this fixture's
        # sentence length (measured 0.49 here) and a tight threshold would be
        # brittle without testing anything more. The claim is the GAP.
        assert wide < 0.05, f"expected 0.25 to snap nearly always, got {wide:.2f}"
        assert narrow > 0.3, f"expected the old constant to cut blind often, got {narrow:.2f}"
        assert narrow > wide * 5, f"narrow={narrow:.2f} wide={wide:.2f}"
