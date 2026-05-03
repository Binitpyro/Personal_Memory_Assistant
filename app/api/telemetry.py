from fastapi import APIRouter

from app.utils.metrics import metrics_tracker

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/metrics")
async def get_metrics():
    """Returns sub-stage latencies from the in-memory metric tracker."""
    stats = metrics_tracker.get_stats()
    return {"status": "ok", "metrics": stats}
