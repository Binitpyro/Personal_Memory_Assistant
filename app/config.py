from pydantic import model_validator
from pydantic_settings import BaseSettings

# P1-3: Module-level cache for extensions_set to avoid repeated split/strip on each file
_extensions_cache: dict[str, set[str]] = {}


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

    @model_validator(mode="after")
    def compute_lancedb_dir(self):
        if self.lancedb_mode == "split_brain":
            import os
            import sys

            if sys.platform == "win32":
                self.lancedb_persist_dir = os.path.join(
                    os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local")),
                    "PersonalMemoryAssistant",
                    "lancedb_cache",
                )
            else:
                self.lancedb_persist_dir = os.path.expanduser(
                    "~/.cache/personal_memory_assistant/lancedb_cache"
                )
        elif not self.lancedb_persist_dir:
            self.lancedb_persist_dir = "data/lancedb"
        return self

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_batch_size: int = 512  # Doubled: modern GPUs/CPUs handle this well

    chunk_size: int = 512
    chunk_overlap: int = 50
    max_file_size_mb: int = 50
    supported_extensions: str = (
        ".txt,.md,.pdf,.docx,.csv,.json,.py,.js,.ts,.java,.c,.cpp,.rs,.go,.rb,.html,.css,.xml"
        ",.yaml,.yml,.toml,.ini,.cfg,.sh,.bat,.uasset,.umap,.uproject,.uplugin"
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

    ollama_url: str = "http://localhost:11434/api/generate"
    ollama_model: str = "llama3"
    ollama_timeout: float = 60.0

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
