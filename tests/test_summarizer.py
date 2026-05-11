from pathlib import Path

from app.indexing.summarizer import generate_deep_summary


def test_summarize_python_classes_and_functions():
    code = """
class MyClass:
    def method_one(self):
        pass

def top_level_func(a, b):
    \"\"\"This is a docstring.\"\"\"
    return a + b
"""
    summary = generate_deep_summary(code, Path("test.py"))
    assert "Classes: MyClass" in summary
    assert "Functions: top_level_func" in summary
    assert "This is a docstring" in summary


def test_summarize_markdown_headers():
    md = "# Main Title\n## Section One\nContent here.\n### Subsection"
    summary = generate_deep_summary(md, Path("readme.md"))
    assert "Structure: Main Title > Section One > Subsection" in summary


def test_summarize_json_keys():
    data = '{"id": 1, "metadata": {"author": "me"}, "tags": ["a", "b"]}'
    summary = generate_deep_summary(data, Path("data.json"))
    assert "Keys: id, metadata, tags" in summary


def test_summarize_typescript_symbols():
    ts = "export interface User { id: number }; export function getUser(id: number) { return {} }"
    summary = generate_deep_summary(ts, Path("user.ts"))
    assert "Symbols: User, getUser" in summary


def test_summarize_rust_symbols():
    rs = "pub struct Config { port: u16 } pub fn run() {}"
    summary = generate_deep_summary(rs, Path("main.rs"))
    assert "Symbols: Config, run" in summary


def test_summarize_csv_headers():
    csv = "id,name,email\n1,alice,a@b.com\n2,bob,b@c.com"
    summary = generate_deep_summary(csv, Path("users.csv"))
    # Match the literal first line
    assert "Headers: id,name,email" in summary


def test_summarize_empty_file():
    summary = generate_deep_summary("", Path("empty.txt"))
    assert "Empty file" in summary


def test_fallback_snippet():
    text = "Just some regular text that should be truncated normally."
    summary = generate_deep_summary(text, Path("notes.txt"), max_chars=10)
    assert "[TXT: notes.txt] Just some" in summary
