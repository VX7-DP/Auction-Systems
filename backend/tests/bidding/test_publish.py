"""Updates are published only after Postgres commits (CLAUDE.md section 4, step 3)."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from app.contract import AuctionSnapshot, AuctionUpdate
from tests.bidding.helpers import EventReader, HeldInsert, Stores, accepted, bid, new_bidder

Listen = Callable[[uuid.UUID], AbstractAsyncContextManager[EventReader]]
HoldBidInsert = Callable[[uuid.UUID, uuid.UUID], AbstractAsyncContextManager[HeldInsert]]


async def test_accepted_bid_is_published_once_after_it_is_saved(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    stores: Stores,
    listen: Listen,
    hold_bid_insert: HoldBidInsert,
) -> None:
    auction = await create_auction(start_price=100)
    asha = new_bidder("Asha")
    bid_id = uuid.uuid4()

    async with listen(auction.id) as events:
        async with hold_bid_insert(auction.id, bid_id) as held:
            task = asyncio.create_task(bid(auction.id, asha, 150, bid_id=bid_id))
            await held.wait_until_blocked(task)
            # Decided in Redis but not yet in Postgres: nobody may hear about it.
            assert await events.drain() == []
        assert await task == accepted(bid_id, 1)

        assert await events.next() == AuctionUpdate(
            auction_id=auction.id,
            seq=1,
            high_amount=150,
            high_bidder_id=asha.id,
            high_bidder_name="Asha",
            is_bot=False,
            end_at=auction.end_at,
            status="open",
        )
        assert await events.drain() == []
    assert [row["bid_id"] for row in await stores.bids(auction.id)] == [bid_id]


async def test_rejected_and_repeated_bids_publish_nothing(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], listen: Listen
) -> None:
    auction = await create_auction(start_price=100)
    asha, ben = new_bidder("Asha"), new_bidder("Ben")
    bid_id = uuid.uuid4()

    async with listen(auction.id) as events:
        assert await bid(auction.id, asha, 150, bid_id=bid_id) == accepted(bid_id, 1)
        assert (await events.next()).seq == 1

        await bid(auction.id, ben, 120)  # too low
        await bid(auction.id, asha, 150, bid_id=bid_id)  # a retry

        assert await events.drain() == []
