"""Plain helpers for the bid engine tests. Fixtures live in conftest.py."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.bidding.service import place_bid
from app.contract import (
    AuctionClosed,
    AuctionUpdate,
    BidResult,
    RejectReason,
    auction_event_adapter,
    auction_key,
)

UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Bidder:
    id: uuid.UUID
    name: str
    is_bot: bool = False


def new_bidder(name: str, *, is_bot: bool = False) -> Bidder:
    return Bidder(id=uuid.uuid4(), name=name, is_bot=is_bot)


async def bid(
    auction_id: uuid.UUID, bidder: Bidder, amount: int, *, bid_id: uuid.UUID | None = None
) -> BidResult:
    """One bid through the contract's single bid path, app.bidding.service.place_bid."""
    return await place_bid(
        auction_id,
        bid_id or uuid.uuid4(),
        bidder.id,
        bidder.name,
        amount,
        is_bot=bidder.is_bot,
    )


def accepted(bid_id: uuid.UUID, seq: int) -> BidResult:
    return BidResult(bid_id=bid_id, status="accepted", seq=seq)


def rejected(bid_id: uuid.UUID, reason: RejectReason) -> BidResult:
    return BidResult(bid_id=bid_id, status="rejected", reason=reason)


def to_ms(moment: datetime) -> int:
    """Whole milliseconds since the Unix epoch, as stored in the Redis hash's end_at_ms."""
    return (moment - UNIX_EPOCH) // timedelta(milliseconds=1)


# --- Reading both stores ----------------------------------------------------------------


class Stores:
    """Reads what Postgres (the source of truth) and Redis (the hot path) hold."""

    def __init__(self, db_engine: AsyncEngine, redis: Redis) -> None:
        self._db = db_engine
        self._redis = redis

    async def bids(self, auction_id: uuid.UUID) -> list[dict[str, Any]]:
        """Saved bids of one auction, in seq order."""
        async with self._db.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT bid_id, seq, bidder_id, bidder_name, amount, is_bot FROM bids"
                    " WHERE auction_id = :auction_id ORDER BY seq"
                ),
                {"auction_id": auction_id},
            )
            return [dict(row._mapping) for row in rows]

    async def auction(self, auction_id: uuid.UUID) -> dict[str, Any]:
        async with self._db.connect() as connection:
            row = (
                await connection.execute(
                    text("SELECT * FROM auctions WHERE id = :auction_id"),
                    {"auction_id": auction_id},
                )
            ).one()
            return dict(row._mapping)

    async def hash(self, auction_id: uuid.UUID) -> dict[str, str]:
        return await self._redis.hgetall(auction_key(auction_id))


async def assert_high_bid(
    stores: Stores,
    auction_id: uuid.UUID,
    *,
    amount: int,
    bidder: Bidder,
    seq: int,
    redis_seq: int | None = None,
) -> None:
    """Postgres and Redis agree on the high bid, the seq and the load epoch.

    ``redis_seq`` is Redis's seq when it differs from the high bid's: after Redis lost
    the auction, seqs resume at the reserved ceiling (app.bidding.store).
    Redis never holds a seq above its ceiling, nor a ceiling Postgres hasn't reserved."""
    row = await stores.auction(auction_id)
    assert (
        row["high_amount"],
        row["high_bidder_id"],
        row["high_bidder_name"],
        row["high_seq"],
    ) == (amount, bidder.id, bidder.name, seq)
    redis_hash = await stores.hash(auction_id)
    assert {
        field: redis_hash.get(field)
        for field in ("high_amount", "high_bidder_id", "high_bidder_name", "high_is_bot", "seq")
    } == {
        "high_amount": str(amount),
        "high_bidder_id": str(bidder.id),
        "high_bidder_name": bidder.name,
        "high_is_bot": "1" if bidder.is_bot else "0",
        "seq": str(seq if redis_seq is None else redis_seq),
    }
    assert redis_hash.get("epoch") == str(row["load_epoch"])
    assert int(redis_hash["seq"]) <= int(redis_hash["ceiling"]) <= row["seq_ceiling"]


# --- Time: Redis TIME is the one clock ----------------------------------------------------


async def redis_now(redis: Redis) -> datetime:
    seconds, microseconds = await redis.time()
    return UNIX_EPOCH + timedelta(seconds=seconds, microseconds=microseconds)


async def wait_until_redis_time(redis: Redis, moment: datetime) -> None:
    async def reached() -> bool:
        return await redis_now(redis) >= moment

    await poll_until(reached, what=f"Redis TIME to reach {moment.isoformat()}")


# --- Waiting on background bids -------------------------------------------------------------


async def poll_until(
    condition: Callable[[], Awaitable[Any]],
    *,
    what: str,
    task: asyncio.Task[Any] | None = None,
    within: float = 10.0,
) -> None:
    """Waits until ``condition()`` is truthy.

    If ``task`` (a background bid) finishes first, its exception is raised, so an
    unimplemented or failing place_bid fails the test at once instead of timing out.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + within
    while True:
        if task is not None and task.done():
            result = task.result()  # raises the task's exception, if any
            raise AssertionError(f"the bid finished ({result!r}) before {what}")
        if await condition():
            return
        if loop.time() > deadline:
            raise AssertionError(f"timed out after {within}s waiting for {what}")
        await asyncio.sleep(0.01)


# --- Pub/Sub events ----------------------------------------------------------------------------


class EventReader:
    """Reads the auction's events from Redis Pub/Sub, as the WebSocket layer would."""

    def __init__(self, pubsub: PubSub) -> None:
        self._pubsub = pubsub

    async def next(self, within: float = 5.0) -> AuctionUpdate | AuctionClosed:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within
        while (remaining := deadline - loop.time()) > 0:
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=remaining
            )
            if message is not None:
                return auction_event_adapter.validate_json(message["data"])
        raise AssertionError(f"no event arrived within {within}s")

    async def drain(self, wait: float = 0.3) -> list[AuctionUpdate | AuctionClosed]:
        """Every event that arrives within ``wait`` seconds."""
        events: list[AuctionUpdate | AuctionClosed] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while (remaining := deadline - loop.time()) > 0:
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=remaining
            )
            if message is not None:
                events.append(auction_event_adapter.validate_json(message["data"]))
        return events


# --- Holding a bid between its Redis decision and its Postgres commit -----------------


class HeldInsert:
    def __init__(self, db_engine: AsyncEngine, holder_pid: int) -> None:
        self._db = db_engine
        self._holder_pid = holder_pid

    async def wait_until_blocked(self, task: asyncio.Task[object]) -> None:
        """Waits until the app's insert of the held bid_id is waiting on our row."""

        async def blocked() -> bool:
            async with self._db.connect() as connection:
                result = await connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity"
                        " WHERE :holder = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"holder": self._holder_pid},
                )
                return bool(result.scalar_one())

        await poll_until(blocked, task=task, what="the bid's insert to wait on the held row")
