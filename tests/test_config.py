import pytest

from app.config import Settings
from app.project_constants import build_context_prefix, chunk_embedding_text


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


class TestOcrSettings:
    """normalize_ocr collapses the enabled/tier pair so every caller can branch
    on a single flag, and clamps the tunables so a bad .env cannot produce a
    nonsensical DPI or a confidence floor above 1.0."""

    def test_ocr_is_off_by_default(self):
        s = Settings()
        assert s.ocr_enabled is False
        assert s.ocr_tier == "none"

    def test_enabling_without_a_tier_is_collapsed_to_off(self, monkeypatch):
        """Enabling OCR with nothing installed can't do anything, so it is a lie."""
        monkeypatch.setenv("PMA_OCR_ENABLED", "true")
        monkeypatch.setenv("PMA_OCR_TIER", "none")
        assert Settings().ocr_enabled is False

    def test_enabled_survives_with_a_real_tier(self, monkeypatch):
        monkeypatch.setenv("PMA_OCR_ENABLED", "true")
        monkeypatch.setenv("PMA_OCR_TIER", "cpu")
        s = Settings()
        assert s.ocr_enabled is True
        assert s.ocr_tier == "cpu"

    def test_unknown_tier_falls_back_to_none(self, monkeypatch):
        monkeypatch.setenv("PMA_OCR_TIER", "quantum")
        s = Settings()
        assert s.ocr_tier == "none"
        assert s.ocr_enabled is False

    def test_tier_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("PMA_OCR_TIER", "  CPU  ")
        assert Settings().ocr_tier == "cpu"

    @pytest.mark.parametrize(
        "env,value,expected",
        [
            ("PMA_OCR_DPI", "10", 72),
            ("PMA_OCR_DPI", "5000", 600),
            ("PMA_OCR_DPI", "300", 300),
            ("PMA_OCR_CONF_FLOOR", "-1", 0.0),
            ("PMA_OCR_CONF_FLOOR", "5", 1.0),
            ("PMA_OCR_GARBAGE_RATIO", "2.5", 1.0),
            ("PMA_OCR_MAX_ATTEMPTS", "0", 1),
            ("PMA_OCR_PAGE_TIMEOUT_S", "-5", 1),
        ],
    )
    def test_tunables_are_clamped(self, monkeypatch, env, value, expected):
        monkeypatch.setenv(env, value)
        assert getattr(Settings(), env.removeprefix("PMA_").lower()) == expected


class TestChunkEmbeddingText:
    """What the embedder sees is not what storage holds. CLAUDE.md D1.

    The stored `text_preview` keeps its `[EXT: name] ` tag for display, FTS
    filename tokens and the offset arithmetic in `_chunk_body`; only the vector
    is built from the body.
    """

    PATH = "/corpus/notes.md"
    BODY = "curl noise is divergence free by construction."

    def test_identity_when_the_prefix_is_kept(self):
        preview = build_context_prefix(self.PATH) + self.BODY
        assert chunk_embedding_text(preview, self.PATH, True) == preview

    def test_strips_exactly_the_prefix_when_off(self):
        preview = build_context_prefix(self.PATH) + self.BODY
        assert chunk_embedding_text(preview, self.PATH, False) == self.BODY

    def test_identity_when_the_preview_does_not_carry_its_prefix(self):
        """An OCR or code chunk whose stored form differs must not be truncated
        by a blind slice - removeprefix is a no-op, len(prefix) would not be."""
        assert chunk_embedding_text(self.BODY, self.PATH, False) == self.BODY

    def test_default_preserves_todays_behaviour(self):
        assert Settings().embed_chunk_prefix is True
