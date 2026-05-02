import logging
import re
from dataclasses import dataclass, field

from app.project_constants import determine_query_intent

logger = logging.getLogger(__name__)

from enum import Enum


class PlanMode(Enum):
    FAST_METADATA = "FAST_METADATA"
    FAST_PROJECT = "FAST_PROJECT"
    FULL_RAG = "FULL_RAG"


@dataclass
class QueryPlan:
    mode: PlanMode
    original_query: str
    intents: dict[str, bool]
    keywords: list[str] = field(default_factory=list)


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
            "give me a summary",
            "show me a summary",
            "index summary",
            "what folders",
            "which folders",
            "folder count",
            "what types of files",
            "file types indexed",
            "extension breakdown",
            "show stats",
            "index stats",
            "storage stats",
            "my index",
            "the index",
        }
    )

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

    # Listing / enumeration phrases — user wants a list, not synthesis ─────────
    _LISTING_PHRASES = frozenset(
        {
            "list all",
            "show all",
            "list my",
            "show my",
            "list files",
            "show files",
            "list folders",
            "show folders",
            "give me a list",
            "what are all",
            "enumerate",
        }
    )

    def plan(self, query: str) -> QueryPlan:
        query_lower = query.lower()
        intents = determine_query_intent(query)
        keywords = self._extract_keywords(query)

        # 1. Exact phrase inventory fast path
        has_inventory_phrase = any(k in query_lower for k in self._INVENTORY_PHRASES)
        # Composite: "how much/many/large/big" + storage/index/file context
        has_composite_inventory = any(
            p in query_lower for p in ("how much", "how many", "how large", "how big")
        ) and any(
            t in query_lower
            for t in (
                "index",
                "storage",
                "file",
                "space",
                "memory",
                "size",
                "data",
                "drive",
                "folder",
            )
        )
        # Listing intent: "list/show/enumerate" + file/folder/index nouns
        has_listing_intent = any(p in query_lower for p in self._LISTING_PHRASES) and any(
            t in query_lower for t in ("file", "folder", "document", "index", "indexed")
        )
        if has_inventory_phrase or has_composite_inventory or has_listing_intent:
            return QueryPlan(
                mode=PlanMode.FAST_METADATA,
                original_query=query,
                intents=intents,
                keywords=keywords,
            )

        # 2. Specific project context fast path
        if intents["metadata_intent"] and (intents["unreal"] or intents["project"]):
            return QueryPlan(
                mode=PlanMode.FAST_PROJECT, original_query=query, intents=intents, keywords=keywords
            )

        # 3. Structural/Architectural hint (Still goes to RAG, but flags intent)
        is_arch = any(p in query_lower for p in self._ARCH_PHRASES)
        intents["architectural"] = is_arch

        # 4. Default to full generative RAG pipeline
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
