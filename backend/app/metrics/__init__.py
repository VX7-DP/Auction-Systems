"""Public metrics: app-level counters. Owned by session 6 (CLAUDE.md section 15)."""

from app.metrics.lifespan import lifespan
from app.metrics.recorder import record_bid
from app.metrics.router import router

__all__ = ["lifespan", "record_bid", "router"]
