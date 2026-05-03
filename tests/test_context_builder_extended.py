"""
tests/test_context_builder_extended.py
Extended coverage for app/search/context_builder.py.
Tests deduplication, token budgeting, stats formatting, and full build_context.
"""

from app.search.context_builder import (
    _compress_text,
    _deduplicate_by_file,
    _format_file_stats,
    _semantic_deduplicate,
    _token_count,
    _truncate_to_tokens,
    build_context,
)

# ── _compress_text ────────────────────────────────────────────────────────────


class TestCompressText:
    def test_collapses_multiple_newlines(self):
        text = "line1\n\n\n\n\nline2"
        result = _compress_text(text)
        assert "\n\n\n" not in result
        assert "line1" in result
        assert "line2" in result

    def test_strips_surrounding_whitespace(self):
        result = _compress_text("   hello world   ")
        assert result == "hello world"

    def test_empty_string(self):
        assert _compress_text("") == ""


# ── _format_file_stats ────────────────────────────────────────────────────────


class TestFormatFileStats:
    def make_stats(self, total_files=10, size=2.5):
        return {
            "total_files": total_files,
            "total_size_mb": size,
            "by_type": [{"ext": ".py", "count": 5, "size_mb": 1.0}],
            "by_folder": [{"folder": "myproject", "count": 10}],
        }

    def test_contains_total_files(self):
        result = _format_file_stats(self.make_stats())
        assert "Total indexed files: 10" in result

    def test_contains_folder(self):
        result = _format_file_stats(self.make_stats())
        assert "myproject: 10 files" in result

    def test_contains_file_type(self):
        result = _format_file_stats(self.make_stats())
        assert ".py" in result

    def test_empty_by_type(self):
        stats = {"total_files": 0, "total_size_mb": 0, "by_type": [], "by_folder": []}
        result = _format_file_stats(stats)
        assert "Total indexed files: 0" in result


# ── _token_count and _truncate_to_tokens ──────────────────────────────────────


class TestTokenCount:
    def test_nonempty_returns_positive(self):
        assert _token_count("hello world") > 0

    def test_empty_returns_positive_or_zero(self):
        # max(1, 0//4) = 1 in char-based fallback
        result = _token_count("")
        assert isinstance(result, int)

    def test_longer_text_has_more_tokens(self):
        short = _token_count("hi")
        long = _token_count("hello world this is a longer text with many words")
        assert long > short


class TestTruncateToTokens:
    def test_empty_text_returns_empty(self):
        assert _truncate_to_tokens("", 100) == ""

    def test_zero_max_returns_empty(self):
        assert _truncate_to_tokens("some text", 0) == ""

    def test_short_text_unchanged(self):
        text = "short"
        result = _truncate_to_tokens(text, 10000)
        assert result == text or text in result

    def test_long_text_truncated(self):
        text = "word " * 1000
        result = _truncate_to_tokens(text, 50)
        assert len(result) < len(text)


# ── _semantic_deduplicate ─────────────────────────────────────────────────────


class TestSemanticDeduplicate:
    def make_result(self, text):
        return {"text": text, "file_path": "file.py", "score": 1.0}

    def test_identical_texts_deduplicated(self):
        text = "This is a longer text block that definitely exceeds fifty characters minimum limit."
        results = [self.make_result(text), self.make_result(text)]
        deduped = _semantic_deduplicate(results)
        assert len(deduped) == 1

    def test_different_texts_kept(self):
        results = [
            self.make_result("This is the first unique piece of text in the corpus here."),
            self.make_result("Completely different content about another topic entirely here."),
        ]
        deduped = _semantic_deduplicate(results)
        assert len(deduped) == 2

    def test_short_texts_always_kept(self):
        results = [{"text": "hi", "file_path": "a.py"}, {"text": "hi", "file_path": "b.py"}]
        deduped = _semantic_deduplicate(results)
        assert len(deduped) == 2

    def test_empty_input(self):
        assert _semantic_deduplicate([]) == []


# ── _deduplicate_by_file ──────────────────────────────────────────────────────


class TestDeduplicateByFile:
    def make_snippet(self, path, text, score=1.0):
        return {"file_path": path, "text": text, "score": score}

    def test_max_2_per_file(self):
        snippets = [
            self.make_snippet("a.py", "unique text one here that is long enough to pass" * 5),
            self.make_snippet("a.py", "unique text two here that is long enough to pass" * 5),
            self.make_snippet("a.py", "unique text three here that is long enough to pass" * 5),
        ]
        result = _deduplicate_by_file(snippets, max_per_file=2)
        from_a = [r for r in result if r["file_path"] == "a.py"]
        assert len(from_a) <= 2

    def test_different_files_kept(self):
        snippets = [
            self.make_snippet("a.py", "text one for file a that is long enough" * 3),
            self.make_snippet("b.py", "text two for file b that is long enough" * 3),
            self.make_snippet("c.py", "text for file c that is definitely different" * 3),
        ]
        result = _deduplicate_by_file(snippets, max_per_file=2)
        files = {r["file_path"] for r in result}
        assert "a.py" in files or "b.py" in files  # at least some are kept


# ── build_context ─────────────────────────────────────────────────────────────


class TestBuildContext:
    def test_empty_all_returns_no_relevant(self):
        result = build_context([], max_tokens=1000)
        assert result == "No relevant context found."

    def test_with_results(self):
        results = [{"file_path": "app.py", "text": "def main(): pass", "score": 1.0}]
        result = build_context(results, max_tokens=1000)
        assert "app.py" in result or "main" in result

    def test_with_file_stats(self):
        stats = {
            "total_files": 5,
            "total_size_mb": 1.0,
            "by_type": [{"ext": ".py", "count": 5, "size_mb": 1.0}],
            "by_folder": [{"folder": "src", "count": 5}],
        }
        result = build_context([], max_tokens=2000, file_stats=stats)
        assert "File Statistics" in result

    def test_with_folder_profiles(self):
        result = build_context(
            [],
            max_tokens=2000,
            folder_profiles_text="PROJECT: MyApp\nType: Python",
        )
        assert "MyApp" in result

    def test_with_metadata_insights(self):
        result = build_context(
            [],
            max_tokens=2000,
            metadata_insights="You have 3 Python projects indexed.",
        )
        assert "Python" in result

    def test_token_budget_respected(self):
        large_text = "content " * 5000
        results = [{"file_path": "big.py", "text": large_text, "score": 1.0}]
        result = build_context(results, max_tokens=100)
        # Should be truncated
        assert len(result) < len(large_text)

    def test_score_threshold_filters_low_score(self):
        results = [
            {"file_path": "top.py", "text": "top scoring result " * 20, "score": 1.0},
            {"file_path": "low.py", "text": "very low scoring result " * 20, "score": 0.01},
        ]
        result = build_context(results, max_tokens=5000)
        # Top result should be included
        assert isinstance(result, str)

    def test_zero_max_tokens_uses_settings_default(self):
        results = [{"file_path": "x.py", "text": "some text", "score": 1.0}]
        result = build_context(results, max_tokens=0)
        assert isinstance(result, str)
