"""
tests/test_dependency_integrity.py
Guards the declared-dependency contract for tiktoken, which is imported under
try/except and therefore fails silently when absent.

It was undeclared until 2026-08-10, so every install counted tokens as
len(text)//4 - a character heuristic standing in for the project's binding
constraint. Nothing raised, so nothing caught it. These assertions make a
missing dependency a red test instead of a silent capability loss.

datasketch was the other such import. It is deliberately *not* a dependency:
it requires scipy (~98 MB, 357 modules loaded eagerly, not excludable from the
PyInstaller bundle), which the 4 GB hardware target does not justify. Both
MinHash passes were replaced by one exact pass in
``context_builder._deduplicate_redundant``.
"""

import app.search.context_builder as cb


class TestTiktokenPresent:
    def test_tiktoken_imports(self):
        import tiktoken

        assert tiktoken.get_encoding("cl100k_base") is not None

    def test_encoding_resolves(self):
        cb._ENCODING = None
        try:
            assert cb._get_encoding() is not False
        finally:
            cb._ENCODING = None

    def test_get_tokens_returns_real_ids(self):
        cb._ENCODING = None
        cb._get_tokens.cache_clear()
        try:
            assert cb._get_tokens("hello") != []
        finally:
            cb._ENCODING = None
            cb._get_tokens.cache_clear()

    def test_token_count_is_not_the_char_heuristic(self):
        """The fallback is len(text)//4. Assert we are not silently on it."""
        cb._ENCODING = None
        cb._get_tokens.cache_clear()
        try:
            text = "Curl noise turbulence is applied to the velocity field."
            assert cb._token_count(text) != max(1, len(text) // 4)
        finally:
            cb._ENCODING = None
            cb._get_tokens.cache_clear()


class TestNoHeavyTransitiveDeps:
    def test_scipy_is_not_pulled_in(self):
        """scipy arrives only via datasketch, which we dropped on bundle size.

        Guards the decision rather than the package: if datasketch is ever
        re-added, this fails and the ~98 MB gets re-argued instead of silently
        landing in the sidecar.
        """
        import importlib.util

        assert importlib.util.find_spec("datasketch") is None
