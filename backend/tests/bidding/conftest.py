"""Fixtures for the bid engine tests (session 1, CLAUDE.md section 15).

Every test here runs with the app's lifespan open, because place_bid takes its Redis
client and Postgres pool from app.resources.get_resources().
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.contract import AuctionSnapshot, auction_events_channel
from tests.bidding.helpers import EventReader, HeldInsert, Stores

# Read by app.bidding when the app starts. Short values keep the failure-path tests
# fast; the production defaults live in app.bidding.
FAST_BIDDING_ENV = {
    "BIDDING_INSERT_TIMEOUT_MS": "500",  # Postgres statement timeout per attempt to save a bid
    "BIDDING_INSERT_ATTEMPTS": "2",  # attempts before the auction is paused
    "BIDDING_SWEEP_INTERVAL_MS": "100",  # close sweeper period, when background jobs run
}

# seq of the row hold_bid_insert keeps open; far above any seq a test reaches.
HELD_ROW_SEQ = 2**62


@pytest.fixture(autouse=True)
def fast_bidding_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in FAST_BIDDING_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture(autouse=True)
def running_app(fast_bidding_settings: None, app: FastAPI) -> FastAPI:
    """Starts the app (after the settings above) for every test in this package."""
    return app


@pytest.fixture
def stores(db_engine: AsyncEngine, redis: Redis) -> Stores:
    return Stores(db_engine, redis)


@pytest.fixture
def create_auction(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> Callable[..., Awaitable[AuctionSnapshot]]:
    """Creates an auction the normal way, through POST /api/admin/auctions."""

    async def create(
        *, start_price: int = 100, end_at: datetime | None = None, title: str = "Brass lamp"
    ) -> AuctionSnapshot:
        if end_at is None:
            end_at = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0)
        response = await client.post(
            "/api/admin/auctions",
            headers=admin_headers,
            json={"title": title, "start_price": start_price, "end_at": end_at.isoformat()},
        )
        assert response.status_code == 200, response.text
        return AuctionSnapshot.model_validate(response.json())

    return create


@pytest.fixture
def insert_auction(db_engine: AsyncEngine) -> Callable[..., Awaitable[uuid.UUID]]:
    """Writes an auction row straight into Postgres, so Redis has never seen it: the
    same state as after a Redis restart."""

    async def insert(
        *, start_price: int = 100, end_at: datetime, status: str = "open"
    ) -> uuid.UUID:
        async with db_engine.begin() as connection:
            result = await connection.execute(
                text(
                    "INSERT INTO auctions (title, start_price, end_at, status)"
                    " VALUES ('Brass lamp', :start_price, :end_at, :status) RETURNING id"
                ),
                {"start_price": start_price, "end_at": end_at, "status": status},
            )
            return result.scalar_one()

    return insert


@pytest.fixture
def restart_redis(redis: Redis) -> Callable[[], Awaitable[None]]:
    """Simulates a Redis restart without persistence: the data and the script cache go."""

    async def restart() -> None:
        await redis.flushdb()
        await redis.script_flush()

    return restart


@pytest.fixture
def hold_bid_insert(
    db_engine: AsyncEngine,
) -> Callable[[uuid.UUID, uuid.UUID], AbstractAsyncContextManager[HeldInsert]]:
    """``async with hold_bid_insert(auction_id, bid_id):`` keeps an uncommitted bids row
    with that bid_id open in another transaction. Postgres makes any insert of the same
    bid_id wait until the block exits (the row is then rolled back), so a bid can be
    held deterministically between its Redis decision and its Postgres commit.
    """

    @asynccontextmanager
    async def hold(auction_id: uuid.UUID, bid_id: uuid.UUID) -> AsyncIterator[HeldInsert]:
        async with db_engine.connect() as connection:
            holder_pid = (await connection.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            await connection.execute(
                text(
                    "INSERT INTO bids (bid_id, auction_id, seq, bidder_id, bidder_name, amount)"
                    " VALUES (:bid_id, :auction_id, :seq, :bidder_id, 'held by the test', 1)"
                ),
                {
                    "bid_id": bid_id,
                    "auction_id": auction_id,
                    "seq": HELD_ROW_SEQ,
                    "bidder_id": uuid.uuid4(),
                },
            )
            try:
                yield HeldInsert(db_engine, holder_pid)
            finally:
                await connection.rollback()

    return hold


@pytest.fixture
def listen(redis: Redis) -> Callable[[uuid.UUID], AbstractAsyncContextManager[EventReader]]:
    """``async with listen(auction_id) as events:`` subscribes to the auction's channel
    and returns once Redis has confirmed the subscription, so no event is missed."""

    @asynccontextmanager
    async def subscribe(auction_id: uuid.UUID) -> AsyncIterator[EventReader]:
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(auction_events_channel(auction_id))
            confirmation = await pubsub.get_message(timeout=5.0)
            assert confirmation is not None and confirmation["type"] == "subscribe"
            yield EventReader(pubsub)
        finally:
            await pubsub.aclose()

    return subscribe
