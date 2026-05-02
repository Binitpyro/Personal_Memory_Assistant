import ast
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_deep_summary(text: str, path: Path, max_chars: int = 300) -> str:
    """
    Generate a high-density structural summary based on file type.
    Falls back to a standard text snippet if no specialized logic applies.
    """
    if not text:
        return f"[{path.suffix.lstrip('.').upper() or 'file'}: {path.name}] (Empty file)"

    ext = path.suffix.lower()
    prefix = f"[{ext.lstrip('.').upper() or 'file'}: {path.name}]"

    try:
        if ext == ".py":
            return f"{prefix} {_summarize_python(text, max_chars)}"
        elif ext in {".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".c", ".cpp", ".java"}:
            return f"{prefix} {_summarize_code_regex(text, ext, max_chars)}"
        elif ext == ".md":
            return f"{prefix} {_summarize_markdown(text, max_chars)}"
        elif ext in {".json", ".yaml", ".yml", ".toml"}:
            return f"{prefix} {_summarize_data_format(text, ext, max_chars)}"
        elif ext in {".xlsx", ".xls", ".csv"}:
            return f"{prefix} {_summarize_spreadsheet_text(text, max_chars)}"
        elif ext in {".pdf", ".pptx", ".docx", ".epub"}:
            return f"{prefix} {_summarize_doc_text(text, max_chars)}"
    except Exception as e:
        logger.debug("Deep summary failed for %s: %s", path.name, e)

    # Fallback: Clean snippet
    snippet = text[:max_chars].replace("\n", " ").strip()
    return f"{prefix} {snippet}"


def _summarize_python(text: str, max_limit: int) -> str:
    try:
        tree = ast.parse(text)
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        functions = [
            n.name
            for n in ast.iter_child_nodes(tree)
            if isinstance(n, ast.FunctionDef) or isinstance(n, ast.AsyncFunctionDef)
        ]

        summary_parts = []
        if classes:
            summary_parts.append(f"Classes: {', '.join(classes[:5])}")
        if functions:
            summary_parts.append(f"Functions: {', '.join(functions[:8])}")

        main_info = "; ".join(summary_parts)
        # Add first available docstring
        doc = ast.get_docstring(tree)
        if not doc:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    doc = ast.get_docstring(node)
                    if doc: break

        if doc:
            main_info += f" — {doc.splitlines()[0]}"

        return main_info[:max_limit]
    except SyntaxError:
        return _summarize_code_regex(text, ".py", max_limit)


def _summarize_code_regex(text: str, ext: str, max_limit: int) -> str:
    # Patterns for common languages
    patterns = {
        ".rs": r"(?:pub\s+)?(?:fn|struct|enum|trait)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        ".go": r"(?:func|type|interface)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        ".java": r"(?:public|protected|private)?\s+(?:class|interface|enum|@interface)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        ".js": r"(?:export\s+)?(?:function|class)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        ".ts": r"(?:export\s+)?(?:function|class|interface|type|enum)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        ".tsx": r"(?:export\s+)?(?:function|class|interface|type|enum)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        ".cpp": r"(?:class|struct|void|int|auto)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
        ".c": r"(?:void|int|char|float)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
        ".py": r"(?:class|def)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    }

    pattern = patterns.get(ext, r"(?:class|function|def|fn)\s+([a-zA-Z_][a-zA-Z0-9_]*)")
    matches = re.findall(pattern, text[:50000])  # limit scan range
    unique_matches = list(dict.fromkeys(matches))  # preserve order, unique only

    if not unique_matches:
        return text[:max_limit].replace("\n", " ").strip()

    return f"Symbols: {', '.join(unique_matches[:15])}"[:max_limit]


def _summarize_markdown(text: str, max_limit: int) -> str:
    headers = re.findall(r"^#+\s+(.*)", text, re.MULTILINE)
    if not headers:
        return text[:max_limit].replace("\n", " ").strip()

    return f"Structure: {' > '.join(headers[:6])}"[:max_limit]


def _summarize_data_format(text: str, ext: str, max_limit: int) -> str:
    # Attempt to find top-level keys without full parse if possible, 
    # but for summaries we only do small fragments anyway.
    try:
        import json
        import yaml
        
        if ext == ".json":
            data = json.loads(text[:100000])
        else: # yaml / toml
            data = yaml.safe_load(text[:50000])
            
        if isinstance(data, dict):
            keys = list(data.keys())
            return f"Keys: {', '.join(keys[:15])} (Total {len(keys)})"
        elif isinstance(data, list):
            return f"Array: contains {len(data)} items"
    except Exception:
        pass
    
    return text[:max_limit].replace("\n", " ").strip()


def _summarize_spreadsheet_text(text: str, max_limit: int) -> str:
    # Our XLSX extractor adds "--- Sheet: Name ---" markers
    sheets = re.findall(r"--- Sheet: (.*) ---", text)
    if sheets:
        return f"Sheets: {', '.join(sheets[:5])}; Contents: {text[:max_limit].replace('---', '').strip()}"[:max_limit]
    
    # For CSV, the first line is headers
    lines = text.splitlines()
    if lines:
        return f"Headers: {lines[0]}"[:max_limit]
    
    return text[:max_limit].strip()


def _summarize_doc_text(text: str, max_limit: int) -> str:
    # PPTX has "--- Slide N ---" markers
    slides = re.findall(r"--- Slide \d+ ---", text)
    if slides:
        # Extract the text immediately following the slide marker as the title
        titles = []
        parts = re.split(r"--- Slide \d+ ---", text)
        for p in parts[1:10]: # Check first 10 slides
            clean = p.strip().splitlines()
            if clean:
                titles.append(clean[0])
        if titles:
            return f"Outline: {', '.join(titles[:8])}"[:max_limit]
        return f"Slides: {len(slides)} total"

    # Default for PDF/Docx is just the clean start of text
    return text[:max_limit].replace("\n", " ").strip()
