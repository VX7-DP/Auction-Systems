"""The close sweeper: "close if open and past end_at", idempotent (CLAUDE.md section 4).

These call one sweep directly; test_close_job.py checks that the app runs it by itself.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta

import pytest
from redis.asyncio import Redis

from app.bidding import store
from app.contract import AuctionClosed, AuctionSnapshot
from tests.bidding.helpers import (
    EventReader,
    Stores,
    accepted,
    bid,
    new_bidder,
    poll_until,
    redis_now,
    rejected,
    wait_until_redis_time,
)

Listen = Callable[[uuid.UUID], AbstractAsyncContextManager[EventReader]]


async def _auction_ending_soon(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], redis: Redis
) -> AuctionSnapshot:
    return await create_auction(
        start_price=100, end_at=await redis_now(redis) + timedelta(seconds=1)
    )


async def test_sweep_closes_a_due_auction_and_announces_the_winner(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    listen: Listen,
    redis: Redis,
) -> None:
    from app.bidding.sweeper import sweep_once

    auction = await _auction_ending_soon(create_auction, redis)
    asha, ben = new_bidder("Asha"), new_bidder("Ben")
    assert (await bid(auction.id, asha, 150)).status == "accepted"
    assert (await bid(auction.id, ben, 200)).status == "accepted"
    await wait_until_redis_time(redis, auction.end_at)

    async with listen(auction.id) as events:
        await sweep_once()

        assert await events.next() == AuctionClosed(
            auction_id=auction.id, seq=3, winner_id=ben.id, winner_name="Ben", amount=200
        )
        assert await events.drain() == []
    row = await stores.auction(auction.id)
    assert (row["status"], row["high_seq"], row["high_amount"]) == ("closed", 3, 200)
    redis_hash = await stores.hash(auction.id)
    assert (redis_hash["status"], redis_hash["seq"]) == ("closed", "3")
    late = await bid(auction.id, asha, 300)
    assert late == rejected(late.bid_id, "closed")


async def test_sweep_closes_an_auction_without_bids(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    listen: Listen,
    redis: Redis,
) -> None:
    from app.bidding.sweeper import sweep_once

    auction = await _auction_ending_soon(create_auction, redis)
    await wait_until_redis_time(redis, auction.end_at)

    async with listen(auction.id) as events:
        await sweep_once()

        assert await events.next() == AuctionClosed(
            auction_id=auction.id, seq=1, winner_id=None, winner_name=None, amount=None
        )
    row = await stores.auction(auction.id)
    assert (row["status"], row["high_seq"], row["high_amount"]) == ("closed", 1, None)


async def test_closing_twice_changes_nothing(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    listen: Listen,
    redis: Redis,
) -> None:
    from app.bidding.sweeper import sweep_once

    auction = await _auction_ending_soon(create_auction, redis)
    assert (await bid(auction.id, new_bidder("Asha"), 150)).status == "accepted"
    await wait_until_redis_time(redis, auction.end_at)

    async with listen(auction.id) as events:
        await sweep_once()
        closed = await events.next()
        await sweep_once()

        assert await events.drain() == []
    assert closed.seq == 2
    assert (await stores.auction(auction.id))["high_seq"] == 2
    assert (await stores.hash(auction.id))["seq"] == "2"


async def test_sweep_leaves_an_auction_before_its_deadline_open(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    listen: Listen,
) -> None:
    from app.bidding.sweeper import sweep_once

    auction = await create_auction(start_price=100)  # ends in an hour
    first = await bid(auction.id, new_bidder("Asha"), 150)
    assert first == accepted(first.bid_id, 1)

    async with listen(auction.id) as events:
        await sweep_once()

        assert await events.drain() == []
    assert (await stores.auction(auction.id))["status"] == "open"
    redis_hash = await stores.hash(auction.id)
    assert (redis_hash["status"], redis_hash["seq"]) == ("open", "1")


async def test_a_bid_saved_after_the_close_never_loses_to_it(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    listen: Listen,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis accepts Asha's bid before end_at, but the close commits before her save
    does. Whatever happens to her bid, an accepted bid must never lose to the close:
    the winner announced, Postgres and Redis all agree with the highest accepted bid."""
    from app.bidding.sweeper import close_auction

    auction = await _auction_ending_soon(create_auction, redis)
    asha, ben = new_bidder("Asha"), new_bidder("Ben")
    first = await bid(auction.id, ben, 120)
    assert first == accepted(first.bid_id, 1)

    asha_bid_id = uuid.uuid4()
    release = asyncio.Event()
    real_save_bid = store.save_bid

    async def save_bid_after_release(*args: object, **kwargs: object) -> None:
        if args[3].bid_id == asha_bid_id:  # type: ignore[attr-defined]
            await release.wait()
        await real_save_bid(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "save_bid", save_bid_after_release)

    async with listen(auction.id) as events:
        late = asyncio.create_task(bid(auction.id, asha, 150, bid_id=asha_bid_id))

        async def decided() -> bool:
            return (await stores.hash(auction.id)).get("high_amount") == "150"

        await poll_until(decided, task=late, what="Redis to accept Asha's bid")
        await wait_until_redis_time(redis, auction.end_at)
        await close_auction(auction.id)
        release.set()
        result = await late
        published = await events.drain(wait=0.5)

    accepted_amounts = [120] + ([150] if result.status == "accepted" else [])
    highest = max(accepted_amounts)
    closes = [event for event in published if isinstance(event, AuctionClosed)]
    assert [event.amount for event in closes] == [highest]
    row = await stores.auction(auction.id)
    assert (row["status"], row["high_amount"]) == ("closed", highest)
    redis_hash = await stores.hash(auction.id)
    assert (redis_hash["status"], redis_hash["high_amount"]) == ("closed", str(highest))
