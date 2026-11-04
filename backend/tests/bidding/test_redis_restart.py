"""A Redis restart mid-auction loses no confirmed bid and keeps the right high bid
(CLAUDE.md section 7, Stage 1 done-signal; section 4, reload and load_epoch fence)."""

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.contract import AuctionSnapshot, BidResult
from tests.bidding.helpers import (
    Bidder,
    HeldInsert,
    Stores,
    accepted,
    assert_high_bid,
    bid,
    new_bidder,
    rejected,
    to_ms,
)

HoldBidInsert = Callable[[uuid.UUID, uuid.UUID], AbstractAsyncContextManager[HeldInsert]]


async def test_redis_restart_keeps_every_confirmed_bid_and_the_high_bid(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    restart_redis: Callable[[], Awaitable[None]],
) -> None:
    auction = await create_auction(start_price=100)
    asha, ben, chen = new_bidder("Asha"), new_bidder("Ben"), new_bidder("Chen")
    first = await bid(auction.id, asha, 150)
    second = await bid(auction.id, ben, 200)
    assert (first, second) == (accepted(first.bid_id, 1), accepted(second.bid_id, 2))
    epoch_before = int((await stores.hash(auction.id))["epoch"])

    await restart_redis()

    # Below the high bid but above the start price: rejected only if the high came back.
    low = await bid(auction.id, chen, 180)
    assert low == rejected(low.bid_id, "too_low")
    restored = await stores.hash(auction.id)
    assert restored == {
        "status": "open",
        "start_price": "100",
        "end_at_ms": str(to_ms(auction.end_at)),
        "seq": "2",
        "high_amount": "200",
        "high_bidder_id": str(ben.id),
        "high_bidder_name": "Ben",
        "high_is_bot": "0",
        "epoch": restored.get("epoch"),
    }
    # The reload is a new load: it moved the epoch on, in both stores.
    assert int(restored["epoch"]) > epoch_before
    assert int(restored["epoch"]) == (await stores.auction(auction.id))["load_epoch"]

    third = await bid(auction.id, chen, 250)
    assert third == accepted(third.bid_id, 3)
    assert [
        (row["bid_id"], row["seq"], row["amount"]) for row in await stores.bids(auction.id)
    ] == [
        (first.bid_id, 1, 150),
        (second.bid_id, 2, 200),
        (third.bid_id, 3, 250),
    ]
    await assert_high_bid(stores, auction.id, amount=250, bidder=chen, seq=3)


async def test_bid_being_saved_during_a_redis_restart_is_kept(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    restart_redis: Callable[[], Awaitable[None]],
    hold_bid_insert: HoldBidInsert,
) -> None:
    """Asha's bid is decided in Redis, then Redis restarts while its insert is still
    waiting. Ben's lower bid arrives and reloads the auction from Postgres. The reload
    must wait for Asha's write instead of reading Postgres without it."""
    auction = await create_auction(start_price=100)
    asha, ben = new_bidder("Asha"), new_bidder("Ben")
    asha_bid_id = uuid.uuid4()

    async with hold_bid_insert(auction.id, asha_bid_id) as held:
        asha_task = asyncio.create_task(bid(auction.id, asha, 500, bid_id=asha_bid_id))
        await held.wait_until_blocked(asha_task)
        await restart_redis()
        ben_task = asyncio.create_task(bid(auction.id, ben, 400))
        await asyncio.sleep(0.2)  # Ben's bid is now reloading the auction
    asha_result, ben_result = await asha_task, await ben_task

    assert asha_result == accepted(asha_bid_id, 1)
    assert ben_result == rejected(ben_result.bid_id, "too_low")
    assert [
        (row["bid_id"], row["seq"], row["amount"]) for row in await stores.bids(auction.id)
    ] == [(asha_bid_id, 1, 500)]
    await assert_high_bid(stores, auction.id, amount=500, bidder=asha, seq=1)


