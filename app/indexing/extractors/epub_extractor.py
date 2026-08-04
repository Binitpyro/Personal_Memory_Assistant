"""
EPUB text extractor using zip-based parsing (no AGPL dependencies).

Instead of EbookLib (AGPL-3.0), we parse EPUB files directly as ZIP archives
containing XHTML/HTML content, which is the EPUB format spec. This is
permissive-license-safe and works for all standard EPUBs (2.x and 3.x).
"""

import logging
import posixpath
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_NAMED_ENTITY_RE = re.compile(r"&[a-zA-Z]+;")
_NUMERIC_ENTITY_RE = re.compile(r"&#\d+;")
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_ENTRY_READ_BYTES = 10_000_000
_MAX_CUMULATIVE_DECOMPRESSED_SIZE = 100 * 1024 * 1024
# container.xml / the OPF package document are metadata, not content - a
# legitimate one is a few KB. Cap generously to avoid parsing an adversarial
# multi-hundred-MB "metadata" entry.
_MAX_METADATA_ENTRY_BYTES = 5_000_000
_CONTENT_SUFFIXES = (".xhtml", ".html", ".htm", ".xml")


def _is_content_entry(name: str) -> bool:
    return name.lower().endswith(_CONTENT_SUFFIXES) and not name.lower().startswith("__macosx")


def _parse_zip_xml(zf: zipfile.ZipFile, name: str) -> ET.Element | None:
    """Parse a small XML entry (container.xml or the OPF) from the zip.

    Returns None on any failure - missing entry, oversized, unparseable -
    so callers can fall back rather than raise.
    """
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_METADATA_ENTRY_BYTES:
        logger.debug("Skipping oversized EPUB metadata entry %s (%d bytes)", name, info.file_size)
        return None
    try:
        with zf.open(info) as f:
            # defusedxml isn't a dependency here (intentional - dependency
            # minimalism), but stdlib ElementTree on this Python version is
            # not actually exposed on the two classic vectors: XXE raises
            # "undefined entity" (external entity resolution is disabled by
            # default), and entity-expansion ("billion laughs") is blocked
            # by expat's built-in amplification-factor limit. Verified
            # empirically against this target, not assumed.
            return ET.parse(f).getroot()  # noqa: S314
    except Exception:
        return None


def _resolve_opf_path(zf: zipfile.ZipFile) -> str | None:
    """Read META-INF/container.xml to find the OPF package document's path."""
    root = _parse_zip_xml(zf, "META-INF/container.xml")
    if root is None:
        return None
    # Wildcard namespace ("{*}tag") matches EPUB2/3's namespaced container.xml
    # and tolerates a malformed file that omits the namespace declaration.
    rootfile = root.find(".//{*}rootfile")
    if rootfile is None:
        return None
    full_path = rootfile.get("full-path")
    return full_path or None


def _spine_order_from_opf(zf: zipfile.ZipFile, opf_path: str) -> list[str] | None:
    """Parse the OPF manifest + spine into zip entry names in reading order."""
    root = _parse_zip_xml(zf, opf_path)
    if root is None:
        return None

    # NB: Element.iter("{*}tag") does NOT support the "{*}" wildcard - that
    # syntax is only understood by the XPath-style find/findall/iterfind
    # (backed by ElementPath). iter() only does exact tag comparison.
    manifest_id_to_href: dict[str, str] = {}
    for item in root.findall(".//{*}item"):
        item_id = item.get("id")
        href = item.get("href")
        if item_id and href:
            manifest_id_to_href[item_id] = href

    spine = root.find(".//{*}spine")
    if spine is None:
        return None

    opf_dir = posixpath.dirname(opf_path)
    ordered: list[str] = []
    for itemref in spine.findall(".//{*}itemref"):
        idref = itemref.get("idref")
        href = manifest_id_to_href.get(idref) if idref else None
        if not href:
            continue
        href = unquote(href.split("#", 1)[0])
        entry_name = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href
        ordered.append(entry_name)

    return ordered or None


