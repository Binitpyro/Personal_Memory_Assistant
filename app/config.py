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
    # Instruction prepended to QUERIES only, never to documents.
    #
    # bge-small-en-v1.5 is trained asymmetrically for short-query -> passage
    # retrieval: the query carries an instruction, the passage does not. PMA
    # embedded both sides identically, which throws away the asymmetry the model
    # was trained with. This is the exact string from the model card - it is not
    # a prompt to tune, it is what the encoder saw during training.
    #
    # Documents are NOT re-embedded by this. Only the query side changes, so the
    # LanceDB chunk and summary indexes stay valid; what does go stale is the
    # persistent semantic query cache, which stores query vectors - handled by
    # versioning the cache scope rather than deleting rows.
    #
    # Set to "" to disable and embed queries exactly like documents.
    embedding_query_prefix: str = "Represent this sentence for searching relevant passages: "
    embedding_allow_download: bool = True
    embedding_allow_unpinned: bool = False

    # The reranker loader never downloads - the model must already be on disk -
    # so there is no download gate to mirror here, only the integrity gate.
    reranker_allow_unpinned: bool = False
    # Padding is batch-longest, so the reranker's peak scales with
    # len(batch) * longest_sequence. Capping candidate *count* alone leaves that
    # product unbounded on the interactive query path.
    reranker_max_batch_chars: int = 60_000

    # How much of the ORIGINAL (RRF) order survives reranking. 0.0 reproduces the
    # old behaviour, where the cross-encoder's ordering simply replaced RRF's.
    #
    # It replaced it for a long time, and that cost answers. The cross-encoder is
    # net-positive - measured over tests/eval/corpus_large it promotes the
    # answer-bearing chunk on 6 of 8 queries, sometimes hugely (rank 25 -> 2) -
    # but it occasionally demotes one, and a demotion across the k boundary costs
    # that query its entire answer while the promotions inside the window buy
    # nothing. Coverage is a threshold metric, so it only ever sees the tail.
    #
    # Measured through the real pipeline at chunk_size=512, k=10, **three
    # independent index builds per arm** - single builds are not usable here, see
    # section 8.4a: chunk ids are assigned in completion order by a concurrent
    # pipeline, so ties resolve differently per build:
    #
    #   weight  answer coverage            document nDCG
    #   0.0     0.509 [0.509 0.509 0.509]  0.985
    #   0.5     0.634 [0.634 0.634 0.634]  0.985
    #
    # +25% coverage, and document ranking is untouched - the lone 0.95 appears
    # once in each arm, which is the tie-resolution noise, not a cost.
    #
    # Do not read an offline simulation of this as a substitute. Fusing the two
    # orderings outside the pipeline predicted 0.684, because it omitted
    # `_rebalance_after_rerank`, which re-applies domain allocation to the answer
    # window afterwards and determines much of the final composition. The number
    # that counts is the one above, taken end to end.
    #
    # At the shipped chunk_size=2048 every arm reaches coverage 1.000 - larger
    # chunks put the answer chunk high enough that a demotion no longer crosses
    # the k boundary - so this costs nothing there and is insurance for the
    # small-chunk regime a large heterogeneous corpus forces you back into.
    reranker_rrf_fusion_weight: float = 0.5
    # The `k` in `1/(k + rank)` for the reranker fusion above. Deliberately NOT
    # `rrf_k`, which is 60 and tuned for the three-signal chunk fusion.
    #
    # Reusing 60 here was the first thing tried and it is the wrong scale: over
    # the ~40-candidate pool the cross-encoder sees, k=60 spreads rank 1 to rank
    # 40 by only **1.64x** (0.01639 -> 0.01000), so position barely signals and
    # the fusion is decided by near-ties. That is the same defect section 8.4a
    # records for the summary leg, where k=60 across a 5-element list gave a 6%
    # spread and turned a ranked signal into a near-binary flag.
    #
    #   k     rank1     rank40    ratio
    #   0     1.00000   0.02500   40.00x
    #   5     0.16667   0.02222    7.50x
    #   10    0.09091   0.02000    4.55x
    #   20    0.04762   0.01667    2.86x
    #   60    0.01639   0.01000    1.64x
    #
    # Swept on the instrument. k=10 is chosen for its FLOOR, not its mean.
    # chunk_size=512, weight 0.5, answer coverage per independent build:
    #
    #   k=10  0.634 0.634 0.634 0.684 0.634 0.634   mean 0.640, min 0.634
    #   k=60  0.634 0.759 0.759 0.684 0.634 0.509   mean 0.663, min 0.509
    #
    # k=60 has the higher mean and is still the wrong choice: its floor of 0.509
    # is exactly the pure-cross-encoder number, i.e. on that build the fusion
    # bought nothing. k=10 never drops below 0.634, which is +25% on pure
    # cross-encoder, every time.
    #
    # (Neither is strictly deterministic - an earlier note here claiming k=10 was
    # is corrected: a later build returned 0.684. What differs is the spread.
    # Chunk ids are assigned in completion order by a concurrent pipeline, so the
    # candidate set itself shifts per build; that is upstream of this setting.)
    #
    # The mechanism is section 8.4a's, exactly. At 1.64x spread over 40
    # candidates the fused scores are mostly ties, so tie-resolution - i.e. build
    # order - decides, and the signal "stopped breaking ties and became a
    # near-binary flag". A sharper k gives position real weight, so the fusion
    # decides on ranks instead of on luck.
    reranker_fusion_k: int = 10

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

    # Characters, not tokens - and that mismatch is why this was four times too
    # small. The embedder truncates at 512 **tokens**
    # (app/embeddings/service.py), and at the measured ~4.8 chars/token on real
    # chunk text a 512-character chunk is ~110 tokens: 21% of the window the
    # model actually holds. Chunks were a fifth of the size they could be.
    #
    # Swept on tests/eval/corpus_large with span-level ground truth, k=10,
    # reporting precision AND coverage because section 8.4a records a
    # configuration that improved recall while wrecking the ranking:
    #
    #   chars  tokens  chunks | prec/cov (rerank off) | prec/cov (rerank on)
    #     512     110     589 |   0.113 / 0.648       |   0.100 / 0.509
    #    1024     216     285 |   0.088 / 0.625       |   0.088 / 0.672
    #    1536     322     190 |   0.125 / 0.784       |   0.113 / 0.655
    #    2048     425     140 |   0.150 / 0.911       |   0.163 / 1.000
    #
    # 2048 wins on every span metric in both arms, and document nDCG with the
    # reranker off goes 0.875 -> 1.000. The one metric that moves the wrong way
    # is document nDCG with the reranker on, 1.000 -> 0.938, which was saturated
    # anyway.
    #
    # Precision was expected to FALL as chunks grew - more non-answer text per
    # chunk - and it does not; it rises with coverage. At 512 the answer-bearing
    # chunk frequently was not retrieved at all, so the window filled with
    # fragments of the right document. A 2048-character chunk is a coherent
    # passage that both contains the answer and ranks for it.
    #
    # Also settles 8.7 A6: CodeChunker computes max_chars as max_tokens * 4 =
    # 2048, so the two chunkers finally agree on size instead of differing 4x.
    #
    # Caveat, and it matters: 8 queries over one generated corpus. Treat this as
    # a configuration comparison, not an absolute. Re-sweep on a real corpus
    # before treating 2048 as settled.
    #
    # CONFIRMED 2026-09-03 on generation quality, which is what the sweep above
    # could not see (section 8.7f). Answer token-recall against the labelled
    # spans, 3 independent builds per arm, reranker on, floor / mean:
    #
    #   chars | gemma2-2b (3b_local) | gemma4-local (7b_local)
    #     512 |    0.216 / 0.267     |     0.532 / 0.563
    #    1024 |    0.268 / 0.322     |     0.616 / 0.646
    #    2048 |    0.291 / 0.328     |     0.676 / 0.729
    #
    # Monotonic on both models, on floor AND mean. 2048 holds.
    #
    # Two things that sweep found which the delivery numbers above cannot show.
    # First, DELIVERED COVERAGE IS ANTI-CORRELATED WITH GENERATION FOR
    # 3b_local: coverage ranks 512 best (0.484 vs 0.359 at 2048) and generation
    # ranks it worst. Do not use delivery as a cheap stand-in for an answer.
    # Second, 3b_local delivers only 1,719 tokens against a 2,520 budget. That
    # looked like a max_chunks limit and IS NOT: sweeping context_max_chunks_small
    # over 3/5/8 leaves delivered tokens flat at ~1,703 (8.7f). What bounds the 3B
    # is still open; max_per_file is the next candidate.
    # CHANGED TO 1024 on 2026-09-04, by instruction, against the table above.
    # Recording the trade rather than quietly restating the evidence: 2048 won
    # that sweep monotonically on both models, so this gives up ~0.006 recall on
    # gemma2-2b and ~0.083 on gemma4-local at the measured means. What it buys is
    # precision - at 2048 char_precision is 0.024, i.e. 97.6% of what the model
    # reads is not the answer, and section 8.7e left that flagged and unresolved.
    # Whether the trade is net positive is measured below, not asserted here.
    #
    # One cost that the chunk_size sweep never showed, found 2026-09-04: the
    # sentence-boundary lookback is an ABSOLUTE character count, so the fraction
    # of chunk boundaries that fall mid-sentence is ~40% on real prose at EVERY
    # chunk size (39.8% at 512, 39.4% at 1024, 40.5% at 2048, corpus_squad).
    # Halving chunk_size therefore doubles the number of broken sentences,
    # 345 -> 696, because it doubles how often you split. That is why
    # `chunk_boundary_lookback_share` below is part of this change and not a
    # separate one.
    chunk_size: int = 1024
    # Held at ~10% of chunk_size, which is what the sweep above used.
    chunk_overlap: int = 102
    # How far back from the target split point to hunt for a sentence or
    # paragraph end, as a fraction of chunk_size. `StreamChunker._find_boundary`
    # hardcoded 100 characters from 23399ca (2026-05-03) until this became a
    # setting.
    #
    # A fraction rather than an absolute, even though the measurement says
    # absolute characters are what drive the hit rate: a fraction cannot exceed
    # chunk_size, and it bounds size variance relative to the chunk instead of
    # letting a big lookback shrink a small chunk arbitrarily.
    #
    # Swept on corpus_squad at chunk_size=1024, overlap=102:
    #
    #   lookback | %chunk | hard cut | mean size | sd
    #      100   |   9.8% |   39.4%  |    996    | 31.4   <- the old constant
    #      160   |  15.6% |   20.1%  |    967    | 48.4
    #      224   |  21.9% |    9.0%  |    939    | 62.4
    #      256   |  25.0% |    6.1%  |    928    | 69.1   <- ships
    #      320   |  31.2% |    3.3%  |    903    | 87.3
    #
    # 0.25 cuts mid-sentence splits 6.5x for a 9% smaller mean chunk. Past it the
    # returns fall off and size variance keeps climbing, and chunk size tracking
    # text structure instead of the budget is exactly the defect that sank
    # `chunk_markdown`'s first attempt (8.7b).
    #
    # It holds at the old chunk size too - 0.25 of 2048 is 512, which measured
    # 1.2% hard cuts - so this is not a 1024-specific patch.
    chunk_boundary_lookback_share: float = 0.25
    # Ceiling on the whole-text buffer used by the syntax-aware chunkers.
    #
    # Code and markdown are chunked from the *whole* file rather than streamed:
    # an AST needs the complete source, and chunk_markdown needs to see every
    # heading. That buffering is the one thing in ingestion that could scale
    # with a document rather than with a tunable, which is exactly what the
    # section 6 boundedness invariant forbids. Capping it keeps peak a function
    # of (this value x index_concurrency) and nothing about the corpus; a file
    # over the cap degrades to the streaming chunker rather than being buffered.
    #
    # 1 MB is generous against reality: measured over this repo's own 186
    # .py/.ts/.tsx/.rs sources, median 4,911 bytes, p95 33,039, max 93,488, and
    # zero above 1 MB. At index_concurrency=16 the worst case is ~16 MB against
    # a 250 MB idle ceiling.
    chunk_buffer_max_chars: int = 1_000_000
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
    ocr_tier: str = "none"  # "none" | "cpu" | "gpu" | "vlm" (app/ocr/settings.py)
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
    #
    # Swept, 4 builds each, on tests/eval. `->` is against the leg switched off
    # (recall 0.979, nDCG 0.985):
    #
    #   w=0.3  (shipped)  recall 0.868  nDCG 0.873   both far worse than off
    #   w=0.1             recall 0.993  nDCG 0.944   recall up, ranking wrecked
    #   w=0.05            recall 0.993  nDCG 0.995   both up
    #   w=0.02            recall 0.993  nDCG 0.995   same
    #
    # The shipped 0.3 was ~6x too high and cost 0.111 recall against not using
    # the leg at all. The signal was never the problem - the summary index
    # ranks the right document first for a single-file query - the scale was.
    # A top-ranked file contributed 0.3/(rrf_k + 1) = 0.004918 to each of its
    # chunks while a semantic hit at rank r contributes 0.6/(60 + r + 1), so the
    # boost outweighed ~61 ranks of chunk evidence, more than a small corpus
    # contains. The leg stopped breaking ties and became a near-binary "is this
    # file in the top 5 summaries" flag, made worse because rrf_k = 60 barely
    # differentiates a *5*-element list (rank 0 -> 0.004918, rank 4 -> 0.004615).
    #
    # 0.1 is a trap worth naming: recall looks great and nDCG collapses, because
    # promoting a file's chunks as a block reorders them against each other.
    # Sweep both metrics, never recall alone.
    #
    # summary_expand_chunks_per_file makes no measurable difference once the
    # weight is right (0.02 at per_file 1 and 5 are identical), so it is left
    # alone.
    #
    # Caveat: 12 queries over 24 documents. That is enough to show 0.3 reliably
    # hurts - the ranges do not overlap - and not enough to characterise the leg
    # on a corpus where document routing actually earns its keep.
    rrf_summary_weight: float = 0.05
    # Chunks pulled per summary-ranked file. Caps how far one long document can
    # push into the fused candidate list.
    summary_expand_chunks_per_file: int = 5
    rrf_k: int = 60
    rrf_score_scale: int = 1000
    retrieval_top_k: int = 15
    context_max_tokens: int = 8000  # Balanced for reliability and depth

    # Slot budget for the 3b_local context class in build_context. These were
    # function-local literals until 2026-09-03; they are settings now because
    # they, not chunk_size, are what bounds the segment section 3 exists to
    # serve, and a literal cannot be swept.
    #
    # Measured (section 8.7f): at the shipped chunk_size=2048 a 3b_local
    # context delivers 1,719 tokens against a 2,520 budget, and delivered
    # coverage never exceeds 0.484 at any chunk size while 7b_local reaches
    # 1.000. gemma2-2b scores 0.328 answer-recall against gemma4-local's 0.729.
    #
    # Swept 3/5/8 and it DOES NOT BIND: delivered tokens stay flat at ~1,703 and
    # coverage is non-monotonic. Kept as a setting because the sweep is what
    # showed that, and the next candidate (max_per_file) needed the same
    # treatment. Do not assume raising it helps.
    #
    # Defaults are exactly the values that were hardcoded, so this change on
    # its own alters nothing. Sweep them before moving them.
    context_max_chunks_small: int = 3
    # RAISED 1 -> 2 on 2026-09-03, and it is the first knob in this block that
    # moved. Everything above was swept on corpus_large, whose delivery sd is
    # 0.069-0.137, so nothing smaller than ~0.12 was decidable there and every
    # sweep came back null. corpus_squad (100 queries over 48 real Wikipedia
    # articles, section 8.7g) has sd 0.0043-0.0078, and the same knob is
    # decisive on it. Three builds per arm, reranker on:
    #
    #   value | 3b coverage | sd     | 3b tokens | 7b_local (control)
    #   ------+-------------+--------+-----------+-------------------
    #     1   | 0.8417      | 0.0043 |   1,481   | 0.9126
    #     2   | 0.9043      | 0.0058 |   1,697   | 0.9093
    #     3   | 0.9058      | 0.0078 |   1,764   | 0.9092
    #
    # +0.063, and the per-value ranges do not overlap at all - 1's best build is
    # 0.8467, 2's worst is 0.8976. It SATURATES at 2 (2->3 buys +0.0016, well
    # inside noise), so there is no case for 3. The 7b_local control does not
    # read this setting and stays flat within 0.0034, which is what confines the
    # change to the class it is for.
    #
    # What it buys: 3b_local goes 0.842 -> 0.904 against cloud's 0.909, i.e. the
    # small class stops being the one that loses answers. Section 8.7g measured
    # that gap at ~7 sd and attributed it to delivery rather than retrieval
    # (document nDCG is 0.957); this closes most of it.
    #
    # It is a corpus-shape bet, stated rather than hidden: allowing 2 chunks per
    # file helps when the answer needs two passages of ONE document, and costs
    # file diversity when it needs one passage each from three. SQuAD answers sit
    # inside a single article, so this fixture is biased toward the first case.
    context_max_per_file_small: int = 2

    # Share of the REMAINING snippet budget each of the first three snippets
    # may take in _format_snippets. Geometric, so three snippets reach at most
    # 1 - (1 - share)^3 of the budget: 0.712 at the shipped 0.34.
    #
    # That is the binding constraint on 3b_local, whose max_chunks is 3 - every
    # snippet lands in the head branch, so 29% of its budget is unreachable no
    # matter what else is tuned. Predicted 1,789 tokens, measured 1,796 (8.7f).
    # Raising it trades depth on the top-ranked chunk for reach across the
    # rest; that trade is unmeasured, which is why the default is unchanged.
    # Share of the REMAINING snippet budget each of the first three snippets may
    # take in _format_snippets. Geometric, so three snippets reach at most
    # 1 - (1 - share)^3 of the budget.
    #
    # TWO settings because the two classes have different optima and the reason
    # is structural, not empirical. 3b_local's max_chunks is 3, so every snippet
    # is in the head branch and there is no tail to starve - a larger share is
    # strictly more evidence. 7b_local and cloud get 15 slots, and a large share
    # starves snippets 4-15 outright.
    #
    # Swept 0.34/0.5/0.7/0.8, three builds each, answer-recall:
    #
    #   share | gemma2-2b (3b) | gemma4-local (7b) | 7b snippets that fit
    #    0.34 |     0.317      |      0.706        |  15 of 15
    #    0.50 |     0.520      |      0.718        |  15 of 15
    #    0.70 |     0.618      |      0.708        |   6 of 15
    #    0.80 |     0.581      |      0.428        |   4 of 15
    #
    # The 7b column is flat to 0.7 and then falls off a cliff: -0.28, about 4.7x
    # the measured 0.06 detection threshold. It survived 0.7 only because this
    # corpus ranks well enough that the answer was inside the six surviving
    # snippets; a corpus that ranks worse would not be so lucky. A single global
    # 0.7 would therefore have been one step from a collapse it could not see.
    #
    # So: the small class takes its measured optimum, and the large classes keep
    # the value that provably loses no snippets.
    context_snippet_head_share: float = 0.34
    context_snippet_head_share_small: float = 0.7

    # Effective context ceiling for the 3b_local class, before
    # compute_context_budget subtracts the system prompt, output reserve and
    # query overhead. 4,000 -> a 2,520 token budget.
    #
    # A setting because after the head-share fix the small class uses 93.5% of
    # that budget, so this is what now limits it - but raising it is NOT free.
    # gemma2-2b truncates its prompt at a measured 4,099 tokens, head first,
    # silently, and head-first truncation costs the system instructions before
    # it costs evidence. At 4,000 the worst-case prompt is ~3,000 tokens; 5,000
    # would reach ~4,000, still inside that model's cap but with little margin,
    # and the class is a guess from the model NAME - another 3B may have a
    # smaller window.
    #
    # SWEPT 2026-09-03, three builds each, and the answer is NO. 3b_local
    # delivered coverage 0.743 / 0.783 / 0.822 at 4000 / 4500 / 5000 - so
    # +0.079 at best against sd 0.069-0.119, about 1 sd, not significant. The
    # risk is not symmetric with it. Kept as a setting because the sweep is
    # what showed that; do not raise the default without knowing the deployed
    # model's real context length.
    context_ceiling_small: int = 4000

    # ── Parent-window expansion (small-to-big retrieval) ─────────────────────
    # Retrieve on the precise child chunk, then hand the LLM the surrounding
    # window stitched from that file's neighbouring chunks. Ranking still runs
    # on children, so precision is unaffected; only what reaches the model
    # widens.
    #
    # CLAUDE.md section 5 claimed this shipped for FULL_RAG for a long time and
    # it did not exist at all (retracted 2026-09-01). It exists now.
    #
    # Honest about the evidence: on tests/eval/corpus_large at chunk_size=2048
    # there is almost nothing for it to recover - answer coverage is already
    # 0.911 reranker-off and 1.000 reranker-on. It is built for the case that
    # fixture cannot represent, a large heterogeneous corpus where precision
    # pressure forces chunk_size back down and answers start straddling again.
    # Measure before assuming it helps at any given chunk size.
    parent_window_enabled: bool = True
    # Window width as a multiple of chunk_size, centred on the child chunk.
    # 3x at the shipped 2048 is ~6k characters, which _format_snippets' per-
    # snippet token budget will usually truncate - that truncation is the hard
    # ceiling, this is only how much is offered to it.
    parent_window_multiplier: int = 3
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
