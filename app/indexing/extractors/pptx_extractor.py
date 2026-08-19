"""PPTX text extraction straight from the OOXML package.

python-pptx was replaced here because `Presentation()` builds an lxml element
tree for every part in the package before a single character is read, and that
tree — not the embedded media — is the cost. Measured over 42 real decks
(33 MB, 0.1 MB of media, 0.15 MB of slide XML): **90.0 MB** peak working set via
python-pptx against **2.2 MB** reading the same text from the zip, a 41x
difference for the same content. On a 403 MB personal corpus that difference was
most of the gap between the ingestion floor of a PPTX-heavy corpus (318.6 MB)
and one with no slides in it at all (204.7 MB). See CLAUDE.md section 6.

A deck is a zip of small XML parts, so each slide is parsed on its own and
dropped before the next one is opened: peak is bounded by the largest single
slide, not by the deck and not by the corpus.

Output is byte-identical to the python-pptx implementation this replaces,
including its omissions - grouped shapes are not descended into, because
`slide.shapes` did not recurse either. That equivalence is asserted against real
decks in tests/test_pptx_extractor_stream.py rather than assumed; the tests that
existed before mocked python-pptx out entirely and so could not have caught a
regression here.
"""

import logging
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from pathlib import Path

from app.indexing.extractors._ooxml_guard import check_ooxml_archive

logger = logging.getLogger(__name__)

# DrawingML (shape text), PresentationML (slide structure), and the two
# relationship namespaces - the package-level one used inside .rels parts and
# the document-level one used for r:id attributes.
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_NOTES_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"


def _resolve(base_dir: str, target: str) -> str:
    """Resolve a relationship Target against the part directory that owns it.

    Targets are relative to the .rels file's parent part, and commonly start
    "../". zipfile names are always forward-slashed and never absolute.
    """
    if target.startswith("/"):
        return target.lstrip("/")
    parts = [p for p in base_dir.split("/") if p]
    for segment in target.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
        else:
            parts.append(segment)
    return "/".join(parts)


# A DTD in an OOXML part is always hostile: the format never uses one, and both
# ElementTree and lxml expand internal entities, so a "billion laughs" deck
# inflates in memory during parse. Measured: a 9-line prolog expands to 30,000
# characters at depth 4, and depth scales it geometrically - lxml behaves the
# same way, so python-pptx carried this exposure too. _ooxml_guard bounds the
# *declared* decompressed size but cannot see post-parse expansion.
_DTD_MARKERS = (b"<!DOCTYPE", b"<!ENTITY", b"<!doctype", b"<!entity")


def _parse_part(zf: zipfile.ZipFile, name: str) -> ET.Element:
    """Parse one package part, refusing any that declares a DTD.

    Raises ValueError on a DTD, KeyError when the part is absent and
    ET.ParseError when it is malformed - callers already handle all three.
    """
    data = zf.read(name)
    if any(marker in data for marker in _DTD_MARKERS):
        raise ValueError(f"OOXML part declares a DTD, refusing to parse: {name}")
    # S314 is suppressed below, not ignored: it flags stdlib XML on untrusted
    # input, and the entity-expansion vector it warns about is closed by the DTD
    # check above. OOXML needs no DTD support at all, so pulling in defusedxml
    # would buy nothing here.
    return ET.fromstring(data)  # noqa: S314


