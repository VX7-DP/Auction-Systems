from fastapi import APIRouter

from app.contract import MetricsSnapshot
from app.metrics import recorder

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("")
async def read_metrics() -> MetricsSnapshot:
    """Bid counts and latency percentiles for this server instance since it started.
    Public: no sign-in."""
    return recorder.current().snapshot()
