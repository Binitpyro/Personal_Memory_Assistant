import logging
import re
from dataclasses import dataclass, field
from enum import Enum

from app.project_constants import determine_query_intent

logger = logging.getLogger(__name__)


class PlanMode(Enum):
    FAST_METADATA = "FAST_METADATA"
    FAST_PROJECT = "FAST_PROJECT"
    FULL_RAG = "FULL_RAG"
    GRAPH_SEARCH = "GRAPH_SEARCH"


@dataclass
class QueryPlan:
    mode: PlanMode
    original_query: str
    intents: dict[str, bool]
    keywords: list[str] = field(default_factory=list)


@dataclass
class GraphSearchPlan(QueryPlan):
    pass


class QueryPlanner:
    """
    Analyzes user queries to route them to the most efficient retrieval pipeline.
    Prevents triggering expensive cross-encoder/LLM calls for simple known-state queries.
    """

    # Inventory / size / count phrases ─────────────────────────────────────────
    # Covers natural phrasings that signal "tell me about stored metadata" rather
    # than "search for content" — these go to FAST_METADATA, skipping the LLM.
    _INVENTORY_PHRASES = frozenset(
        {
            # quantity / size originals
            "how many files",
            "total size",
            "disk space",
            "storage used",
            "how much is indexed",
            "how much storage",
            "index size",
            "how large",
            "file count",
            "how much space",
            "total indexed",
            "what's in my index",
            "whats in my index",
            "how big is",
            "storage usage",
            "space used",
            # contractions and possessives
            "what is in my index",
            "what is indexed",
            "what's indexed",
            "what have i indexed",
            "what did i index",
            "how many items",
            "item count",
            # drive / folder context
            "how much of my drive",
            "drive usage",
            "how full",
            "how much data",
            "data indexed",
            "indexed data",
            "folder size",
            "directory size",
            # descriptive inventory
            "index summary",
            "folder count",
            "what types of files",
            "file types indexed",
            "extension breakdown",
            "show stats",
            "index stats",
            "storage stats",
            # Removed - these matched as bare substrings and hijacked content
            # questions into a canned file-count string the user never asked
            # for, or silently stripped the reranker and the bounded loop:
            #   "my index" / "the index"  - how a person refers to this tool by
            #       name, so "what does the index say about turbulence" routed
            #       to FAST_METADATA.
            #   "give me a summary" / "show me a summary" - "give me a summary
            #       of my thesis notes" is a content question.
            #   "what folders" / "which folders" - "which folders have the most
            #       physics notes" needs retrieval, not a folder listing.
            # Inventory intent is now required to look like inventory: see
            # _has_inventory_intent below.
        }
    )

    # An inventory question names a thing being counted *and* asks about its
    # extent. Requiring both kills the bare-substring hijacks above without
    # maintaining an ever-growing phrase list.
    # "space" and "data" are deliberately absent: they are ordinary content
    # words, and with "how much" they turned "how much space does the renderer
    # use in the frame budget" into a file count. The literal phrases "how much
    # space" / "disk space" still live in _INVENTORY_PHRASES.
    _INVENTORY_NOUNS = frozenset(
        {
            "file",
            "files",
            "folder",
            "folders",
            "index",
            "storage",
            "disk",
            "drive",
            "document",
            "documents",
            "item",
            "items",
            "indexed",
            "pdf",
            "pdfs",
            "pma",
            "collection",
        }
    )

    # Negative evidence. An inventory phrase followed by a topic is a content
    # question about the corpus, not a question about the corpus's size:
    # "what's in my index about kinetics" is not "what's in my index".
    # A marker counts only when a topic follows it: "what does it know about?"
    # names no topic, "what's in my index about kinetics" does.
    _TOPIC_RE = re.compile(r"\b(?:about|regarding|concerning|mention\w*|related to)\s+\w")

    # Negative evidence of a different kind: the measure is applied to something
    # that is not the indexed collection - "how many files does *the build
    # script* copy", "how big is *the moon*". Any determiner phrase after a
    # verb or preposition whose head is not a corpus noun marks a content
    # question. Without this, letting FAST_METADATA load stats (A3-08) would
    # turn every such hijack into a live canned file count.
    _EXTERNAL_SUBJECT_RE = re.compile(
        r"\b(?:in|of|for|from|by|on|does|do|did|is|are|was|were)\s+"
        r"(?:the|a|an|this|that|these|those|my|your|his|her|their|its|our)\s+(\w+)"
    )
    _CORPUS_NOUNS = frozenset(
        {
            "index",
            "indexed",
            "library",
            "collection",
            "corpus",
            "pma",
            "storage",
            "disk",
            "drive",
            "file",
            "files",
            "folder",
            "folders",
            "document",
            "documents",
            "project",
            "projects",
            "workspace",
            "repos",
            "codebase",
            "codebases",
        }
    )
    _INVENTORY_MEASURES = frozenset(
        {
            "how many",
            "how much",
            "how large",
            "how big",
            "how full",
            "count",
            "total",
            "size",
            "usage",
            "stats",
            "breakdown",
            "capacity",
            "kinds",
            "types",
            "extensions",
            "formats",
        }
    )
    # Ownership comparisons carry no measure word: "do I have more PDFs or docs".
    _OWNERSHIP_RE = re.compile(r"\bdo i have (?:more|any|fewer|many)\b")
    # "How many documents are in the Physics folder?" is inventory, but per folder:
    # the canned total would be wrong, while file_stats carries per-folder counts,
    # so it gets the stats and goes to RAG.
    _FOLDER_SCOPE_RE = re.compile(r"\b(?:in|of|under|inside)\s+(?:the|my)\s+\w+\s+folder\b")

    # Architectural / Structural phrases ──────────────────────────────────────
    _ARCH_PHRASES = frozenset(
        {
            "how does this work",
            "how it works",
            "overall structure",
            "project structure",
            "codebase structure",
            "architectural overview",
            "system overview",
            "module relationship",
            "package structure",
            "how are modules",
            "high level",
            "big picture",
            "folder structure",
            "logic flow",
            "data flow",
        }
    )

    # Listing / enumeration — user wants a list, not synthesis ───────────────
    # The corpus noun must be the object of the verb: "list all indexed
    # folders", not "show my notes on index funds".
    _LISTING_RE = re.compile(
        r"\b(?:list|show|enumerate|give me a list of|what are)\s+(?:(?:all|my|the|of)\s+)*"
        # A bare "indexed" ends the object; "my indexed projects" is not a file listing.
        r"(?:indexed\s+)?(?:files|folders|documents|indexed\b(?!\s+\w))\b"
    )

    def plan(self, query: str) -> QueryPlan:
        query_lower = query.lower()
        intents = determine_query_intent(query)
        keywords = self._extract_keywords(query)

        # 1. Exact phrase inventory fast path
        has_inventory_phrase = any(k in query_lower for k in self._INVENTORY_PHRASES)
        # Composite: an extent measure *and* a thing being measured. Both are
        # required. Either alone matched content questions - "how much space
        # does the renderer use in the frame budget" answered with a canned
        # file count, because "how much" and "space" both appeared.
        words = set(re.findall(r"\w+", query_lower))
        has_composite_inventory = bool(words & self._INVENTORY_NOUNS) and (
            any(m in query_lower if " " in m else m in words for m in self._INVENTORY_MEASURES)
            or bool(self._OWNERSHIP_RE.search(query_lower))
        )
        has_listing_intent = bool(self._LISTING_RE.search(query_lower))
        names_a_topic = bool(self._TOPIC_RE.search(query_lower))
        names_external_subject = any(
            w not in self._CORPUS_NOUNS for w in self._EXTERNAL_SUBJECT_RE.findall(query_lower)
        )
        # Newest/largest questions want a file list, which the canned count is not.
        wants_file_list = intents["latest"] or intents["largest"]
        if (
            (has_inventory_phrase or has_composite_inventory or has_listing_intent)
            and not names_a_topic
            and not names_external_subject
            and not wants_file_list
        ):
            # A3-08: file stats load only when intents["inventory"] is set, and
            # determine_query_intent knew a narrower set of phrases than this
            # router - so most inventory questions reached FAST_METADATA with no
            # stats and fell through to RAG. The route is now the one decision.
            intents["inventory"] = True
            return QueryPlan(
                mode=PlanMode.FAST_METADATA,
                original_query=query,
                intents=intents,
                keywords=keywords,
            )

        if (
            (has_inventory_phrase or has_composite_inventory)
            and not names_a_topic
            and self._FOLDER_SCOPE_RE.search(query_lower)
        ):
            intents["inventory"] = True  # per-folder counts live in file_stats

        # 2. Specific project context fast path
        if intents["metadata_intent"] and intents["project"] and not names_external_subject:
            return QueryPlan(
                mode=PlanMode.FAST_PROJECT, original_query=query, intents=intents, keywords=keywords
            )

        # 3. Structural/Architectural hint (Still goes to RAG, but flags intent)
        is_arch = any(p in query_lower for p in self._ARCH_PHRASES)
        intents["architectural"] = is_arch

        # 4. Graph Intent
        if intents.get("graph", False):
            return GraphSearchPlan(
                mode=PlanMode.GRAPH_SEARCH, original_query=query, intents=intents, keywords=keywords
            )

        # 5. Default to full generative RAG pipeline
        return QueryPlan(
            mode=PlanMode.FULL_RAG, original_query=query, intents=intents, keywords=keywords
        )

    # Common English stop-words to skip when extracting query keywords
    _STOP_WORDS = frozenset(
        {
            "a",
            "an",
            "the",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "shall",
            "can",
            "need",
            "dare",
            "ought",
            "used",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "and",
            "or",
            "but",
            "if",
            "as",
            "i",
            "my",
            "me",
            "what",
            "how",
            "where",
            "when",
            "who",
            "which",
            "that",
            "this",
            "it",
            "its",
        }
    )

    def _extract_keywords(self, query: str) -> list[str]:
        """Extract meaningful terms from a query, stripping stop-words."""
        tokens = re.findall(r"\w+", query.lower())
        return [t for t in tokens if t not in self._STOP_WORDS and len(t) > 2]
