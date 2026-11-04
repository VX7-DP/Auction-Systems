"""Reads an auction's live state from its Redis hash (CLAUDE.md section 10).

The bid engine writes ``auction:{id}`` with the encodings section 10 pins. This reader
also tolerates the obvious variants:

- ``status``: ``open``, ``paused`` or ``closed``.
- ``end_at_ms``, ``start_price``, ``seq``, ``high_amount``: base-10 integers;
  ``end_at_ms`` is milliseconds since the Unix epoch.
- ``high_bidder_id``: a UUID. ``high_bidder_name``: text.
- ``high_is_bot``: ``1`` or ``0`` (``true`` and ``false`` are accepted too).
- A ``high_*`` field that is missing or empty means there is no high bid yet.

A hash that is missing (Redis restarted, auction not reloaded yet) or unreadable
reads as None, and callers fall back to Postgres, the source of truth.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.contract import AuctionStatus, auction_key

logger = logging.getLogger(__name__)

# A Redis that hangs must not hang the page: after this, Postgres answers instead.
REDIS_READ_TIMEOUT_SECONDS = 1.0


def _blank_to_none(value: Any) -> Any:
    return None if value == "" else value


class LiveState(BaseModel):
    """One auction's hash. Lax mode parses Redis's strings into ints, bools and UUIDs."""

    status: AuctionStatus
    end_at_ms: int
    start_price: int
    seq: int
    high_amount: Annotated[int | None, BeforeValidator(_blank_to_none)] = None
    high_bidder_id: Annotated[UUID | None, BeforeValidator(_blank_to_none)] = None
    high_bidder_name: Annotated[str | None, BeforeValidator(_blank_to_none)] = None
    high_is_bot: Annotated[bool | None, BeforeValidator(_blank_to_none)] = None

    @property
    def end_at(self) -> datetime:
        seconds, milliseconds = divmod(self.end_at_ms, 1000)
        return datetime.fromtimestamp(seconds, UTC) + timedelta(milliseconds=milliseconds)


def parse_live_state(fields: Mapping[str, str]) -> LiveState | None:
    """Parse an HGETALL reply. Empty means the key is missing."""
    if not fields:
        return None
    try:
        return LiveState.model_validate(fields)
    except ValidationError:
        logger.error("Unreadable Redis auction hash, using Postgres instead: %r", dict(fields))
        return None


async def read_live_states(redis: Redis, auction_ids: Sequence[UUID]) -> dict[UUID, LiveState]:
    """Live state for each auction whose hash is present and readable, in one round trip.

    On a Redis error or timeout it returns nothing, so every auction falls back to Postgres.
    """
    if not auction_ids:
        return {}
    try:
        async with asyncio.timeout(REDIS_READ_TIMEOUT_SECONDS):
            async with redis.pipeline(transaction=False) as pipe:
                for auction_id in auction_ids:
                    pipe.hgetall(auction_key(auction_id))
                replies = await pipe.execute()
    except (RedisError, OSError, TimeoutError):
        logger.warning("Redis unavailable for live auction state, using Postgres", exc_info=True)
        return {}
    states: dict[UUID, LiveState] = {}
    for auction_id, fields in zip(auction_ids, replies, strict=True):
        state = parse_live_state(fields)
        if state is not None:
            states[auction_id] = state
    return states
