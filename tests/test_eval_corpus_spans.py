"""The labelled-span fixture has to stay true, or every chunk metric lies.

Not marked ``eval``: this needs no models and no index, only files and the
chunker, so it runs in the default suite where it can actually catch drift. The
metrics it protects are opt-in; the invariant they rest on should not be.

What can silently break, in the order it is likely to:

1. **Line endings.** The repo sets ``core.autocrlf=true`` with ``* text=auto``,
   so any file not pinned in ``.gitattributes`` arrives CRLF on a fresh clone.
   That does not break the labels *today* - ``_extract_plain_text_stream`` opens
   in text mode, so CRLF collapses to LF and matches the LF text the offsets
   were computed against. It breaks them the moment any reader stops
   normalising: a binary read, a ``newline=""`` read, or an extractor handling
   bytes itself, which rust_core does. Pinning LF makes disk and stream
   identical so that change cannot silently invalidate every offset.
2. **Hand-edits.** The corpus and the offsets are generated together. Editing
   either alone desynchronises them, again with nothing raising.
3. **The fixture stopping being multi-chunk**, which is its entire reason to
   exist over ``tests/eval/corpus``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import settings
from app.indexing.service import StreamChunker
from scripts.generate_eval_corpus import generate

CORPUS = Path("tests/eval/corpus_large")
QUERIES = Path("tests/eval/queries_large.json")

# The reason this fixture exists rather than tests/eval/corpus, which sits at a
# median of 1-2. Kept well under the measured median of 21 so ordinary content
# edits do not trip it.
# The corpus is validated against a REFERENCE size, not the shipped one.
#
# What this fixture has to guarantee is that its documents can *express*
# chunking - that a chunk is a fraction of a document rather than the whole of
# it - which is a property of the corpus, not of whatever `chunk_size` currently
# ships. Asserting against `settings.chunk_size` would make these guards move
# every time the setting is swept, which is backwards: the sweep is the
# experiment, the corpus is the apparatus.
#
# 512 is the reference because it is the size the corpus was built and labelled
# against, and the size the short/long answer contrast was designed for.
REFERENCE_CHUNK_SIZE = 512
REFERENCE_CHUNK_OVERLAP = 50
MIN_CHUNKS_PER_LABELLED_FILE = 15

# At the shipped size a document must still be several chunks, or chunk-level
# metrics collapse back into document-level ones. Loose on purpose - it tracks
# whatever ships, so it must not encode one particular setting.
MIN_CHUNKS_AT_SHIPPED_SIZE = 3


def _load() -> dict:
    return json.loads(QUERIES.read_text(encoding="utf-8"))


def _chunks(path: Path, size: int | None = None, overlap: int | None = None) -> list[dict]:
    """Chunk exactly as ingestion does, including reading in text mode."""
    text = path.read_text(encoding="utf-8")
    chunker = StreamChunker(
        settings.chunk_size if size is None else size,
        settings.chunk_overlap if overlap is None else overlap,
        f"[{path.suffix.lstrip('.').upper()}: {path.name}] ",
    )
    return chunker.process(text) + chunker.finalize()


def test_corpus_files_are_lf_on_disk():
    """Keep disk and extracted stream identical, so no reader change can desync them.

    Not currently load-bearing - text-mode reads normalise CRLF, so the offsets
    survive a CRLF checkout as things stand. This holds the assumption still.
    """
    crlf = [str(p) for p in sorted(CORPUS.rglob("*.md")) if b"\r\n" in p.read_bytes()]
    assert not crlf, (
        f"{len(crlf)} corpus file(s) are CRLF on disk, which invalidates every "
        f"answer_span offset: {crlf[:5]}. Check the .gitattributes rule for "
        f"{CORPUS}."
    )


def test_answer_spans_address_the_text_they_claim():
    """Read the file the way the indexer does and confirm the span is real."""
    data = _load()
    assert data["queries"], "no queries in the fixture"

    for q in data["queries"]:
        assert q["answer_spans"], f"{q['id']} carries no answer_spans"
        for span in q["answer_spans"]:
            path = CORPUS / span["file"]
            assert path.exists(), f"{q['id']} labels a missing file: {span['file']}"
            text = path.read_text(encoding="utf-8")
            start, end = span["start"], span["end"]
            assert 0 <= start < end <= len(text), (
                f"{q['id']} span [{start}, {end}) is outside {span['file']} (length {len(text)})"
            )
            segment = text[start:end]
            assert segment.strip(), f"{q['id']} span addresses only whitespace"
            # The passage is meant to be the unique answer in the corpus. If it
            # stops being unique the distractors have started answering.
            assert text.count(segment) == 1, (
                f"{q['id']} answer passage occurs {text.count(segment)} times in "
                f"{span['file']}; it must be unique for the label to mean anything"
            )


def test_labelled_documents_are_actually_multi_chunk():
    """The fixture's whole purpose. If this fails it has become corpus/ again."""
    data = _load()
    for q in data["queries"]:
        for span in q["answer_spans"]:
            path = CORPUS / span["file"]
            n_ref = len(_chunks(path, REFERENCE_CHUNK_SIZE, REFERENCE_CHUNK_OVERLAP))
            assert n_ref >= MIN_CHUNKS_PER_LABELLED_FILE, (
                f"{span['file']} produces {n_ref} chunks at the reference size "
                f"{REFERENCE_CHUNK_SIZE}, below the {MIN_CHUNKS_PER_LABELLED_FILE} "
                f"this fixture exists to guarantee"
            )
            n_shipped = len(_chunks(path))
            assert n_shipped >= MIN_CHUNKS_AT_SHIPPED_SIZE, (
                f"{span['file']} produces only {n_shipped} chunks at the shipped "
                f"chunk_size={settings.chunk_size}; at that point chunk ~= document "
                f"and the span metrics stop measuring chunking"
            )


