import pytest

from app.config import Settings


class TestOllamaUrlNormalization:
    """P0-1: config.py used to default ollama_url to the legacy
    '/api/generate' path, which Ollama's provider client (app/providers/ollama.py)
    then concatenates its own paths onto (/api/tags, /api/chat, ...), 404ing
    every request. The normalizer strips known suffixes so a stale value in
    an old .env file or a copy-pasted PMA_OLLAMA_URL doesn't silently break
    the local-first chain again."""

    def test_default_has_no_suffix(self):
        assert Settings().ollama_url == "http://localhost:11434"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("http://localhost:11434/api/generate", "http://localhost:11434"),
            ("http://localhost:11434/api/chat", "http://localhost:11434"),
            ("http://localhost:11434/api/tags", "http://localhost:11434"),
            ("http://localhost:11434/", "http://localhost:11434"),
            ("http://localhost:11434/api/generate/", "http://localhost:11434"),
            ("http://localhost:11434", "http://localhost:11434"),
            ("http://my-ollama-host:11434", "http://my-ollama-host:11434"),
        ],
    )
    def test_normalizes_suffix_forms(self, monkeypatch, raw, expected):
        monkeypatch.setenv("PMA_OLLAMA_URL", raw)
        assert Settings().ollama_url == expected

    def test_warns_when_suffix_stripped(self, monkeypatch, caplog):
        monkeypatch.setenv("PMA_OLLAMA_URL", "http://localhost:11434/api/generate")
        with caplog.at_level("WARNING", logger="app.config"):
            Settings()
        assert any("PMA_OLLAMA_URL" in r.message for r in caplog.records)

    def test_no_warning_for_clean_url(self, monkeypatch, caplog):
        monkeypatch.setenv("PMA_OLLAMA_URL", "http://localhost:11434")
        with caplog.at_level("WARNING", logger="app.config"):
            Settings()
        assert not any("PMA_OLLAMA_URL" in r.message for r in caplog.records)


class TestSupportedExtensions:
    """P0-3: epub_extractor.py, pptx_extractor.py and xlsx_extractor.py all
    exist and work, but the scanner filters on supported_extensions before a
    file ever reaches an extractor - so these formats never reached their
    extractors during a normal folder scan without a PMA_SUPPORTED_EXTENSIONS
    override."""

    def test_epub_pptx_xlsx_xls_are_supported(self):
        extensions = Settings().extensions_set
        for ext in (".epub", ".pptx", ".xlsx", ".xls"):
            assert ext in extensions
