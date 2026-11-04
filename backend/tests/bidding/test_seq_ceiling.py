"""Seqs are reserved in Postgres before Redis hands them out (app.bidding.store), so a
Redis that loses an auction can resume above every seq it could have shown."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis

from app.bidding.settings import load_bidding_settings
from app.bidding.sweeper import close_auction
from app.contract import AuctionSnapshot, auction_key
from tests.bidding.helpers import Stores, assert_high_bid, bid, new_bidder, wait_until_redis_time

SMALL_BLOCK = 3


@pytest.fixture
def small_seq_block(monkeypatch: pytest.MonkeyPatch) -> int:
    """A tiny block, so a few state changes run through several reservations."""
    monkeypatch.setenv("BIDDING_SEQ_BLOCK", str(SMALL_BLOCK))
    load_bidding_settings()
    return SMALL_BLOCK


async def assert_within_ceiling(stores: Stores, auction_id: uuid.UUID) -> None:
    redis_hash = await stores.hash(auction_id)
    row = await stores.auction(auction_id)
    assert int(redis_hash["seq"]) <= int(redis_hash["ceiling"]) <= row["seq_ceiling"]


async def test_bids_carry_on_in_order_past_each_reserved_block(
    small_seq_block: int,
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
) -> None:
    auction = await create_auction(start_price=100)
    bidder = new_bidder("Asha")

    for n in range(1, 11):
        result = await bid(auction.id, bidder, 100 + n)
        assert (result.status, result.seq) == ("accepted", n)
        await assert_within_ceiling(stores, auction.id)

    assert [row["seq"] for row in await stores.bids(auction.id)] == list(range(1, 11))
    await assert_high_bid(stores, auction.id, amount=110, bidder=bidder, seq=10)


async def test_concurrent_bids_past_the_ceiling_keep_unbroken_seqs(
    small_seq_block: int,
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
) -> None:
    auction = await create_auction(start_price=0)

    results = await asyncio.gather(
        *(bid(auction.id, new_bidder(f"Bidder {n}"), n) for n in range(1, 41))
    )

    seqs = sorted(result.seq for result in results if result.status == "accepted")
    assert seqs == list(range(1, len(seqs) + 1))
    assert [row["seq"] for row in await stores.bids(auction.id)] == seqs
    await assert_within_ceiling(stores, auction.id)


async def test_close_reserves_a_seq_when_none_is_left(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    redis: Redis,
) -> None:
    end_at = datetime.now(UTC) + timedelta(seconds=1)
    auction = await create_auction(start_price=100, end_at=end_at)
    winner = new_bidder("Asha")
    assert (await bid(auction.id, winner, 150)).seq == 1
    # Leave Redis no spare seq for the close.
    await redis.hset(auction_key(auction.id), "ceiling", "1")
    await wait_until_redis_time(redis, end_at)

    await close_auction(auction.id)

    row = await stores.auction(auction.id)
    assert (row["status"], row["high_seq"]) == ("closed", 2)
    assert row["seq_ceiling"] >= 2
    await assert_within_ceiling(stores, auction.id)
