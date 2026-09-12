import logging
import time

from app.indexing.service import StreamChunker


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


def _chunk(text, fragsize, chunk_size=1024, overlap=102):
    ch = StreamChunker(chunk_size, overlap, prefix="ctx: ")
    out = []
    for i in range(0, len(text), fragsize):
        out.extend(ch.process(text[i : i + fragsize]))
    out.extend(ch.finalize())
    return out


_PROSE = ("The quick brown fox jumps over the lazy dog. " * 20) + "\n\n"


def test_fragmentation_does_not_change_the_chunks():
    """The read cursor must be invisible in the output.

    Chunking the same bytes as one whole-file fragment, as 128 KB reads, and as
    an awkward 997-byte dribble must produce identical chunks - offsets
    included. Offsets address the extracted text stream and are asserted
    elsewhere against the eval corpus, so a cursor bookkeeping slip here would
    corrupt every citation.
    """
    text = (_PROSE * 600)[: 512 * 1024]

    whole = _chunk(text, len(text))
    streamed = _chunk(text, 128 * 1024)
    dribbled = _chunk(text, 997)

    assert whole == streamed
    assert whole == dribbled
    assert len(whole) > 100, "fixture too small to exercise the loop"


def test_single_huge_fragment_is_not_quadratic():
    """`process` used to re-slice its buffer on every emitted chunk, so an
    extractor yielding a whole file as one fragment cost O(n^2) - 0.627s at
    2 MB, 3.234s at 4 MB, 12.071s at 8 MB, 46.440s at 16 MB, against 0.008s at
    4 MB once the read cursor landed.

    The assertion is RELATIVE - the same bytes delivered whole versus in 128 KB
    reads - rather than a wall-clock ceiling, so it calibrates itself to the
    machine instead of passing vacuously on a fast one. Both paths emit
    identical chunks, so the only thing being compared is the copying.
    """
    text = (_PROSE * 6000)[: 4 * 1024 * 1024]

    start = time.perf_counter()
    whole = _chunk(text, len(text))
    one_fragment = time.perf_counter() - start

    start = time.perf_counter()
    streamed = _chunk(text, 128 * 1024)
    in_pieces = time.perf_counter() - start

    assert whole == streamed, "fragmentation changed the output"
    assert len(whole) > 1000, "fixture too small to exercise the loop"

    # Measured ~58x on the re-slicing implementation and ~1x on the cursor.
    ratio = one_fragment / max(in_pieces, 1e-6)
    assert ratio < 10, (
        f"one whole-file fragment cost {ratio:.1f}x the same bytes in 128 KB pieces "
        f"({one_fragment:.3f}s vs {in_pieces:.3f}s) - the buffer copying is back"
    )


def test_stream_chunker_lying_len_terminates_without_the_loop_guard(caplog):
    """A str subclass whose `__len__` lies used to stall `process` until the
    infinite-loop guard fired and broke out.

    The read cursor made that structurally impossible: `_pos` advances by at
    least one character every iteration regardless of what `__len__` claims, so
    the loop now exits on its own condition and the guard stays silent.

    Asserting the SILENCE is the negative control for the cursor - restoring the
    buffer re-slicing makes the guard fire again and fails this test.
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
        chunks = chunker.process("")

    assert isinstance(chunks, list)
    assert chunker._pos > 0, "the read cursor must advance on adversarial input"
    assert not any(
        "Infinite loop guard triggered" in record.message for record in caplog.records
    ), "the cursor should terminate this input without needing the guard"
