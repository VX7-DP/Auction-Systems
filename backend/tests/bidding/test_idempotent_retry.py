"""A retried bid_id returns the original result (CLAUDE.md section 4, idempotent bids)."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from app.contract import AuctionSnapshot
from tests.bidding.helpers import Stores, accepted, assert_high_bid, bid, new_bidder


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
    await assert_high_bid(stores, auction.id, amount=200, bidder=ben, seq=2)
