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

# Install states for the OCR engine.
#   "cpu" - PP-OCRv4 mobile weights bundled in the pinned wheel, CPU only.
#   "gpu" - PP-OCRv4 *server* weights (downloaded, digest-pinned) on DirectML.
#           Windows-only: onnxruntime-directml publishes win_amd64 wheels only.
#   "vlm"  - a vision model in the user's own Ollama / LM Studio. No local
#            engine and no venv; "installed" means a model has been chosen.
_OCR_TIERS = frozenset({"none", "cpu", "gpu", "vlm"})


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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
    # P0-4: the tokenizer pads each batch to its own longest sequence
    # (enable_padding has no length=), so peak memory scales with
    # batch_size * longest_seq_in_batch, not item count. At 512 that's a
    # ~262k-token worst case on a product sized for an 8 GB laptop (the
    # "60 MB RAM" this comment used to cite is retracted - see CLAUDE.md
    # section 6, which now states a steady-state ceiling plus a boundedness
    # invariant that this padding strategy currently violates).
    # 64 is still generous - sentence-transformers defaults to 32. The real
    # fix is token-budget batching; it was deferred as "post-deadline" and
    # that deferral has expired with the XPRIZE withdrawal. This just bounds
    # the blast radius until then.
    embedding_batch_size: int = 64
    # Caps rows x width-of-widest-row per batch, which is what actually bounds
    # peak embedding memory - measured 0.140 MB per (row x token) on bge-small.
    # A row count alone does not bound it: at 64 rows the College corpus (full
    # ~560-char chunks) reached 988 MB in the embed stage, while a fixture of
    # small files stayed low purely because its chunks were short. ~5.09
    # chars/token on real chunk text, so 10240 is ~2000 token-slots ~= 285 MB.
    # Short texts are unaffected - embedding_batch_size is still the row cap, so
    # this only narrows batches that would otherwise be wide. 0 disables it and
    # restores fixed-size batching. See CLAUDE.md section 6.
    embedding_batch_char_budget: int = 10240
    embedding_allow_download: bool = True
    embedding_allow_unpinned: bool = False

    # The reranker loader never downloads - the model must already be on disk -
    # so there is no download gate to mirror here, only the integrity gate.
    reranker_allow_unpinned: bool = False
    # Padding is batch-longest, so the reranker's peak scales with
    # len(batch) * longest_sequence. Capping candidate *count* alone leaves that
    # product unbounded on the interactive query path.
    reranker_max_batch_chars: int = 60_000

    # The persistent semantic cache is scanned exhaustively on every query (it
    # has no vector index) and used to grow without bound. At 1,536 bytes per
    # vector, I/O binds around 5,000 rows at a 50 ms budget; the RAM ceiling is
    # ~39,000 rows, where one probe streams the whole 60 MB budget.
    query_cache_max_rows: int = 5_000
    # Prune every N writes rather than on each one: the cost is the scan and
    # compaction, not the row.
    query_cache_prune_interval: int = 100

    # Explicit model -> context-budget class, checked before the parameter-count
    # heuristic. Parameter count is a proxy for context window and sometimes a
    # poor one (llama3.1:8b has a 128k window), so this is the honest escape
    # hatch. Example: {"llama3.1:8b": "cloud"}.
    model_class_overrides: dict[str, str] = {}

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
        # P0-3: these extractors exist (epub_extractor.py, pptx_extractor.py,
        # xlsx_extractor.py) but the scanner filters on this list, so files
        # of these types never reached them during a normal folder scan.
        ",.epub,.pptx,.xlsx,.xls"
        ","
    )
    index_concurrency: int = 16  # Increased from 12 for better I/O overlap

    # ── OCR (Tier 1, CPU) ────────────────────────────────────────────────
    # The engine runs in its own venv as a subprocess; nothing here pulls
    # rapidocr/pypdfium2 into the main interpreter. `ocr_tier` is the install
    # state, `ocr_enabled` the user switch - normalize_ocr keeps them coherent
    # so callers only ever need to read one of them.
    ocr_enabled: bool = False
    ocr_tier: str = "none"  # "none" | "cpu" | "gpu"
    ocr_dpi: int = 150
    ocr_min_chars_per_page: int = 100
    ocr_garbage_ratio: float = 0.30
    ocr_blank_stream_bytes: int = 512
    ocr_conf_floor: float = 0.30
    ocr_page_timeout_s: int = 30
    ocr_doc_timeout_s: int = 600
    ocr_worker_idle_timeout_s: int = 60
    ocr_worker_max_docs: int = 50
    ocr_worker_max_pages: int = 2000
    ocr_cache_max_mb: int = 500
    ocr_max_attempts: int = 3

    # ── OCR Tier 3 (VLM) ─────────────────────────────────────────────────
    # A vision model reading a 300-DPI page is 1-2 orders of magnitude slower
    # than PP-OCR: a 2550x3300 render becomes thousands of image tokens, and on
    # a 4 GB card a 7B vision model does not fit in VRAM at all, so Ollama
    # spills layers to CPU. The Tier 1/2 budgets (30s per page, 600s per doc)
    # would time out every multi-page scan, retry it `ocr_max_attempts` times,
    # and then fail it - hours of compute for nothing. These are separate
    # settings rather than raised shared ones so the CPU tiers keep their tight
    # budgets, where a 30s page genuinely does mean something is wrong.
    ocr_vlm_page_timeout_s: int = 240
    ocr_vlm_doc_timeout_s: int = 7200
    #: Provider HTTP timeout for a vision call. The default client timeout is
    #: 30s (app/providers/__init__.py), which would abort the request long
    #: before the page timeout ever fired.
    ocr_vlm_request_timeout_s: float = 300.0
    #: Refuse to queue a document longer than this to a VLM. At minutes per
    #: page an unbounded book is a multi-day job the user cannot see the end of.
    ocr_vlm_max_pages_per_doc: int = 50

    @model_validator(mode="after")
    def normalize_ocr(self):
        self.ocr_tier = (self.ocr_tier or "none").strip().lower()
        if self.ocr_tier not in _OCR_TIERS:
            logger.warning(
                "PMA_OCR_TIER=%r is not one of %s; falling back to 'none'.",
                self.ocr_tier,
                sorted(_OCR_TIERS),
            )
            self.ocr_tier = "none"

        # A tier of "none" means nothing is installed, so enabling OCR cannot
        # do anything. Collapsing it here means every caller can branch on a
        # single flag instead of re-deriving the pair.
        if self.ocr_tier == "none":
            self.ocr_enabled = False

        self.query_stream_timeout_s = max(1, self.query_stream_timeout_s)
        self.ocr_garbage_ratio = _clamp(self.ocr_garbage_ratio, 0.0, 1.0)
        self.ocr_conf_floor = _clamp(self.ocr_conf_floor, 0.0, 1.0)
        self.ocr_dpi = int(_clamp(self.ocr_dpi, 72, 600))
        self.ocr_min_chars_per_page = max(0, self.ocr_min_chars_per_page)
        self.ocr_blank_stream_bytes = max(0, self.ocr_blank_stream_bytes)
        self.ocr_page_timeout_s = max(1, self.ocr_page_timeout_s)
        self.ocr_doc_timeout_s = max(1, self.ocr_doc_timeout_s)
        self.ocr_worker_idle_timeout_s = max(1, self.ocr_worker_idle_timeout_s)
        self.ocr_worker_max_docs = max(1, self.ocr_worker_max_docs)
        self.ocr_worker_max_pages = max(1, self.ocr_worker_max_pages)
        self.ocr_cache_max_mb = max(0, self.ocr_cache_max_mb)
        self.ocr_max_attempts = max(1, self.ocr_max_attempts)
        self.ocr_vlm_page_timeout_s = max(1, self.ocr_vlm_page_timeout_s)
        self.ocr_vlm_doc_timeout_s = max(1, self.ocr_vlm_doc_timeout_s)
        self.ocr_vlm_request_timeout_s = max(1.0, self.ocr_vlm_request_timeout_s)
        self.ocr_vlm_max_pages_per_doc = max(1, self.ocr_vlm_max_pages_per_doc)
        return self

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
    nvidia_nim_base_url: str = ""

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
    # Document-routing signal. Ranks whole files by summary similarity, then
    # feeds their chunks into RRF as a third ranked list. Weighted below the two
    # chunk-level signals: it decides which documents are worth spending chunk
    # budget on, it does not by itself decide which chunk answers the question.
    rrf_summary_weight: float = 0.3
    # Chunks pulled per summary-ranked file. Caps how far one long document can
    # push into the fused candidate list.
    summary_expand_chunks_per_file: int = 5
    rrf_k: int = 60
    rrf_score_scale: int = 1000
    retrieval_top_k: int = 15
    context_max_tokens: int = 8000  # Balanced for reliability and depth
    # Hard ceiling on one /api/query/stream response. The keepalive frame proves
    # the connection is alive, not that the model is making progress, and
    # Request.is_disconnected() only resolves when the ASGI server delivers the
    # disconnect - behind a buffering proxy that can lag for minutes while a
    # stuck provider keeps burning tokens. Generous: a local 3 tok/s model on a
    # long answer is legitimately slow.
    query_stream_timeout_s: int = 180

    # ── Source-balanced fusion ───────────────────────────────────────────────
    # Allocate the result window across folder_tag domains rather than taking a
    # single global ranking. Without this a lexically dense corpus floods every
    # slot on a multi-domain query.
    fusion_balance_enabled: bool = True
    fusion_domain_ceiling: float = 0.6  # max share of k any one domain may take

    # ── Bounded agentic retrieval loop ───────────────────────────────────────
    # Off by default: decomposition adds an LLM round-trip to the critical path,
    # which is real latency on a local 4GB provider.
    agentic_enabled: bool = False
    agentic_max_iterations: int = 2
    agentic_subquery_max: int = 4
    # On the cross-encoder logit scale, NOT the RRF scale. ms-marco-MiniLM-L-6-v2
    # logits below about -2.0 mean a very poor match. This was 0.0, read against
    # strictly-positive RRF scores, so every sub-question was marked satisfied by
    # any hit at all and the not-found list never fired. Calibrate against
    # tests/eval before moving it.
    agentic_evidence_score_floor: float = -2.0

    # ── Folder watcher ───────────────────────────────────────────────────────
    # Re-indexes already-indexed folders on a timer so the corpus does not drift
    # from disk between manual runs. Polls rather than subscribing to OS events:
    # the Rust scanner and the indexer's own change detection already do the
    # work, and polling avoids a third-party dependency plus editor write-burst
    # and recursive-watch-limit problems. Detection latency is the interval.
    watcher_enabled: bool = False
    watcher_interval_seconds: int = 300

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
