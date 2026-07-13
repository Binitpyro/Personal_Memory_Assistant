import ast
import logging
import re
from typing import Any

from app.indexing.graph_extractor import CodeGraphExtractor

logger = logging.getLogger(__name__)


class CodeChunker:
    """
    Syntax-aware chunking for code source files.
    Tries to split by function/class boundaries instead of arbitrary character counts.
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

    def _chunk_python(self, text: str, prefix: str, file_path: str) -> list[dict[str, Any]]:
        chunks = []
        try:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(text)
            lines = text.split("\n")

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
                    for l in range(start, end):  # noqa: E741
                        module_scope_lines.add(l)

            module_scope_text = "\n".join(lines[l] for l in sorted(module_scope_lines))  # noqa: E741
            if module_scope_text.strip():
                full_chunk = (
                    f"{prefix}\n{import_text}\n{module_scope_text}"
                    if import_text
                    else f"{prefix}\n{module_scope_text}"
                )
                if len(full_chunk) > self.max_chars:
                    chunks.extend(
                        self._chunk_fallback(
                            module_scope_text, prefix + ("\n" + import_text if import_text else "")
                        )
                    )
                else:
                    chunks.append({"text_preview": full_chunk})

            if not boundaries and not chunks:
                return self._chunk_fallback(text, prefix)

            for node in boundaries:
                start = getattr(node, "lineno", 1) - 1
                end = getattr(node, "end_lineno", len(lines))

                chunk_body = "\n".join(lines[start:end])
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
                            chunk_body, prefix + ("\n" + preamble if preamble else "")
                        )
                    )
                else:
                    chunks.append({"text_preview": full_chunk})

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
            preamble_body = text[0 : matches[0].start()].strip()
            if preamble_body:
                full_chunk = f"{prefix}\n{preamble_body}"
                if len(full_chunk) > self.max_chars:
                    chunks.extend(self._chunk_fallback(preamble_body, prefix))
                else:
                    chunks.append({"text_preview": full_chunk})

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

            chunk_body = text[start:end].strip()
            if not chunk_body:
                continue

            full_chunk = f"{prefix}\n{chunk_body}"
            if len(full_chunk) > self.max_chars:
                chunks.extend(self._chunk_fallback(chunk_body, prefix))
            else:
                chunks.append({"text_preview": full_chunk})

        return chunks

    def _chunk_fallback(self, text: str, prefix: str) -> list[dict[str, Any]]:
        chunks = []
        text_len = len(text)
        max_chunk = self.max_chars - len(prefix) - 5
        if max_chunk < 100:
            max_chunk = self.max_chars  # Safety

        step = max(1, max_chunk - 50)
        for i in range(0, text_len, step):  # 50 char overlap
            body = text[i : i + max_chunk]
            chunks.append({"text_preview": f"{prefix}\n{body}"})

        return chunks
