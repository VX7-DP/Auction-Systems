"""When Postgres won't take a bid Redis accepted: answer "error", pause the auction, and
reopen it from Postgres once writes work again (CLAUDE.md section 4)."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

import httpx
import pytest
from redis.asyncio import Redis

from app.bidding import scripts, store
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
    poll_until,
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


async def test_pause_event_keeps_the_last_saved_high_bid(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    client: httpx.AsyncClient,
    listen: Listen,
    hold_bid_insert: HoldBidInsert,
) -> None:
    """Asha's bid was never saved and she was told so. Viewers must not see her as the
    high bidder while the auction is paused: Ben's saved bid still leads."""
    auction = await create_auction(start_price=100)
    ben = new_bidder("Ben")
    saved = await bid(auction.id, ben, 120)
    assert saved == accepted(saved.bid_id, 1)

    async with listen(auction.id) as events:
        failed = await _bid_that_postgres_never_takes(
            hold_bid_insert, auction.id, new_bidder("Asha"), 150
        )
        assert failed.status == "error"
        paused = await events.next()

    assert isinstance(paused, AuctionUpdate) and paused.status == "paused"
    assert (paused.high_amount, paused.high_bidder_id) == (120, ben.id)
    response = await client.get(f"/api/auctions/{auction.id}")
    snapshot = AuctionSnapshot.model_validate(response.json())
    assert (snapshot.status, snapshot.high_amount, snapshot.high_bidder_id) == (
        "paused",
        120,
        ben.id,
    )


async def test_a_bid_decided_as_the_auction_reopens_becomes_the_high_bid(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    listen: Listen,
    hold_bid_insert: HoldBidInsert,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ben's bid is decided the moment the sweeper's reload reopens the auction, before
    the reopen is announced. His saved bid must become the high bid in Postgres, and the
    reopen event must not show it before it is saved."""
    from app.bidding.sweeper import sweep_once

    auction = await create_auction(start_price=100)
    asha, ben = new_bidder("Asha"), new_bidder("Ben")
    saved = await bid(auction.id, asha, 120)
    assert saved == accepted(saved.bid_id, 1)
    failed = await _bid_that_postgres_never_takes(
        hold_bid_insert, auction.id, new_bidder("Kenji"), 150
    )
    assert failed.status == "error"

    ben_bid_id = uuid.uuid4()
    release = asyncio.Event()
    real_save_bid = store.save_bid

    async def save_bid_after_release(*args: object, **kwargs: object) -> None:
        if args[3].bid_id == ben_bid_id:  # type: ignore[attr-defined]
            await release.wait()
        await real_save_bid(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "save_bid", save_bid_after_release)

    real_load = scripts.LOAD
    ben_task: list[asyncio.Task[BidResult]] = []

    async def load_then_decide_ben(redis: Redis, keys: list[str], args: list[str]) -> object:
        result = await real_load(redis, keys, args)
        if not ben_task:
            ben_task.append(asyncio.create_task(bid(auction.id, ben, 200, bid_id=ben_bid_id)))

            async def decided() -> bool:
                return (await stores.hash(auction.id)).get("high_amount") == "200"

            await poll_until(decided, task=ben_task[0], what="Redis to accept Ben's bid")
        return result

    monkeypatch.setattr(scripts, "LOAD", load_then_decide_ben)

    async with listen(auction.id) as events:
        await sweep_once()
        release.set()
        result = await ben_task[0]
        published = await events.drain(wait=0.5)

    assert result.status == "accepted" and result.seq is not None
    row = await stores.auction(auction.id)
    assert (row["high_amount"], row["high_bidder_name"]) == (200, "Ben")
    await assert_high_bid(stores, auction.id, amount=200, bidder=ben, seq=result.seq)
    reopened = [e for e in published if isinstance(e, AuctionUpdate) and e.seq < result.seq]
    assert [(e.status, e.high_amount) for e in reopened] == [("open", 120)]
