"""A retried bid_id returns the original result (CLAUDE.md section 4, idempotent bids)."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

import pytest

from app.bidding import store
from app.bidding.settings import get_bidding_settings
from app.contract import AuctionSnapshot
from tests.bidding.helpers import (
    EventReader,
    Stores,
    accepted,
    assert_high_bid,
    bid,
    new_bidder,
    poll_until,
    rejected,
)

Listen = Callable[[uuid.UUID], AbstractAsyncContextManager[EventReader]]


async def test_retried_bid_id_returns_the_original_result(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], stores: Stores
) -> None:
    auction = await create_auction(start_price=100)
    asha = new_bidder("Asha")
    bid_id = uuid.uuid4()

    first = await bid(auction.id, asha, 150, bid_id=bid_id)
    retry = await bid(auction.id, asha, 150, bid_id=bid_id)

    assert first == accepted(bid_id, 1)
    assert retry == first
    assert [(row["bid_id"], row["seq"]) for row in await stores.bids(auction.id)] == [(bid_id, 1)]
    assert (await stores.hash(auction.id))["seq"] == "1"  # the retry used no seq


async def test_retry_with_a_changed_amount_cannot_change_the_bid(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], stores: Stores
) -> None:
    auction = await create_auction(start_price=100)
    asha, ben = new_bidder("Asha"), new_bidder("Ben")
    bid_id = uuid.uuid4()

    first = await bid(auction.id, asha, 150, bid_id=bid_id)
    retry = await bid(auction.id, asha, 999, bid_id=bid_id)

    assert retry == first == accepted(bid_id, 1)
    assert [row["amount"] for row in await stores.bids(auction.id)] == [150]
    # The high is still 150, so 151 wins.
    next_bid = await bid(auction.id, ben, 151)
    assert next_bid == accepted(next_bid.bid_id, 2)
    await assert_high_bid(stores, auction.id, amount=151, bidder=ben, seq=2)


async def test_concurrent_duplicates_of_one_bid_are_saved_once(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], stores: Stores
) -> None:
    """A double-clicked bid button: the same bid_id arrives ten times at once."""
    auction = await create_auction(start_price=100)
    asha = new_bidder("Asha")
    bid_id = uuid.uuid4()

    results = await asyncio.gather(*(bid(auction.id, asha, 150, bid_id=bid_id) for _ in range(10)))

    assert results == [accepted(bid_id, 1)] * 10
    assert [(row["bid_id"], row["seq"]) for row in await stores.bids(auction.id)] == [(bid_id, 1)]
    await assert_high_bid(stores, auction.id, amount=150, bidder=asha, seq=1)


async def test_retried_bid_id_returns_the_original_result_after_a_redis_restart(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    restart_redis: Callable[[], Awaitable[None]],
) -> None:
    """The bidder missed the answer to a confirmed bid, Redis restarted, and the bidder
    retries. The answer must still be "accepted at seq 1", not a fresh decision that
    would call the bid too low."""
    auction = await create_auction(start_price=100)
    asha, ben = new_bidder("Asha"), new_bidder("Ben")
    bid_id = uuid.uuid4()
    first = await bid(auction.id, asha, 150, bid_id=bid_id)
    second = await bid(auction.id, ben, 200)
    assert (first, second) == (accepted(bid_id, 1), accepted(second.bid_id, 2))

    await restart_redis()
    retry = await bid(auction.id, asha, 150, bid_id=bid_id)

    assert retry == accepted(bid_id, 1)
    assert [row["seq"] for row in await stores.bids(auction.id)] == [1, 2]
    # Redis lost the auction, so its seqs resume at the first load's ceiling.
    block = get_bidding_settings().seq_block
    await assert_high_bid(stores, auction.id, amount=200, bidder=ben, seq=2, redis_seq=block)


async def test_a_bid_id_from_another_auction_does_not_pause_or_show_on_this_one(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    listen: Listen,
) -> None:
    """bid_id is unique across all auctions. Sending one already used on auction A to
    auction B must not pause B, nor show the bid that Postgres refused as B's high."""
    first_auction = await create_auction(start_price=100)
    other_auction = await create_auction(start_price=100)
    asha, ben = new_bidder("Asha"), new_bidder("Ben")
    bid_id = uuid.uuid4()
    assert await bid(first_auction.id, asha, 150, bid_id=bid_id) == accepted(bid_id, 1)

    async with listen(other_auction.id) as events:
        reused = await bid(other_auction.id, ben, 150, bid_id=bid_id)
        published = await events.drain(wait=0.5)

    assert reused.status != "accepted"
    assert await stores.bids(other_auction.id) == []
    redis_hash = await stores.hash(other_auction.id)
    assert redis_hash["status"] == "open"
    assert "high_amount" not in redis_hash
    assert [event for event in published if getattr(event, "high_amount", None) == 150] == []


async def test_a_bid_id_reused_on_another_auction_never_blocks_its_bids(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mallory reuses her bid_id from auction A on auction B, with a huge amount. Postgres
    will refuse it, so it must never stand in the way of an honest bid on B, not even
    for the moment before that refusal."""
    first_auction = await create_auction(start_price=100)
    other_auction = await create_auction(start_price=100)
    mallory, ben = new_bidder("Mallory"), new_bidder("Ben")
    reused_id = uuid.uuid4()
    assert await bid(first_auction.id, mallory, 150, bid_id=reused_id) == accepted(reused_id, 1)

    release = asyncio.Event()
    real_save_bid = store.save_bid

    async def save_bid_after_release(*args: object, **kwargs: object) -> None:
        if args[1] == other_auction.id and args[3].bid_id == reused_id:  # type: ignore[attr-defined]
            await release.wait()
        await real_save_bid(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "save_bid", save_bid_after_release)

    huge = 10**12
    reuse = asyncio.create_task(bid(other_auction.id, mallory, huge, bid_id=reused_id))

    async def refused_or_decided() -> bool:
        return reuse.done() or (await stores.hash(other_auction.id)).get("high_amount") == str(huge)

    await poll_until(refused_or_decided, what="the reused bid_id to be decided or refused")
    honest = await bid(other_auction.id, ben, 200)
    release.set()
    reused = await reuse

    assert (honest.status, honest.reason) == ("accepted", None)
    assert reused == rejected(reused_id, "duplicate_bid_id")
    assert honest.seq is not None
    await assert_high_bid(stores, other_auction.id, amount=200, bidder=ben, seq=honest.seq)