def test_long_answers_straddle_chunk_boundaries_and_short_ones_do_not():
    """The negative control for the chunk metrics.

    ``answer_coverage`` can only respond to chunk size if some answers are
    actually split by it. If every span fitted inside one chunk, a sweep would
    return a flat line and it would look like chunk size does not matter -
    when in fact the fixture had stopped being able to see it.

    Asserted at ``REFERENCE_CHUNK_SIZE``, not the shipped one. At larger sizes a
    long answer deliberately *stops* straddling - that is the improvement the
    sweep measures, not a broken fixture.
    """
    data = _load()
    seen = {"short": 0, "long": 0}

    for q in data["queries"]:
        span = q["answer_spans"][0]
        path = CORPUS / span["file"]
        touching = [
            c
            for c in _chunks(path, REFERENCE_CHUNK_SIZE, REFERENCE_CHUNK_OVERLAP)
            if c["start_offset"] < span["end"] and c["end_offset"] > span["start"]
        ]
        seen[q["answer_len"]] = seen.get(q["answer_len"], 0) + 1

        if q["answer_len"] == "long":
            assert len(touching) >= 3, (
                f"{q['id']} is labelled long but spans only {len(touching)} chunk(s) "
                f"at the reference chunk_size={REFERENCE_CHUNK_SIZE}; the coverage "
                f"metric has nothing to measure on it"
            )
        else:
            assert len(touching) <= 2, (
                f"{q['id']} is labelled short but spans {len(touching)} chunks; "
                f"the short/long contrast is what makes a sweep readable"
            )

    assert seen["short"] >= 2 and seen["long"] >= 2, (
        f"need at least two of each answer length to compare groups, got {seen}"
    )


def test_corpus_matches_its_generator(tmp_path):
    """Regenerating must reproduce the committed bytes exactly.

    Offsets and text are produced together; a hand-edit to either desynchronises
    them with nothing raising. This is what makes "never hand-edit" enforceable
    rather than a comment nobody reads.
    """
    generate(tmp_path / "corpus", tmp_path / "queries.json")

    committed = sorted(p.relative_to(CORPUS).as_posix() for p in CORPUS.rglob("*.md"))
    regenerated = sorted(
        p.relative_to(tmp_path / "corpus").as_posix() for p in (tmp_path / "corpus").rglob("*.md")
    )
    assert committed == regenerated, "corpus file list drifted from the generator"

    drifted = [
        rel
        for rel in committed
        if (CORPUS / rel).read_text(encoding="utf-8")
        != (tmp_path / "corpus" / rel).read_text(encoding="utf-8")
    ]
    assert not drifted, (
        f"{len(drifted)} corpus file(s) differ from what the generator produces: "
        f"{drifted[:5]}. Regenerate with scripts/generate_eval_corpus.py rather "
        f"than editing them."
    )

    assert _load() == json.loads((tmp_path / "queries.json").read_text(encoding="utf-8")), (
        "queries_large.json drifted from the generator; regenerate it"
    )


@pytest.mark.parametrize("group", ["short", "long"])
def test_every_answer_length_group_is_populated(group):
    data = _load()
    assert any(q["answer_len"] == group for q in data["queries"])
