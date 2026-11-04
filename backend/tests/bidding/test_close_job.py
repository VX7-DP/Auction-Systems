"""The app runs the close sweeper by itself, behind its job lock (CLAUDE.md sections 4, 5).

Background jobs are on in this module only. Other modules' jobs (such as session 2's
simulated bidders) may run too, so this checks only that the auction gets closed.
"""

from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from redis.asyncio import Redis

from app.config import Settings
from app.contract import AuctionSnapshot, job_lock_key
from tests.bidding.helpers import Stores, poll_until, redis_now, wait_until_redis_time


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(run_background_jobs=True)


async def test_app_closes_a_due_auction_by_itself(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], stores: Stores, redis: Redis
) -> None:
    auction = await create_auction(
        start_price=100, end_at=await redis_now(redis) + timedelta(seconds=1)
    )

    async def lock_held() -> bool:
        return bool(await redis.exists(job_lock_key("close_sweeper")))

    await poll_until(lock_held, what="the close sweeper to take its job lock")

    await wait_until_redis_time(redis, auction.end_at)

    async def closed() -> bool:
        return (await stores.auction(auction.id))["status"] == "closed"

    await poll_until(closed, what="the sweeper to close the auction", within=5.0)
    assert (await stores.hash(auction.id))["status"] == "closed"
