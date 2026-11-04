"""The checks the bid script makes (CLAUDE.md section 4, step 1)."""

import uuid
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis

from app.contract import MAX_AMOUNT, AuctionSnapshot, auction_key
from tests.bidding.helpers import Stores, accepted, assert_high_bid, bid, new_bidder, rejected


async def test_first_bid_must_be_above_the_start_price(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], stores: Stores
) -> None:
    auction = await create_auction(start_price=100)
    asha = new_bidder("Asha")

    at_start = await bid(auction.id, asha, 100)
    above = await bid(auction.id, asha, 101)

    assert at_start == rejected(at_start.bid_id, "too_low")
    assert above == accepted(above.bid_id, 1)
    await assert_high_bid(stores, auction.id, amount=101, bidder=asha, seq=1)


async def test_bid_equal_to_the_high_bid_is_too_low(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], stores: Stores
) -> None:
    auction = await create_auction(start_price=100)
    asha, ben = new_bidder("Asha"), new_bidder("Ben")
    first = await bid(auction.id, asha, 150)

    tie = await bid(auction.id, ben, 150)

    assert first == accepted(first.bid_id, 1)
    assert tie == rejected(tie.bid_id, "too_low")
    await assert_high_bid(stores, auction.id, amount=150, bidder=asha, seq=1)


async def test_bid_on_an_unknown_auction_is_not_found(redis: Redis) -> None:
    auction_id = uuid.uuid4()

    result = await bid(auction_id, new_bidder("Asha"), 150)

    assert result == rejected(result.bid_id, "not_found")
    assert not await redis.exists(auction_key(auction_id))


async def test_bot_bids_are_labelled(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], stores: Stores
) -> None:
    auction = await create_auction(start_price=100)
    bot = new_bidder("Bot Priya", is_bot=True)

    result = await bid(auction.id, bot, 150)

    assert result == accepted(result.bid_id, 1)
    assert [row["is_bot"] for row in await stores.bids(auction.id)] == [True]
    await assert_high_bid(stores, auction.id, amount=150, bidder=bot, seq=1)


async def test_amounts_up_to_max_amount_stay_exact(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], stores: Stores
) -> None:
    """Lua numbers are doubles, and tostring() prints them with 14 digits; amounts this
    large must still round-trip exactly."""
    auction = await create_auction(start_price=MAX_AMOUNT - 2)
    asha, ben = new_bidder("Asha"), new_bidder("Ben")

    first = await bid(auction.id, asha, MAX_AMOUNT - 1)
    second = await bid(auction.id, ben, MAX_AMOUNT)

    assert (first, second) == (accepted(first.bid_id, 1), accepted(second.bid_id, 2))
    assert [row["amount"] for row in await stores.bids(auction.id)] == [MAX_AMOUNT - 1, MAX_AMOUNT]
    await assert_high_bid(stores, auction.id, amount=MAX_AMOUNT, bidder=ben, seq=2)