def _read_rels(zf: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Map relationship Id -> (Type, resolved target) for one part."""
    slash = part.rfind("/")
    base_dir, name = part[:slash], part[slash + 1 :]
    rels_name = f"{base_dir}/_rels/{name}.rels"
    try:
        root = _parse_part(zf, rels_name)
    except (KeyError, ET.ParseError, ValueError):
        return {}
    out: dict[str, tuple[str, str]] = {}
    for rel in root.findall(f"{_PKG_REL}Relationship"):
        rid = rel.get("Id")
        target = rel.get("Target") or ""
        if rid and target and rel.get("TargetMode") != "External":
            out[rid] = (rel.get("Type") or "", _resolve(base_dir, target))
    return out


def _slide_parts(zf: zipfile.ZipFile) -> list[str]:
    """Slide part names in presentation order.

    Order comes from `p:sldIdLst` resolved through the presentation's rels, not
    from sorting filenames: slide part numbering does not have to match the
    order slides are shown in, and a deck that has had slides reordered or
    deleted will not sort correctly.
    """
    try:
        root = _parse_part(zf, "ppt/presentation.xml")
    except (KeyError, ET.ParseError, ValueError):
        return sorted(
            n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")
        )

    rels = _read_rels(zf, "ppt/presentation.xml")
    ordered: list[str] = []
    id_list = root.find(f"{_P}sldIdLst")
    if id_list is not None:
        for sld in id_list.findall(f"{_P}sldId"):
            rid = sld.get(f"{_R}id")
            if rid and rid in rels:
                ordered.append(rels[rid][1])
    return ordered


def _text_frame_text(tx_body: ET.Element) -> str:
    """Reproduce python-pptx's ``TextFrame.text``.

    Paragraphs joined with "\\n"; within a paragraph, run text concatenated and
    a soft line break rendered as a vertical tab. Both are python-pptx
    behaviours, kept so downstream chunking and the summariser see exactly what
    they saw before.
    """
    paragraphs: list[str] = []
    for para in tx_body.findall(f"{_A}p"):
        buf: list[str] = []
        for child in para:
            if child.tag in (f"{_A}r", f"{_A}fld"):
                node = child.find(f"{_A}t")
                if node is not None and node.text:
                    buf.append(node.text)
            elif child.tag == f"{_A}br":
                buf.append("\v")
        paragraphs.append("".join(buf))
    return "\n".join(paragraphs)


def _shape_text(shape: ET.Element) -> str:
    tx_body = shape.find(f"{_P}txBody")
    return _text_frame_text(tx_body) if tx_body is not None else ""


def _table_of(graphic_frame: ET.Element) -> ET.Element | None:
    graphic = graphic_frame.find(f"{_A}graphic")
    data = graphic.find(f"{_A}graphicData") if graphic is not None else None
    return data.find(f"{_A}tbl") if data is not None else None


def _table_rows(tbl: ET.Element) -> Iterator[str]:
    for row in tbl.findall(f"{_A}tr"):
        cells: list[str] = []
        for cell in row.findall(f"{_A}tc"):
            body = cell.find(f"{_A}txBody")
            cells.append(_text_frame_text(body) if body is not None else "")
        yield " | ".join(cells)


def _notes_text(zf: zipfile.ZipFile, slide_part: str) -> str:
    for rel_type, target in _read_rels(zf, slide_part).values():
        if rel_type != _NOTES_REL:
            continue
        try:
            root = _parse_part(zf, target)
        except (KeyError, ET.ParseError, ValueError):
            return ""
        # The *body* placeholder only. python-pptx exposes
        # notes_slide.notes_text_frame, which is that one placeholder's frame -
        # not every text frame on the part. A notes slide also carries a slide
        # number placeholder and a thumbnail of the slide itself, so taking all of
        # them appended a stray page number to every note. The differential test
        # against 42 real decks caught that; the mocked tests it replaced could not
        # have.
        for sp in root.iter(f"{_P}sp"):
            ph = sp.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
            if ph is None or ph.get("type") != "body":
                continue
            body = sp.find(f"{_P}txBody")
            if body is not None:
                return _text_frame_text(body)
        return ""
    return ""


class PptxExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pptx"

    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str]:
        """Yield text from slides in a PPTX document.

        Order per slide is: marker, regular shape text, tables, speaker
        notes. Regular text must stay first - app/indexing/summarizer.py's
        _summarize_doc_text() takes the first line after each "--- Slide
        N ---" marker as that slide's title, and tables/notes are appended
        content, not the title.
        """
        try:
            check_ooxml_archive(path)

            with zipfile.ZipFile(str(path)) as zf:
                total = 0
                for i, slide_part in enumerate(_slide_parts(zf)):
                    try:
                        slide = _parse_part(zf, slide_part)
                    except (KeyError, ET.ParseError, ValueError) as slide_err:
                        logger.debug(
                            "Skipping unreadable slide %s of %s: %s", slide_part, path, slide_err
                        )
                        continue

                    yield f"--- Slide {i + 1} ---"

                    tree = slide.find(f"{_P}cSld/{_P}spTree")
                    shapes = list(tree) if tree is not None else []

                    # Two passes over the same shape list, matching the previous
                    # implementation: python-pptx exposed .text and .has_table
                    # as separate concerns and this order is load-bearing for
                    # the summariser (see the docstring above). Grouped shapes
                    # are skipped, because slide.shapes did not recurse either.
                    for shape in shapes:
                        if shape.tag != f"{_P}sp":
                            continue
                        text = _shape_text(shape)
                        if text:
                            yield text
                            total += len(text)

                    for shape in shapes:
                        if shape.tag != f"{_P}graphicFrame":
                            continue
                        tbl = _table_of(shape)
                        if tbl is None:
                            continue
                        try:
                            for row_text in _table_rows(tbl):
                                if row_text.strip():
                                    yield row_text
                                    total += len(row_text)
                        except Exception as table_err:
                            logger.debug(
                                "Skipping unreadable table on slide %d of %s: %s",
                                i + 1,
                                path,
                                table_err,
                            )

                    try:
                        notes = _notes_text(zf, slide_part)
                        if notes and notes.strip():
                            yield f"[Notes] {notes}"
                            total += len(notes)
                    except Exception as notes_err:
                        logger.debug(
                            "Skipping unreadable notes on slide %d of %s: %s",
                            i + 1,
                            path,
                            notes_err,
                        )

                    # The parsed slide is dropped here, before the next one is
                    # opened. This is what bounds peak memory to one slide.
                    slide.clear()

                    if total > max_file_size:
                        break
        except ValueError as ve:
            logger.warning("PPTX extraction aborted: %s", ve)
        except Exception as e:
            logger.warning("Failed to extract PPTX %s: %s", path, e)

    def extract(self, path: Path, max_file_size: int) -> str:
        """Legacy extraction for backward compatibility."""
        return "\n".join(self.extract_stream(path, max_file_size))[:max_file_size]