async def test_bid_decided_from_an_outdated_load_is_not_saved(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    db_engine: AsyncEngine,
) -> None:
    """Another load has moved Postgres on (load_epoch bumped, then a bid of 300 saved at
    seq 2), but the Redis copy here is still the old one: no bids, seq 0. A bid decided
    from that old copy must be fenced off, not saved."""
    auction = await create_auction(start_price=100)
    dana = new_bidder("Dana")
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO bids (bid_id, auction_id, seq, bidder_id, bidder_name, amount)"
                " VALUES (:bid_id, :auction_id, 2, :bidder_id, :bidder_name, 300)"
            ),
            {
                "bid_id": uuid.uuid4(),
                "auction_id": auction.id,
                "bidder_id": dana.id,
                "bidder_name": dana.name,
            },
        )
        await connection.execute(
            text(
                "UPDATE auctions SET load_epoch = load_epoch + 1, high_amount = 300,"
                " high_bidder_id = :bidder_id, high_bidder_name = :bidder_name, high_seq = 2"
                " WHERE id = :auction_id"
            ),
            {"auction_id": auction.id, "bidder_id": dana.id, "bidder_name": dana.name},
        )

    late = await bid(auction.id, new_bidder("Eve"), 200)

    # Decided again after a reload, 200 loses to the saved 300.
    assert late == rejected(late.bid_id, "too_low")
    assert [row["amount"] for row in await stores.bids(auction.id)] == [300]
    await assert_high_bid(stores, auction.id, amount=300, bidder=dana, seq=2)


async def test_redis_restarts_during_a_bidding_burst_lose_no_confirmed_bid(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    restart_redis: Callable[[], Awaitable[None]],
) -> None:
    """Three waves of 100 concurrent bids. Redis restarts once half of each wave has
    answered, and the next wave starts straight away, so reloads race bids that were
    decided before the restart and are still being saved."""
    auction = await create_auction(start_price=0)
    amounts = list(range(1, 301))
    random.Random(7).shuffle(amounts)
    offers: list[tuple[Bidder, int, asyncio.Task[BidResult]]] = []

    for wave in (amounts[0:100], amounts[100:200], amounts[200:300]):
        tasks = []
        for amount in wave:
            bidder = new_bidder(f"Bidder {amount}")
            task = asyncio.create_task(bid(auction.id, bidder, amount))
            offers.append((bidder, amount, task))
            tasks.append(task)
        for answered, next_answer in enumerate(asyncio.as_completed(tasks), start=1):
            await next_answer
            if answered == 50:
                break
        await restart_redis()
    results = await asyncio.gather(*(task for *_, task in offers))

    # A fenced write may answer "error" (the bidder retries); nothing else may go wrong.
    assert all(
        result.status in ("accepted", "error") or result.reason == "too_low" for result in results
    ), results
    confirmed = {
        result.bid_id: (result.seq, amount, bidder.id)
        for (bidder, amount, _), result in zip(offers, results, strict=True)
        if result.status == "accepted"
    }
    assert confirmed, "no bid was accepted"
    saved = await stores.bids(auction.id)
    saved_by_id = {row["bid_id"]: (row["seq"], row["amount"], row["bidder_id"]) for row in saved}
    # Every confirmed bid is saved exactly as it was confirmed.
    assert {bid_id: saved_by_id.get(bid_id) for bid_id in confirmed} == confirmed
    # Saved bids rise strictly with seq: no restart let a lower bid in after a higher one.
    saved_amounts = [row["amount"] for row in saved]
    assert saved_amounts == sorted(set(saved_amounts))

    # Bidding carries on from where Postgres is, and both stores agree afterwards.
    finisher = new_bidder("Finisher")
    final = await bid(auction.id, finisher, 1000)
    assert final.status == "accepted" and final.seq is not None
    assert final.seq > saved[-1]["seq"]
    await assert_high_bid(stores, auction.id, amount=1000, bidder=finisher, seq=final.seq)
