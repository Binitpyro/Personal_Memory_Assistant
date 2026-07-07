import ast
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class CodeGraphExtractor:
    """
    Extracts structural nodes (classes, functions) and edges (calls, inheritances)
    from source code. Built to integrate with CodeChunker to avoid duplicate AST parsing.
    """

    def __init__(self, lang: str):
        self.lang = lang.lower()

    def extract_from_ast(
        self, tree: ast.AST, file_path: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Walks a Python AST to extract nodes and edges.
        Returns:
            nodes: [{"id": str, "label": str, "name": str, "start_line": int, "end_line": int}]
            edges: [{"src_id": str, "dst_id": str, "rel_type": str}]
        """
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        if self.lang != "py":
            return nodes, edges

        class GraphVisitor(ast.NodeVisitor):
            def __init__(self):
                self.current_scope = []

            def visit_ClassDef(self, node):
                node_id = f"{file_path}::{node.name}"
                nodes.append(
                    {
                        "id": node_id,
                        "label": "class",
                        "name": node.name,
                        "start_line": getattr(node, "lineno", 1),
                        "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                    }
                )

                # Check for inheritance edges
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        edges.append(
                            {
                                "src_id": node_id,
                                "dst_id": f"PENDING::{base.id}",  # Will be resolved later
                                "rel_type": "INHERITS",
                            }
                        )

                self.current_scope.append(node_id)
                self.generic_visit(node)
                self.current_scope.pop()

            def visit_FunctionDef(self, node):
                self._handle_function(node)

            def visit_AsyncFunctionDef(self, node):
                self._handle_function(node)

            def _handle_function(self, node):
                parent_id = self.current_scope[-1] if self.current_scope else None
                node_id = f"{file_path}::{node.name}"
                if parent_id:
                    node_id = f"{parent_id}.{node.name}"

                nodes.append(
                    {
                        "id": node_id,
                        "label": "function",
                        "name": node.name,
                        "start_line": getattr(node, "lineno", 1),
                        "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                    }
                )

                if parent_id:
                    edges.append({"src_id": parent_id, "dst_id": node_id, "rel_type": "CONTAINS"})

                self.current_scope.append(node_id)
                self.generic_visit(node)
                self.current_scope.pop()

            def visit_Call(self, node):
                if self.current_scope:
                    caller_id = self.current_scope[-1]
                    callee_name = None
                    if isinstance(node.func, ast.Name):
                        callee_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        callee_name = node.func.attr

                    if callee_name:
                        edges.append(
                            {
                                "src_id": caller_id,
                                "dst_id": f"PENDING::{callee_name}",  # Resolved in _resolve_pending_calls  # noqa: E501
                                "rel_type": "CALLS",
                            }
                        )
                self.generic_visit(node)

        visitor = GraphVisitor()
        visitor.visit(tree)

        return nodes, edges

    def extract_from_text(
        self, text: str, file_path: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Regex-based fallback extraction for JS/TS/Rust.
        """
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        if self.lang in ["js", "ts", "jsx", "tsx"]:
            # Basic JS/TS extraction
            func_pattern = re.compile(
                r"(?:function|class|const|let|var)\s+(\w+)\s*(?:=|{)?", re.MULTILINE
            )
            for match in func_pattern.finditer(text):
                name = match.group(1)
                nodes.append(
                    {
                        "id": f"{file_path}::{name}",
                        "label": "function_or_class",
                        "name": name,
                        "start_line": text[: match.start()].count("\n") + 1,
                        "end_line": text[: match.end()].count("\n") + 1,
                    }
                )
        elif self.lang == "rs":
            func_pattern = re.compile(r"(?:fn|struct|enum|trait)\s+(\w+)", re.MULTILINE)
            for match in func_pattern.finditer(text):
                name = match.group(1)
                nodes.append(
                    {
                        "id": f"{file_path}::{name}",
                        "label": "rust_item",
                        "name": name,
                        "start_line": text[: match.start()].count("\n") + 1,
                        "end_line": text[: match.end()].count("\n") + 1,
                    }
                )

        return nodes, edges
