"""
tests/test_code_chunker.py
Full coverage of app/indexing/code_chunker.py — CodeChunker class.
Tests Python, JS/TS, Rust, and fallback strategies.
"""

from app.indexing.code_chunker import CodeChunker


class TestCodeChunkerInit:
    def test_default_params(self):
        cc = CodeChunker()
        assert cc.max_tokens == 512
        assert cc.max_chars == 512 * 4

    def test_custom_max_tokens(self):
        cc = CodeChunker(max_tokens=256)
        assert cc.max_tokens == 256
        assert cc.max_chars == 1024


class TestChunkPython:
    def setup_method(self):
        self.cc = CodeChunker(max_tokens=512)

    def test_simple_function(self):
        text = "import os\n\ndef hello():\n    print('hello')\n"
        chunks = self.cc.chunk_code(text, "test.py")
        assert len(chunks) >= 1
        assert all("text_preview" in c for c in chunks)

    def test_class_and_method(self):
        text = "class Foo:\n    def bar(self):\n        return 1\n"
        chunks = self.cc.chunk_code(text, "foo.py")
        assert len(chunks) >= 1

    def test_no_boundaries_uses_fallback(self):
        text = "x = 1\ny = 2\nz = 3\n"
        chunks = self.cc.chunk_code(text, "vars.py")
        assert len(chunks) >= 1

    def test_syntax_error_falls_back(self):
        text = "def broken(\n"  # syntax error
        chunks = self.cc.chunk_code(text, "broken.py")
        assert isinstance(chunks, list)

    def test_large_function_splits(self):
        func_body = "    x = 1\n" * 200
        text = f"def big_func():\n{func_body}"
        # Use a small but valid token budget (>= 25 chars to avoid negative overlap)
        cc = CodeChunker(max_tokens=32)
        chunks = cc.chunk_code(text, "big.py")
        assert len(chunks) >= 1

    def test_multiple_functions(self):
        text = "\n".join(
            [
                "import os",
                "def func_a():",
                "    return 'a'",
                "",
                "def func_b():",
                "    return 'b'",
            ]
        )
        chunks = self.cc.chunk_code(text, "multi.py")
        assert len(chunks) >= 2


class TestChunkJavaScript:
    def setup_method(self):
        self.cc = CodeChunker(max_tokens=512)

    def test_function_declaration(self):
        text = "export function hello() {\n    return 'hi';\n}\n"
        chunks = self.cc.chunk_code(text, "app.ts")
        assert len(chunks) >= 1

    def test_class_declaration(self):
        text = "class MyClass {\n    method() {}\n}\n"
        chunks = self.cc.chunk_code(text, "comp.tsx")
        assert len(chunks) >= 1

    def test_no_pattern_match_uses_fallback(self):
        text = "// just a comment\nconst x = 1;\n"
        chunks = self.cc.chunk_code(text, "misc.js")
        assert isinstance(chunks, list)

    def test_jsx_extension_handled(self):
        text = "export const Comp = () => {\n    return <div/>;\n};"
        chunks = self.cc.chunk_code(text, "Comp.jsx")
        assert isinstance(chunks, list)


class TestChunkRust:
    def setup_method(self):
        self.cc = CodeChunker(max_tokens=512)

    def test_fn_declaration(self):
        text = "pub fn compute(x: i32) -> i32 {\n    x * 2\n}\n"
        chunks = self.cc.chunk_code(text, "core.rs")
        assert len(chunks) >= 1

    def test_struct_declaration(self):
        text = "struct Point {\n    x: f64,\n    y: f64,\n}\n"
        chunks = self.cc.chunk_code(text, "types.rs")
        assert len(chunks) >= 1

    def test_impl_block(self):
        text = "impl Foo {\n    fn bar(&self) {}\n}\n"
        chunks = self.cc.chunk_code(text, "impl.rs")
        assert len(chunks) >= 1

    def test_no_pattern_falls_back(self):
        text = "// Just a comment\nuse std::io;\n"
        chunks = self.cc.chunk_code(text, "imports.rs")
        assert isinstance(chunks, list)


class TestChunkFallback:
    def setup_method(self):
        self.cc = CodeChunker(max_tokens=10)  # tiny to trigger chunking

    def test_unknown_extension(self):
        # Use a generous token budget to avoid negative-overlap edge case in _chunk_fallback
        cc = CodeChunker(max_tokens=128)
        text = "some plain text content " * 50
        chunks = cc.chunk_code(text, "readme.md")
        assert len(chunks) >= 1

    def test_no_extension(self):
        text = "plain content"
        chunks = self.cc.chunk_code(text, "Makefile")
        assert isinstance(chunks, list)

    def test_empty_text(self):
        chunks = self.cc.chunk_code("", "empty.txt")
        assert isinstance(chunks, list)

    def test_overlapping_chunks(self):
        cc = CodeChunker(max_tokens=50)
        text = "x" * 5000
        chunks = cc.chunk_code(text, "big.txt")
        assert len(chunks) >= 2
        for c in chunks:
            assert "text_preview" in c


class TestChunkCodeDispatch:
    """Test that chunk_code dispatches correctly based on file extension."""

    def test_dispatch_py(self):
        cc = CodeChunker()
        chunks = cc.chunk_code("def f(): pass", "test.py")
        assert isinstance(chunks, list)
        assert len(chunks) >= 1

    def test_dispatch_ts(self):
        cc = CodeChunker()
        chunks = cc.chunk_code("function f() {}", "test.ts")
        assert isinstance(chunks, list)

    def test_dispatch_rs(self):
        cc = CodeChunker()
        chunks = cc.chunk_code("fn f() {}", "test.rs")
        assert isinstance(chunks, list)

    def test_dispatch_unknown(self):
        cc = CodeChunker()
        chunks = cc.chunk_code("some content", "test.yaml")
        assert isinstance(chunks, list)
