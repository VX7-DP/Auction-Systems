"""Bid engine. Owned by session 1 (CLAUDE.md section 15).

Lua CAS, synchronous dual-write to Postgres, pause on a failed insert, reload on
NOT_LOADED, idempotent bid_id, publish after commit, and the close sweeper.
"""

from app.bidding.lifespan import lifespan
from app.bidding.router import router

__all__ = ["lifespan", "router"]
