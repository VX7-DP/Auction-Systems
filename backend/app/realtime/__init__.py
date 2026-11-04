"""WebSocket fan-out over Redis Pub/Sub. Owned by session 2 (CLAUDE.md section 15)."""

from app.realtime.lifespan import lifespan
from app.realtime.router import router

__all__ = ["lifespan", "router"]
