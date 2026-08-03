import logging

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# P1-3: Module-level cache for extensions_set to avoid repeated split/strip on each file
_extensions_cache: dict[str, set[str]] = {}

# Legacy/incorrect suffixes users may have copied into PMA_OLLAMA_URL or an old
# .env file. Ollama's provider client builds its own paths ("/api/tags",
# "/api/chat", ...) onto base_url, so any of these turn every request into a
# 404 (e.g. "http://localhost:11434/api/generate" + "/api/tags").
_OLLAMA_URL_SUFFIXES = ("/api/generate", "/api/chat", "/api/tags")


def _get_extensions_set(raw: str) -> set[str]:
    if raw not in _extensions_cache:
        _extensions_cache[raw] = {e.strip() for e in raw.split(",") if e.strip()}
    return _extensions_cache[raw]


class Settings(BaseSettings):
    """Application-wide settings - single source of truth."""

    host: str = "127.0.0.1"
    port: int = 8000

    db_path: str = "data/pma_metadata.db"
    schema_path: str = "app/storage/schema.sql"

    lancedb_mode: str = "portable"  # "portable" or "split_brain"
    lancedb_persist_dir: str = "data/lancedb"
    # Split-brain always keeps this recovery copy; portable mode may opt in.
    sqlite_embedding_backup: bool = False

    @model_validator(mode="after")
    def compute_paths(self):
        import os
        import sys

        # Determine the base directory for persistent data
        if self.lancedb_mode == "split_brain":
            if sys.platform == "win32":
                persist_base = os.path.join(
                    os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local")),
                    "PersonalMemoryAssistant",
                )
            else:
                persist_base = os.path.expanduser("~/.cache/personal_memory_assistant")

            os.makedirs(persist_base, exist_ok=True)

            # Update paths to use the persistent base
            self.db_path = os.path.join(persist_base, "pma_metadata.db")
            self.lancedb_persist_dir = os.path.join(persist_base, "lancedb_cache")
        else:
            # Portable mode or default - ensure directories exist or are relative to CWD
            if not self.lancedb_persist_dir:
                self.lancedb_persist_dir = "data/lancedb"
            if not self.db_path:
                self.db_path = "data/pma_metadata.db"

            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
            os.makedirs(os.path.abspath(self.lancedb_persist_dir), exist_ok=True)

        return self

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_batch_size: int = 512  # Doubled: modern GPUs/CPUs handle this well
    embedding_allow_download: bool = True
    embedding_allow_unpinned: bool = False

    chunk_size: int = 512
    chunk_overlap: int = 50
    max_file_size_mb: int = 50
    supported_extensions: str = (
        ".txt,.md,.pdf,.docx,.csv,.json,.py,.js,.ts,.java,.c,.cpp,.rs,.go,.rb,.html,.css,.xml"
        ",.yaml,.yml,.toml,.ini,.cfg,.sh,.bat"
        # Extended language support (overhaul plan)
        ",.swift,.kt,.dart,.vue,.svelte,.graphql,.gql,.proto,.thrift"
        ",.sql,.log,.r,.lua,.zig,.tf,.hcl,.ipynb,.rtf,.odt"
        ",.tsx,.jsx,.lock,.env,.gitignore,.editorconfig,.dockerfile,.makefile"
        ","
    )
    index_concurrency: int = 16  # Increased from 12 for better I/O overlap

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    gemini_max_output_tokens: int = 4096
    gemini_timeout: float = 90.0

    openai_api_key: str = ""
    openai_base_url: str = ""

    anthropic_api_key: str = ""

    groq_api_key: str = ""

    openrouter_api_key: str = ""

    nvidia_nim_api_key: str = ""

    openai_compatible_api_key: str = ""
    openai_compatible_base_url: str = ""

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    ollama_timeout: float = 60.0

    @model_validator(mode="after")
    def normalize_ollama_url(self):
        original = self.ollama_url
        url = original.rstrip("/")
        suffix_stripped = False
        # Loop in case someone concatenated more than one suffix.
        while True:
            for suffix in _OLLAMA_URL_SUFFIXES:
                if url.endswith(suffix):
                    url = url[: -len(suffix)].rstrip("/")
                    suffix_stripped = True
                    break
            else:
                break
        if url != original:
            if suffix_stripped:
                logger.warning(
                    "PMA_OLLAMA_URL had a path suffix (e.g. /api/generate) that Ollama's "
                    "provider client appends itself; normalized %r to %r.",
                    original,
                    url,
                )
            self.ollama_url = url
        return self

    lm_studio_url: str = "http://localhost:1234/v1"

    rrf_fts_weight: float = 0.4
    rrf_semantic_weight: float = 0.6
    rrf_k: int = 60
    rrf_score_scale: int = 1000
    summary_boost_factor: float = 1.25
    retrieval_top_k: int = 15
    context_max_tokens: int = 8000  # Balanced for reliability and depth

    dev_mode: bool = False  # Set to True for verbose dev logs and debug endpoints
    log_level: str = "INFO"

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def extensions_set(self) -> set[str]:
        # P1-3: Cached to avoid repeated split+strip on every file during indexing.
        # pydantic BaseSettings does not support @cached_property, so we delegate
        # to a module-level cache keyed on the raw string.
        return _get_extensions_set(self.supported_extensions)

    model_config = {
        "env_prefix": "PMA_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
