import logging
from dataclasses import dataclass
from typing import Dict
from app.project_constants import determine_query_intent

logger = logging.getLogger(__name__)

class PlanMode:
    FAST_METADATA = "FAST_METADATA"
    FAST_PROJECT = "FAST_PROJECT"
    FULL_RAG = "FULL_RAG"

@dataclass
class QueryPlan:
    mode: str
    original_query: str
    intents: Dict[str, bool]

class QueryPlanner:
    """
    Analyzes user queries to route them to the most efficient retrieval pipeline.
    Prevents triggering expensive cross-encoder/LLM calls for simple known-state queries.
    """
    
    def plan(self, query: str) -> QueryPlan:
        query_lower = query.lower()
        intents = determine_query_intent(query)
        
        # 1. Very specific inventory fast path
        has_inventory_keywords = any(k in query_lower for k in ["how many files", "total size", "disk space"])
        if has_inventory_keywords:
            return QueryPlan(mode=PlanMode.FAST_METADATA, original_query=query, intents=intents)
            
        # 2. Specific project context fast path
        if intents["metadata_intent"] and (intents["unreal"] or intents["project"]):
            return QueryPlan(mode=PlanMode.FAST_PROJECT, original_query=query, intents=intents)
            
        # 3. Default to full generative RAG pipeline
        return QueryPlan(mode=PlanMode.FULL_RAG, original_query=query, intents=intents)
