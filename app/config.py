from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Set

class Settings(BaseSettings):
    """Application-wide settings – single source of truth."""

    host: str = "127.0.0.1"
    port: int = 8000

    db_path: str = "pma_metadata.db"
    schema_path: str = "app/storage/schema.sql"

    chroma_persist_dir: str = "chroma_db"

    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_batch_size: int = 512  # Doubled: modern GPUs/CPUs handle this well

    chunk_size: int = 512
    chunk_overlap: int = 50
    max_file_size_mb: int = 50
    supported_extensions: str = ".txt,.md,.pdf,.docx,.csv,.json,.py,.js,.ts,.java,.c,.cpp,.rs,.go,.rb,.html,.css,.xml,.yaml,.yml,.toml,.ini,.cfg,.sh,.bat,.uasset,.umap,.uproject,.uplugin"
    index_concurrency: int = 16  # Increased from 12 for better I/O overlap

    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    gemini_timeout: float = 30.0
    ollama_url: str = "http://localhost:11434/api/generate"
    ollama_model: str = "llama3"
    ollama_timeout: float = 60.0

    rrf_fts_weight: float = 0.4
    rrf_semantic_weight: float = 0.6
    rrf_k: int = 60
    rrf_score_scale: int = 1000
    summary_boost_factor: float = 1.25
    retrieval_top_k: int = 6
    context_max_tokens: int = 3000  # Increased from 2200 for richer context

    dev_mode: bool = True  # Default on for hackathon; set to False for prod
    log_level: str = "INFO"

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def extensions_set(self) -> Set[str]:
        return {e.strip() for e in self.supported_extensions.split(",") if e.strip()}

    model_config = {
        "env_prefix": "PMA_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

settings = Settings()
