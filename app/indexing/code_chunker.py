import ast
import logging
import re
from typing import Any

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
                return self._chunk_python(text, prefix)
            elif ext in ["js", "ts", "jsx", "tsx"]:
                return self._chunk_javascript(text, prefix)
            elif ext == "rs":
                return self._chunk_rust(text, prefix)
            else:
                return self._chunk_fallback(text, prefix)
        except Exception as e:
            logger.warning(f"Syntax chunking failed for {file_path} ({e}), dropping to fallback.")
            return self._chunk_fallback(text, prefix)

    def _chunk_python(self, text: str, prefix: str) -> list[dict[str, Any]]:
        chunks = []
        try:
            tree = ast.parse(text)
            lines = text.split("\n")

            # Gather top level imports and assignments to prepend to chunks if possible
            imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
            import_text = ""
            for node in imports:
                start, end = getattr(node, "lineno", 1) - 1, getattr(node, "end_lineno", 1)
                import_text += "\n".join(lines[start:end]) + "\n"

            # Find main boundaries: classes and functions
            boundaries = []
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    boundaries.append(node)

            if not boundaries:
                return self._chunk_fallback(text, prefix)

            for _i, node in enumerate(boundaries):
                start = getattr(node, "lineno", 1) - 1
                end = getattr(node, "end_lineno", len(lines))

                chunk_body = "\n".join(lines[start:end])
                full_chunk = f"{prefix}\n{import_text}\n{chunk_body}"

                # If too big, split it blindly, otherwise add
                if len(full_chunk) > self.max_chars:
                    chunks.extend(self._chunk_fallback(chunk_body, prefix + "\n" + import_text))
                else:
                    chunks.append({"text_preview": full_chunk})

            return chunks
        except SyntaxError:
            return self._chunk_fallback(text, prefix)

    def _chunk_javascript(self, text: str, prefix: str) -> list[dict[str, Any]]:
        # Regex heuristic for TS/JS: exported functions, classes, consts
        pattern = re.compile(
            r"^(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+\w+\s*=?\s*(?:\([^)]*\))?\s*(?:=>)?\s*[{]",
            re.MULTILINE,
        )
        return self._regex_chunk(text, prefix, pattern)

    def _chunk_rust(self, text: str, prefix: str) -> list[dict[str, Any]]:
        # Regex heuristic for Rust: fn, impl, struct, enum
        pattern = re.compile(
            r"^(?:pub\s+)?(?:async\s+)?(?:fn|impl|struct|enum|trait)\s+\w+", re.MULTILINE
        )
        return self._regex_chunk(text, prefix, pattern)

    def _regex_chunk(self, text: str, prefix: str, pattern: re.Pattern) -> list[dict[str, Any]]:
        chunks = []
        matches = list(pattern.finditer(text))

        if not matches:
            return self._chunk_fallback(text, prefix)

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

        for i in range(0, text_len, max_chunk - 50):  # 50 char overlap
            body = text[i : i + max_chunk]
            chunks.append({"text_preview": f"{prefix}\n{body}"})

        return chunks
