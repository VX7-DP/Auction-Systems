"""Many bidders racing on one auction: bids land in the right order and every confirmed
bid is saved (CLAUDE.md sections 1 and 4)."""

import asyncio
import random
from collections.abc import Awaitable, Callable

from app.contract import AuctionSnapshot
from tests.bidding.helpers import Stores, assert_high_bid, bid, new_bidder


async def test_concurrent_bids_are_accepted_in_strictly_increasing_order(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], stores: Stores
) -> None:
    auction = await create_auction(start_price=100)
    # Amounts 101..200, each offered by two different bidders, all sent at once in a
    # shuffled order, so equal and lower bids race each other.
    offers = [
        (new_bidder(f"Bidder {amount}-{copy}"), amount)
        for amount in range(101, 201)
        for copy in (1, 2)
    ]
    random.Random(4).shuffle(offers)

    results = await asyncio.gather(*(bid(auction.id, bidder, amount) for bidder, amount in offers))

    assert all(
        result.status == "accepted" or (result.status == "rejected" and result.reason == "too_low")
        for result in results
    ), results
    winners = sorted(
        (
            (result.seq, amount, bidder, result.bid_id)
            for (bidder, amount), result in zip(offers, results, strict=True)
            if result.status == "accepted"
        ),
        key=lambda winner: winner[0],
    )
    # seq counts accepted bids one by one, with no gaps or repeats.
    assert [seq for seq, *_ in winners] == list(range(1, len(winners) + 1))
    # Each accepted bid beats the one before it, so no amount is accepted twice.
    amounts = [amount for _, amount, *_ in winners]
    assert amounts == sorted(set(amounts))
    assert amounts[-1] == 200

    # Exactly the confirmed bids are saved, each with the seq it was confirmed with.
    saved = await stores.bids(auction.id)
    assert [
        (row["seq"], row["bid_id"], row["amount"], row["bidder_id"], row["bidder_name"])
        for row in saved
    ] == [(seq, bid_id, amount, bidder.id, bidder.name) for seq, amount, bidder, bid_id in winners]
    top_seq, _, top_bidder, _ = winners[-1]
    await assert_high_bid(stores, auction.id, amount=200, bidder=top_bidder, seq=top_seq)
