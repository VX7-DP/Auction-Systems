"""When Postgres won't take a bid Redis accepted: answer "error", pause the auction, and
reopen it from Postgres once writes work again (CLAUDE.md section 4)."""

import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from app.contract import AuctionSnapshot, AuctionUpdate, BidResult
from tests.bidding.helpers import (
    Bidder,
    EventReader,
    HeldInsert,
    Stores,
    accepted,
    assert_high_bid,
    bid,
    new_bidder,
    rejected,
)

Listen = Callable[[uuid.UUID], AbstractAsyncContextManager[EventReader]]
HoldBidInsert = Callable[[uuid.UUID, uuid.UUID], AbstractAsyncContextManager[HeldInsert]]


async def _bid_that_postgres_never_takes(
    hold_bid_insert: HoldBidInsert, auction_id: uuid.UUID, bidder: Bidder, amount: int
) -> BidResult:
    """Holds the bid's row for longer than every attempt to save it."""
    bid_id = uuid.uuid4()
    async with hold_bid_insert(auction_id, bid_id):
        return await bid(auction_id, bidder, amount, bid_id=bid_id)


async def test_failed_save_answers_error_and_pauses_the_auction(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    listen: Listen,
    hold_bid_insert: HoldBidInsert,
) -> None:
    auction = await create_auction(start_price=100)

    async with listen(auction.id) as events:
        failed = await _bid_that_postgres_never_takes(
            hold_bid_insert, auction.id, new_bidder("Asha"), 150
        )

        assert failed == BidResult(bid_id=failed.bid_id, status="error")
        assert await stores.bids(auction.id) == []
        assert (await stores.hash(auction.id))["status"] == "paused"
        paused = await events.next()
        assert isinstance(paused, AuctionUpdate) and paused.status == "paused"

    blocked = await bid(auction.id, new_bidder("Ben"), 200)
    assert blocked == rejected(blocked.bid_id, "paused")


async def test_paused_auction_reopens_from_postgres_once_writes_work_again(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    listen: Listen,
    hold_bid_insert: HoldBidInsert,
) -> None:
    from app.bidding.sweeper import sweep_once

    auction = await create_auction(start_price=100)
    ben = new_bidder("Ben")

    async with listen(auction.id) as events:
        failed = await _bid_that_postgres_never_takes(
            hold_bid_insert, auction.id, new_bidder("Asha"), 150
        )
        assert failed.status == "error"
        paused = await events.next()
        epoch_while_paused = int((await stores.hash(auction.id))["epoch"])

        await sweep_once()  # the hold is gone, so Postgres takes writes again

        reopened = await events.next()
        assert isinstance(reopened, AuctionUpdate) and reopened.status == "open"
        assert reopened.seq > paused.seq  # seq never goes backwards for viewers
        assert reopened.high_amount is None  # the unsaved bid of 150 is gone
        reloaded = await stores.hash(auction.id)
        assert reloaded["status"] == "open"
        assert int(reloaded["epoch"]) > epoch_while_paused

        next_bid = await bid(auction.id, ben, 120)
        assert next_bid.status == "accepted" and next_bid.seq is not None
        assert next_bid.seq > reopened.seq
        assert (await events.next()).seq == next_bid.seq

    assert [row["bid_id"] for row in await stores.bids(auction.id)] == [next_bid.bid_id]
    await assert_high_bid(stores, auction.id, amount=120, bidder=ben, seq=next_bid.seq)


async def test_failed_bid_retried_with_its_bid_id_is_saved_as_first_decided(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    hold_bid_insert: HoldBidInsert,
) -> None:
    """The bidder retries after the error, once Postgres is back. The retry's body says
    999, but the bid was already decided at 150 and seq 1, so that is what gets saved."""
    auction = await create_auction(start_price=100)
    asha = new_bidder("Asha")
    failed = await _bid_that_postgres_never_takes(hold_bid_insert, auction.id, asha, 150)
    assert failed.status == "error"

    retry = await bid(auction.id, asha, 999, bid_id=failed.bid_id)

    assert retry == accepted(failed.bid_id, 1)
    assert [
        (row["bid_id"], row["seq"], row["amount"]) for row in await stores.bids(auction.id)
    ] == [(failed.bid_id, 1, 150)]
