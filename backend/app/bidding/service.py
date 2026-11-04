"""The one bid path, used by the bids endpoint and by the simulated bidders.

CLAUDE.md section 4: Redis decides (Lua CAS), Postgres confirms (the bid counts only
once committed), then the update is published.
"""

import asyncio
import logging
from typing import Literal
from uuid import UUID

from app.bidding import scripts, store
from app.bidding.events import publish, publish_current_state
from app.bidding.loader import reload_auction
from app.bidding.settings import bid_slots, get_bidding_settings
from app.contract import AuctionUpdate, BidResult, auction_key
from app.resources import get_resources

logger = logging.getLogger(__name__)

# Bounds the reload-and-decide-again loop when reloads keep racing this bid.
MAX_DECISIONS = 10

SaveOutcome = Literal["saved", "fenced", "failed"]


async def place_bid(
    auction_id: UUID,
    bid_id: UUID,
    bidder_id: UUID,
    bidder_name: str,
    amount: int,
    is_bot: bool = False,
) -> BidResult:
    """Decide the bid in Redis (Lua CAS), commit it to Postgres, then publish it."""
    try:
        async with bid_slots():
            return await _place_bid(auction_id, bid_id, bidder_id, bidder_name, amount, is_bot)
    except Exception:
        logger.exception("Bid %s on auction %s failed", bid_id, auction_id)
        return BidResult(bid_id=bid_id, status="error")


async def _place_bid(
    auction_id: UUID,
    bid_id: UUID,
    bidder_id: UUID,
    bidder_name: str,
    amount: int,
    is_bot: bool,
) -> BidResult:
    redis = get_resources().redis
    keys = [auction_key(auction_id), scripts.bid_memory_key(auction_id)]
    args = [str(bid_id), str(bidder_id), bidder_name, str(amount), "1" if is_bot else "0"]

    for _ in range(MAX_DECISIONS):
        decision = await scripts.DECIDE(redis, keys, args)
        kind = decision[0]
        if kind == "NOT_LOADED":
            if await reload_auction(auction_id) == "not_found":
                return BidResult(bid_id=bid_id, status="rejected", reason="not_found")
            continue
        if kind == "REJECTED":
            return BidResult(bid_id=bid_id, status="rejected", reason=decision[1])

        epoch = int(decision[1])
        if kind == "REPEAT":
            # Save what was decided the first time, never this request's body.
            bid = store.BidRecord.decode(bid_id, decision[2])
        else:
            bid = store.BidRecord(bid_id, int(decision[2]), amount, bidder_id, bidder_name, is_bot)

        outcome = await _save(auction_id, epoch, bid)
        if outcome == "saved":
            if kind == "ACCEPTED":
                await publish(
                    redis,
                    AuctionUpdate(
                        auction_id=auction_id,
                        seq=bid.seq,
                        high_amount=bid.amount,
                        high_bidder_id=bid.bidder_id,
                        high_bidder_name=bid.bidder_name,
                        is_bot=bid.is_bot,
                        end_at=store.from_ms(int(decision[3])),
                        status="open",
                    ),
                )
            return BidResult(bid_id=bid_id, status="accepted", seq=bid.seq)
        if outcome == "fenced":
            # The decision came from an outdated Redis copy. Reload it unless another
            # reload already has, then decide again.
            loaded_epoch = await redis.hget(auction_key(auction_id), "epoch")
            if loaded_epoch is None or int(loaded_epoch) <= epoch:
                await reload_auction(auction_id)
            continue
        # Redis accepted a bid that Postgres won't take: stop building on it.
        await _pause(auction_id, epoch)
        return BidResult(bid_id=bid_id, status="error")

    logger.error(
        "Bid %s on auction %s: gave up after %s decisions", bid_id, auction_id, MAX_DECISIONS
    )
    return BidResult(bid_id=bid_id, status="error")


async def _save(auction_id: UUID, epoch: int, bid: store.BidRecord) -> SaveOutcome:
    settings = get_bidding_settings()
    engine = get_resources().db_engine
    # A guard in case Postgres can't even be reached; the statement timeout is the real limit.
    guard_seconds = max(5.0, 4 * settings.insert_timeout_ms / 1000)
    for attempt in range(1, settings.insert_attempts + 1):
        try:
            async with asyncio.timeout(guard_seconds):
                await store.save_bid(engine, auction_id, epoch, bid, settings.insert_timeout_ms)
            return "saved"
        except store.Fenced:
            return "fenced"
        except Exception as error:
            logger.warning(
                "Saving bid %s (attempt %s of %s) failed: %r",
                bid.bid_id,
                attempt,
                settings.insert_attempts,
                error,
            )
    return "failed"


async def _pause(auction_id: UUID, epoch: int) -> None:
    """Pauses the auction in Redis. Postgres is what failed, so the pause can't be
    committed first; it is published straight away. The sweeper reloads and reopens it."""
    redis = get_resources().redis
    result = await scripts.PAUSE(
        redis, [auction_key(auction_id), scripts.PAUSED_AUCTIONS_KEY], [str(epoch), str(auction_id)]
    )
    if result[0] == "PAUSED":
        logger.error("Auction %s paused: a bid accepted in Redis could not be saved", auction_id)
        await publish_current_state(redis, auction_id)