def _get_content_files_in_order(zf: zipfile.ZipFile, path: Path) -> list[str]:
    """Reading order for an EPUB's content documents.

    Prefers the OPF spine (the actual authoring order - chapter 2 sorts
    after chapter 10 alphabetically, but the spine always has it right).
    Falls back to alphabetical if there's no OPF, it's malformed, or the
    spine doesn't resolve to any entries actually present in the zip - e.g.
    a minimal/hand-built EPUB with no META-INF/container.xml at all.
    """
    namelist_set = set(zf.namelist())
    opf_path = _resolve_opf_path(zf)
    if opf_path and opf_path in namelist_set:
        spine_files = _spine_order_from_opf(zf, opf_path)
        if spine_files:
            filtered = [
                name for name in spine_files if name in namelist_set and _is_content_entry(name)
            ]
            if filtered:
                return filtered

    logger.info("EPUB %s: no usable OPF spine, falling back to alphabetical order.", path)
    return sorted(name for name in zf.namelist() if _is_content_entry(name))


class EpubExtractor:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".epub"

    def extract_stream(self, path: Path, max_file_size: int) -> Iterator[str]:
        """Yield text from an EPUB by streaming its internal XHTML documents."""
        try:
            total_chars = 0
            cumulative_decompressed_bytes = 0

            with zipfile.ZipFile(str(path), "r") as zf:
                content_files = _get_content_files_in_order(zf, path)

                for file_name in content_files:
                    if total_chars >= max_file_size:
                        break

                    try:
                        entry_info = zf.getinfo(file_name)
                        if (
                            cumulative_decompressed_bytes + entry_info.file_size
                            > _MAX_CUMULATIVE_DECOMPRESSED_SIZE
                        ):
                            logger.warning(
                                "EPUB extraction stopped: cumulative decompressed size limit "
                                "(%d bytes) would be exceeded for %s",
                                _MAX_CUMULATIVE_DECOMPRESSED_SIZE,
                                path,
                            )
                            raise ValueError("Decompression limit exceeded (potential ZIP bomb)")

                        # Read at most 10 MB from a safe entry.
                        with zf.open(entry_info) as f:
                            raw_bytes = f.read(min(entry_info.file_size, _MAX_ENTRY_READ_BYTES))
                            cumulative_decompressed_bytes += len(raw_bytes)
                            if cumulative_decompressed_bytes > _MAX_CUMULATIVE_DECOMPRESSED_SIZE:
                                logger.warning(
                                    "EPUB extraction stopped: cumulative decompressed size limit (%d bytes) exceeded for %s",
                                    _MAX_CUMULATIVE_DECOMPRESSED_SIZE,
                                    path,
                                )
                                raise ValueError(
                                    "Decompression limit exceeded (potential ZIP bomb)"
                                )

                            raw_html = raw_bytes.decode("utf-8", errors="ignore")

                            # Strip script/style blocks (tag + enclosed content) before
                            # generic tag-stripping, which would otherwise leave the JS/CSS
                            # body behind as prose. An unclosed <script> tag won't match
                            # here and still leaks via the tag-strip pass below - acceptable
                            # for malformed input.
                            text = _SCRIPT_STYLE_RE.sub(" ", raw_html)
                            # Strip XML/HTML tags and collapse whitespace
                            text = _HTML_TAG_RE.sub(" ", text)
                            text = _NAMED_ENTITY_RE.sub(" ", text)
                            text = _NUMERIC_ENTITY_RE.sub(" ", text)
                            text = _WHITESPACE_RE.sub(" ", text).strip()

                            if len(text) > 50:
                                yield text
                                total_chars += len(text)
                    except ValueError as ve:
                        raise ve
                    except Exception as inner_e:
                        logger.debug("Skipping EPUB entry %s: %s", file_name, inner_e)
                        continue
        except ValueError as ve:
            logger.warning("EPUB extraction aborted: %s", ve)
        except Exception as e:
            logger.warning("Failed to extract EPUB %s: %s", path, e)

    def extract(self, path: Path, max_file_size: int) -> str:
        """Legacy extraction for backward compatibility."""
        return "\n".join(self.extract_stream(path, max_file_size))[:max_file_size]
