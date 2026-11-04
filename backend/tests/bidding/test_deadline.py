"""Bids are rejected once Redis TIME reaches end_at (CLAUDE.md section 4, auction close)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

from redis.asyncio import Redis

from app.contract import AuctionSnapshot
from tests.bidding.helpers import (
    Stores,
    accepted,
    assert_high_bid,
    bid,
    new_bidder,
    redis_now,
    rejected,
    wait_until_redis_time,
)


async def test_bids_are_rejected_once_end_at_passes(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], stores: Stores, redis: Redis
) -> None:
    auction = await create_auction(
        start_price=100, end_at=await redis_now(redis) + timedelta(seconds=1)
    )
    asha, ben = new_bidder("Asha"), new_bidder("Ben")
    before = await bid(auction.id, asha, 150)
    assert before == accepted(before.bid_id, 1)

    # No sweeper runs in this test: the bid script alone must stop the bid.
    await wait_until_redis_time(redis, auction.end_at)
    after = await bid(auction.id, ben, 200)

    assert after == rejected(after.bid_id, "closed")
    assert [row["bid_id"] for row in await stores.bids(auction.id)] == [before.bid_id]
    await assert_high_bid(stores, auction.id, amount=150, bidder=asha, seq=1)


async def test_auction_past_end_at_rejects_bids_when_loaded_from_postgres(
    insert_auction: Callable[..., Awaitable[uuid.UUID]], stores: Stores, redis: Redis
) -> None:
    """Postgres still says "open" (the sweeper hasn't closed it yet) and Redis has
    never loaded it, as after a restart. The deadline still rules."""
    auction_id = await insert_auction(
        start_price=100, end_at=await redis_now(redis) - timedelta(seconds=1)
    )

    late = await bid(auction_id, new_bidder("Asha"), 150)

    assert late == rejected(late.bid_id, "closed")
    assert await stores.bids(auction_id) == []
