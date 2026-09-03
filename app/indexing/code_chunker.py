import ast
import logging
import re
from typing import Any

from app.indexing.graph_extractor import CodeGraphExtractor

logger = logging.getLogger(__name__)


def _line_start_offsets(text: str) -> list[int]:
    """Character offset of the first character of each line.

    ``ast`` reports positions as 1-based line numbers; chunk offsets are
    character positions into the source. One table converts between them for a
    whole file, rather than re-scanning per node.
    """
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _strip_span(text: str, start: int, end: int) -> tuple[str, int, int]:
    """``text[start:end]`` stripped, with the span narrowed to match.

    The chunk bodies are stripped before being stored, so the recorded span has
    to be narrowed by the same amount. Reporting the unstripped span would make
    a chunk claim leading blank lines it does not contain, which matters because
    the span is what deduplication and the span-level eval metrics both read.
    """
    raw = text[start:end]
    lead = len(raw) - len(raw.lstrip())
    trail = len(raw) - len(raw.rstrip())
    return raw.strip(), start + lead, end - trail


class CodeChunker:
    """
    Syntax-aware chunking for code source files.
    Tries to split by function/class boundaries instead of arbitrary character counts.

    Every chunk carries ``start_offset``/``end_offset`` into the *source text*.
    They used to be omitted, and ``IndexingService._create_chunks`` back-filled
    ``0`` and ``len(text_preview)`` for all of them. That made every chunk of a
    file nominally start at 0, so ``_span_overlap_ratio``
    (``app/search/context_builder.py:118``) scored every pair at 1.00 against a
    0.7 threshold and ``_deduplicate_redundant`` dropped all but the first -
    silently reducing any code file to a single chunk of usable context.
    Measured on a 2-chunk file before the fix: spans ``(0, 71)`` and
    ``(0, 1023)``, overlap 1.00, second dropped. See CLAUDE.md 8.7 A4b.

    The offsets were never hard to produce; they already existed at every site
    and were discarded. ``_regex_chunk`` has the match positions,
    ``_chunk_fallback`` has its loop index, and the AST path has
    ``lineno``/``end_lineno``.
    """

    def __init__(self, max_tokens: int = 512):
        self.max_tokens = max_tokens
        # Rough conversion: 1 token ~= 4 chars
        self.max_chars = max_tokens * 4

    def chunk_code(self, text: str, file_path: str, prefix: str = "") -> list[dict[str, Any]]:
        ext = file_path.split(".")[-1].lower() if "." in file_path else ""

        try:
            if ext == "py":
                return self._chunk_python(text, prefix, file_path)
            elif ext in ["js", "ts", "jsx", "tsx"]:
                return self._chunk_javascript(text, prefix, file_path)
            elif ext == "rs":
                return self._chunk_rust(text, prefix, file_path)
            else:
                return self._chunk_fallback(text, prefix)
        except Exception as e:
            logger.warning(f"Syntax chunking failed for {file_path} ({e}), dropping to fallback.")
            return self._chunk_fallback(text, prefix)

    @staticmethod
    def _chunk(text_preview: str, start: int, end: int) -> dict[str, Any]:
        return {"text_preview": text_preview, "start_offset": start, "end_offset": end}

    def _chunk_python(self, text: str, prefix: str, file_path: str) -> list[dict[str, Any]]:
        chunks = []
        try:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(text)
            lines = text.split("\n")
            line_starts = _line_start_offsets(text)

            def span_of(node: Any) -> tuple[int, int]:
                start_line = getattr(node, "lineno", 1) - 1
                end_line = getattr(node, "end_lineno", len(lines))
                start = line_starts[min(start_line, len(line_starts) - 1)]
                end = line_starts[min(end_line, len(line_starts) - 1)]
                return start, max(start, end)

            extractor = CodeGraphExtractor("py")
            nodes, edges = extractor.extract_from_ast(tree, file_path)

            # Gather top-level imports to prepend to the FIRST chunk only.
            # H-18: Prepending imports to every chunk inflates token usage and
            # introduces duplicate content that degrades retrieval precision.
            imports: Any = [
                node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
            ]
            import_text = ""
            for node in imports:
                start, end = getattr(node, "lineno", 1) - 1, getattr(node, "end_lineno", 1)
                import_text += "\n".join(lines[start:end]) + "\n"

            # Gather module scope code (globals, if __name__ == "__main__", etc)
            module_scope_lines = set()
            boundaries = []
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    boundaries.append(node)
                elif not isinstance(node, (ast.Import, ast.ImportFrom)):
                    start = getattr(node, "lineno", 1) - 1
                    end = getattr(node, "end_lineno", len(lines))
                    for line_no in range(start, end):
                        module_scope_lines.add(line_no)

            module_scope_text = "\n".join(lines[line_no] for line_no in sorted(module_scope_lines))
            if module_scope_text.strip():
                full_chunk = (
                    f"{prefix}\n{import_text}\n{module_scope_text}"
                    if import_text
                    else f"{prefix}\n{module_scope_text}"
                )
                # This chunk's text is assembled from NON-CONTIGUOUS lines, so it
                # has no exact span - the statements it joins are interleaved
                # with the functions between them. Anchored at the first
                # module-scope line and given the length of the text it actually
                # holds: right place, right size, deliberately not claiming the
                # whole range it was gathered from. Claiming min..max would
                # overlap every function chunk in the file and dedup would then
                # drop them, which is the defect this class had in the first
                # place.
                ms_start = line_starts[min(sorted(module_scope_lines)[0], len(line_starts) - 1)]
                ms_end = min(len(text), ms_start + len(module_scope_text))
                if len(full_chunk) > self.max_chars:
                    chunks.extend(
                        self._chunk_fallback(
                            module_scope_text,
                            prefix + ("\n" + import_text if import_text else ""),
                            base_offset=ms_start,
                        )
                    )
                else:
                    chunks.append(self._chunk(full_chunk, ms_start, ms_end))

            if not boundaries and not chunks:
                return self._chunk_fallback(text, prefix)

            for node in boundaries:
                start_line = getattr(node, "lineno", 1) - 1
                end_line = getattr(node, "end_lineno", len(lines))
                node_start, node_end = span_of(node)

                chunk_body = "\n".join(lines[start_line:end_line])
                # Only prepend imports on the first chunk for context; subsequent
                # chunks omit them to prevent duplication across the index.
                preamble = import_text if not chunks else ""
                full_chunk = (
                    f"{prefix}\n{preamble}\n{chunk_body}" if preamble else f"{prefix}\n{chunk_body}"
                )

                # If too big, split it blindly, otherwise add
                if len(full_chunk) > self.max_chars:
                    chunks.extend(
                        self._chunk_fallback(
                            chunk_body,
                            prefix + ("\n" + preamble if preamble else ""),
                            base_offset=node_start,
                        )
                    )
                else:
                    chunks.append(self._chunk(full_chunk, node_start, node_end))

            # Append the extracted graph items to the first chunk as metadata
            if chunks and (nodes or edges):
                chunks[0]["kg_nodes"] = nodes
                chunks[0]["kg_edges"] = edges

            return chunks
        except SyntaxError:
            return self._chunk_fallback(text, prefix)

    def _chunk_javascript(self, text: str, prefix: str, file_path: str) -> list[dict[str, Any]]:
        pattern = re.compile(
            r"^(?:export\s+)?"
            r"(?:"
            r"(?:async\s+)?function\s+\w+\s*\([^)]*\)\s*\{|"
            r"class\s+\w+(?:\s+extends\s+\w+)?\s*\{|"
            r"(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\([^)]*\))?\s*=>\s*\{|"
            r"(?:const|let|var)\s+\w+\s*=\s*\{"
            r")",
            re.MULTILINE,
        )
        extractor = CodeGraphExtractor("js")
        nodes, edges = extractor.extract_from_text(text, file_path)
        chunks = self._regex_chunk(text, prefix, pattern)
        if chunks and (nodes or edges):
            chunks[0]["kg_nodes"] = nodes
            chunks[0]["kg_edges"] = edges
        return chunks

    def _chunk_rust(self, text: str, prefix: str, file_path: str) -> list[dict[str, Any]]:
        # Regex heuristic for Rust: fn, impl, struct, enum
        pattern = re.compile(
            r"^(?:pub\s+)?(?:async\s+)?(?:fn|impl|struct|enum|trait)\s+\w+", re.MULTILINE
        )
        extractor = CodeGraphExtractor("rs")
        nodes, edges = extractor.extract_from_text(text, file_path)
        chunks = self._regex_chunk(text, prefix, pattern)
        if chunks and (nodes or edges):
            chunks[0]["kg_nodes"] = nodes
            chunks[0]["kg_edges"] = edges
        return chunks

    def _regex_chunk(self, text: str, prefix: str, pattern: re.Pattern) -> list[dict[str, Any]]:
        chunks = []
        matches = list(pattern.finditer(text))

        if not matches:
            return self._chunk_fallback(text, prefix)

        if matches[0].start() > 0:
            preamble_body, pre_start, pre_end = _strip_span(text, 0, matches[0].start())
            if preamble_body:
                full_chunk = f"{prefix}\n{preamble_body}"
                if len(full_chunk) > self.max_chars:
                    chunks.extend(
                        self._chunk_fallback(preamble_body, prefix, base_offset=pre_start)
                    )
                else:
                    chunks.append(self._chunk(full_chunk, pre_start, pre_end))

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

            chunk_body, body_start, body_end = _strip_span(text, start, end)
            if not chunk_body:
                continue

            full_chunk = f"{prefix}\n{chunk_body}"
            if len(full_chunk) > self.max_chars:
                chunks.extend(self._chunk_fallback(chunk_body, prefix, base_offset=body_start))
            else:
                chunks.append(self._chunk(full_chunk, body_start, body_end))

        return chunks

    def _chunk_fallback(self, text: str, prefix: str, base_offset: int = 0) -> list[dict[str, Any]]:
        """Blind character split.

        ``base_offset`` translates the local slice positions into source
        coordinates: this is called with a *substring* (one oversized function,
        the module-scope text) as often as with a whole file, and without it
        every such chunk would claim to start at 0 - the defect described on the
        class.
        """
        chunks = []
        text_len = len(text)
        max_chunk = self.max_chars - len(prefix) - 5
        if max_chunk < 100:
            max_chunk = self.max_chars  # Safety

        step = max(1, max_chunk - 50)
        for i in range(0, text_len, step):  # 50 char overlap
            body = text[i : i + max_chunk]
            chunks.append(
                self._chunk(f"{prefix}\n{body}", base_offset + i, base_offset + i + len(body))
            )

        return chunks
