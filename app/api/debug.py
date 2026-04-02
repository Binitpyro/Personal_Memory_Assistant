from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings
from app.search.planner import QueryPlanner

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/query-plan")
async def debug_query_plan(q: str):
    """Dev-only: inspect planner routing for tuning (§2.8 IMPLEMENTATION_PLAN)."""
    if not settings.dev_mode:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    planner = QueryPlanner()
    plan = planner.plan(q or "")
    latency_class = "fast" if plan.mode in ("FAST_METADATA", "FAST_PROJECT") else "full_rag"
    return {
        "query": q,
        "mode": plan.mode,
        "intents": plan.intents,
        "latency_class": latency_class,
        "cache_hit": None,
        "dev_mode": True,
    }
