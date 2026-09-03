"""Turn a real folder of documents into a text corpus the eval harness can label.

`tests/eval/corpus_large` is 24 generated Markdown files, and by 2026-09-03 every
knob swept against it came back null at about one standard deviation - the tuning
headroom is gone and the failures that remain are single queries (CLAUDE.md
8.7f). Fixing that needs a corpus of real documents with real language, and
labelling one needs its text on disk.

**Why materialise rather than label the PDFs in place.** Answer spans are
character offsets, and CLAUDE.md section 7 records what they are offsets *into*:
the extracted text stream, not the file's bytes. For Markdown those coincide,
which is why the generated fixture works. For a PDF or a PPTX they do not - there
is no character 4,812 of a PDF in any sense the chunker would agree with. So the
extractor runs once, its output is written as text, and the labels address that
text. Indexing the result then goes through `_extract_plain_text_stream`, which
reads it back unchanged, so a label written here means the same thing at
retrieval time.

The cost, stated plainly: the extraction path itself stops being under test. That
is the right trade - extraction has its own tests, and what this corpus is for is
retrieval and delivery on realistic prose.

**Output is private.** The text is whatever was in the source documents. The
default destination is gitignored, and it is gitignored *before* this script is
ever run. Do not move it somewhere that is not.

    .venv\\Scripts\\python.exe scripts/materialize_corpus.py \\
        --src "C:/Users/binit/Documents/College" --out tests/eval/corpus_college
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.indexing.extractors import EXTRACTORS, ExtractMeta

# service.py filters exactly these before the chunker sees them; a corpus that
# kept them would be labelling error messages as document content (8.1a).
_STUB_PREFIXES = ("[BINARY:", "[UNREADABLE:", "[ENCRYPTED")

# Below this, a "document" is a title slide or a scanned page that yielded
# nothing - it cannot carry an answer span and would only dilute retrieval.
_MIN_USEFUL_CHARS = 400


def _extract(path: Path, max_file_size: int) -> tuple[str, str]:
    """Mirror `IndexingService._extract_and_chunk`'s `_get_stream` exactly.

    Returns ``(text, reason)`` - reason is "" on success. Deliberately a copy of
    the dispatch rather than a call into the service: the service needs a
    database, a progress handle and an embedding model, none of which this needs,
    and the part that matters is only which extractor wins and how its fragments
    are joined.
    """
    parts: list[str] = []
    try:
        for ex in EXTRACTORS:
            if ex.can_handle(path):
                for fragment in ex.extract_stream(path, max_file_size):
                    if isinstance(fragment, ExtractMeta):
                        continue  # out-of-band: which pages want OCR
                    if fragment.startswith(_STUB_PREFIXES):
                        return "", "stub"
                    parts.append(fragment)
                break
        else:
            # Plain text. errors="replace" and universal newlines, matching
            # _extract_plain_text_stream, so what is written here is what a
            # re-read would produce.
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return "", f"error: {type(exc).__name__}"

    text = "".join(parts)
    if not text.strip():
        return "", "empty"
    return text, ""


def _safe_stem(name: str) -> str:
    """A filename that survives being a path component and a JSON key."""
    cleaned = unicodedata.normalize("NFKD", name)
    cleaned = "".join(c if (c.isalnum() or c in " ._-") else "_" for c in cleaned)
    return "_".join(cleaned.split())[:96] or "untitled"


def main() -> int:
    p = argparse.ArgumentParser(description="Materialise a real folder as a text corpus.")
    p.add_argument("--src", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("tests/eval/corpus_college"))
    p.add_argument(
        "--min-chars",
        type=int,
        default=_MIN_USEFUL_CHARS,
        help=f"skip documents shorter than this (default {_MIN_USEFUL_CHARS})",
    )
    args = p.parse_args()

    if not args.src.is_dir():
        raise SystemExit(f"not a directory: {args.src}")
    args.out.mkdir(parents=True, exist_ok=True)

    outcomes: Counter[str] = Counter()
    written = 0
    total_chars = 0
    seen: set[Path] = set()

    for src in sorted(args.src.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(args.src)
        # Top-level directory becomes the folder_tag the harness indexes on, so
        # a file sitting loose at the root gets one rather than none.
        domain = _safe_stem(rel.parts[0]) if len(rel.parts) > 1 else "root"

        text, reason = _extract(src, settings.max_file_size_bytes)
        if reason:
            outcomes[reason] += 1
            continue
        if len(text) < args.min_chars:
            outcomes["too_short"] += 1
            continue

        dest_dir = args.out / domain
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (_safe_stem(src.stem) + ".txt")
        n = 2
        while dest in seen:  # two source files can normalise to one name
            dest = dest_dir / f"{_safe_stem(src.stem)}_{n}.txt"
            n += 1
        seen.add(dest)

        # newline="\n" explicitly: section 7's offsets hold only while disk and
        # stream agree, and a CRLF write would make them disagree on re-read.
        with dest.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        written += 1
        total_chars += len(text)
        outcomes["written"] += 1

    print(f"source     : {args.src}")
    print(f"destination: {args.out}")
    print(f"written    : {written} documents, {total_chars:,} characters")
    if written:
        print(f"mean length: {total_chars // written:,} characters")
    for k, v in sorted(outcomes.items()):
        if k != "written":
            print(f"  skipped {k:<12} {v}")
    print("\nnext: label it. Spans must be offsets into THESE files, not the sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
