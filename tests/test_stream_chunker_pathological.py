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


def test_stream_chunker_loop_guard_trigger(caplog):
    """
    Verify that the infinite loop guard is tripped and handles the input gracefully.
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

    assert any("Infinite loop guard triggered" in record.message for record in caplog.records)
    assert isinstance(chunks, list)
