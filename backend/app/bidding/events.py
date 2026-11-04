"""Publishing auction events to Redis Pub/Sub, after Postgres has committed them."""

import logging
import uuid

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.bidding.store import from_ms
from app.contract import (
    AuctionClosed,
    AuctionStatus,
    AuctionUpdate,
    auction_events_channel,
    auction_key,
)

logger = logging.getLogger(__name__)


async def publish(redis: Redis, event: AuctionUpdate | AuctionClosed) -> None:
    """Best effort: the change is already saved, and a client that misses an event
    sees a seq gap and fetches a snapshot."""
    try:
        await redis.publish(auction_events_channel(event.auction_id), event.model_dump_json())
    except RedisError:
        logger.warning("Could not publish seq %s of auction %s", event.seq, event.auction_id)


def update_from_hash(auction_id: uuid.UUID, state: dict[str, str]) -> AuctionUpdate:
    """The auction_update for the state held in the auction's Redis hash."""
    high_amount = state.get("high_amount")
    status: AuctionStatus = state["status"]  # type: ignore[assignment]
    return AuctionUpdate(
        auction_id=auction_id,
        seq=int(state["seq"]),
        high_amount=int(high_amount) if high_amount is not None else None,
        high_bidder_id=uuid.UUID(state["high_bidder_id"]) if high_amount is not None else None,
        high_bidder_name=state.get("high_bidder_name"),
        is_bot=state.get("high_is_bot") == "1" if high_amount is not None else None,
        end_at=from_ms(int(state["end_at_ms"])),
        status=status,
    )


async def publish_current_state(redis: Redis, auction_id: uuid.UUID) -> None:
    """Publishes the auction's state as Redis holds it now (for pause and unpause)."""
    try:
        state = await redis.hgetall(auction_key(auction_id))
    except RedisError:
        logger.warning("Could not read auction %s to publish it", auction_id)
        return
    if state:
        await publish(redis, update_from_hash(auction_id, state))
