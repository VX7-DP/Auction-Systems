"""Loading auctions from Postgres into Redis, and reloading them.

A reload happens when the Redis copy is missing (a Redis restart: NOT_LOADED), outdated
(a bid write was fenced off), or paused after a failed save. It runs under a Redis lock
so a burst of bids triggers one reload, not one each.
"""

import asyncio
import logging
import uuid
from typing import Literal

from app.bidding import scripts, store
from app.bidding.events import publish
from app.bidding.settings import get_bidding_settings
from app.contract import AuctionUpdate, auction_key
from app.resources import get_resources

logger = logging.getLogger(__name__)

ReloadOutcome = Literal["loaded", "not_found", "busy"]


async def load_into_redis(auction: store.StoredAuction) -> int | None:
    """Writes the auction's state into Redis. If this load reopened a paused auction,
    returns the reopen's seq (one more seq, not yet recorded in Postgres); else None."""
    redis = get_resources().redis
    args = [
        str(auction.id),
        str(auction.load_epoch),
        auction.status,
        str(store.to_ms(auction.end_at)),
        str(auction.start_price),
        str(auction.high_seq),
        "" if auction.high_amount is None else str(auction.high_amount),
        "" if auction.high_bidder_id is None else str(auction.high_bidder_id),
        auction.high_bidder_name or "",
        "1" if auction.high_is_bot else "0",
        str(auction.resume_seq),
        str(auction.seq_ceiling),
    ]
    for bid in auction.bids:
        args += [str(bid.bid_id), bid.encode()]
    result = await scripts.LOAD(
        redis,
        [auction_key(auction.id), scripts.bid_memory_key(auction.id), scripts.PAUSED_AUCTIONS_KEY],
        args,
    )
    if result[0] == "LOADED" and result[2] == "1":
        return int(result[1])
    return None


async def reload_auction(auction_id: uuid.UUID) -> ReloadOutcome:
    """Reloads the auction from Postgres. "busy" means another reload held the lock;
    it has finished or timed out by the time this returns, so the caller just retries."""
    resources = get_resources()
    settings = get_bidding_settings()
    lock = scripts.reload_lock_key(auction_id)
    token = uuid.uuid4().hex
    if not await resources.redis.set(lock, token, nx=True, px=settings.reload_lock_ttl_ms):
        await _wait_for_release(lock, settings.reload_lock_ttl_ms)
        return "busy"
    try:
        auction = await store.read_for_load(
            resources.db_engine, auction_id, settings.reload_timeout_ms, settings.seq_block
        )
        if auction is None:
            return "not_found"
        reopen_seq = await load_into_redis(auction)
        if reopen_seq is not None:
            await _announce_reopened(auction, reopen_seq)
        return "loaded"
    finally:
        await scripts.RELEASE_LOCK(resources.redis, [lock], [token])


async def reserve_seqs(auction_id: uuid.UUID, epoch: int, seq: int) -> None:
    """Redis is out of reserved seqs: reserve the next block in Postgres, then let Redis
    use it. If the auction has been reloaded since, its new load has its own block."""
    resources = get_resources()
    ceiling = await store.raise_seq_ceiling(
        resources.db_engine, auction_id, epoch, seq, get_bidding_settings().seq_block
    )
    if ceiling is not None:
        await scripts.RAISE_CEILING(
            resources.redis, [auction_key(auction_id)], [str(epoch), str(ceiling)]
        )


async def _announce_reopened(auction: store.StoredAuction, seq: int) -> None:
    """Records the reopen's seq in Postgres, then publishes the state LOAD wrote.

    Both come from the load itself, never from reading Redis again: the auction is open
    from the moment LOAD returns, so a bid decided since would otherwise lend its seq to
    record_seq (and its save would then not move the high bid) and be published unsaved.
    """
    resources = get_resources()
    if await store.record_seq(resources.db_engine, auction.id, auction.load_epoch, seq):
        await publish(
            resources.redis,
            AuctionUpdate(
                auction_id=auction.id,
                seq=seq,
                high_amount=auction.high_amount,
                high_bidder_id=auction.high_bidder_id,
                high_bidder_name=auction.high_bidder_name,
                is_bot=auction.high_is_bot,
                end_at=store.from_ms(store.to_ms(auction.end_at)),
                status="open",
            ),
        )


async def _wait_for_release(lock: str, ttl_ms: int) -> None:
    redis = get_resources().redis
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ttl_ms / 1000
    # The lock lives in Redis, shared by every instance, so this polls it.
    while loop.time() < deadline:
        if not await redis.exists(lock):
            return
        await asyncio.sleep(0.01)
