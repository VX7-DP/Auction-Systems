"""The background sweeper: reopens paused auctions and closes due ones.

Closing is idempotent ("close if open and past end_at"); the close is announced once,
by the call whose Postgres update actually closed the auction. The loop runs only on
the instance holding the job lock (CLAUDE.md section 4, background jobs).
"""

import asyncio
import logging
import uuid

from app.bidding import scripts, store
from app.bidding.events import publish
from app.bidding.loader import reload_auction, reserve_seqs
from app.bidding.settings import BiddingSettings
from app.contract import AuctionClosed, auction_key, job_lock_key
from app.resources import get_resources

logger = logging.getLogger(__name__)

JOB_NAME = "close_sweeper"
MAX_CLOSE_ATTEMPTS = 3


async def sweep_once() -> None:
    redis = get_resources().redis
    for member in await redis.smembers(scripts.PAUSED_AUCTIONS_KEY):
        auction_id = uuid.UUID(member)
        try:
            if await reload_auction(auction_id) == "not_found":
                await redis.srem(scripts.PAUSED_AUCTIONS_KEY, member)
        except Exception:
            logger.warning("Auction %s is still paused: reload failed", auction_id, exc_info=True)

    for auction_id in await store.due_auction_ids(get_resources().db_engine):
        try:
            await close_auction(auction_id)
        except Exception:
            logger.warning("Closing auction %s failed; will retry", auction_id, exc_info=True)


async def close_auction(auction_id: uuid.UUID) -> None:
    resources = get_resources()
    for _ in range(MAX_CLOSE_ATTEMPTS):
        result = await scripts.CLOSE(resources.redis, [auction_key(auction_id)], [])
        if result[0] == "NOT_LOADED":
            if await reload_auction(auction_id) == "not_found":
                return
            continue
        if result[0] == "RESERVE":
            await reserve_seqs(auction_id, int(result[1]), int(result[2]))
            continue
        if result[0] != "CLOSED":  # not due yet, or paused until it is reloaded
            return
        seq, epoch = int(result[1]), int(result[2])
        winner = await store.record_close(resources.db_engine, auction_id, epoch, seq)
        if winner is not None:
            await publish(
                resources.redis,
                AuctionClosed(
                    auction_id=auction_id,
                    seq=seq,
                    winner_id=winner.bidder_id,
                    winner_name=winner.bidder_name,
                    amount=winner.amount,
                ),
            )
            return
        if await store.auction_status(resources.db_engine, auction_id) in ("closed", None):
            return
        # Fenced off: the Redis copy is outdated. Reload and close again.
        await reload_auction(auction_id)


async def run_sweeper(settings: BiddingSettings) -> None:
    """Sweeps every interval while this instance holds the job lock."""
    redis = get_resources().redis
    lock = job_lock_key(JOB_NAME)
    token = uuid.uuid4().hex
    try:
        while True:
            try:
                holds = await scripts.HOLD_LOCK(
                    redis, [lock], [token, str(settings.job_lock_ttl_ms)]
                )
                if holds == 1:
                    await sweep_once()
            except Exception:
                logger.exception("Close sweeper pass failed")
            await asyncio.sleep(settings.sweep_interval_ms / 1000)
    finally:
        try:
            await scripts.RELEASE_LOCK(redis, [lock], [token])
        except Exception:
            logger.warning("Could not release the %s job lock", JOB_NAME)
