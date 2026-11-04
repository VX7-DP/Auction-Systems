"""Fixtures for session 2's tests: real-time fan-out, auction reads and demo bots.

The bid engine (session 1) is built in parallel, so these tests never call it.
``FakeEngine`` stands in for its effects, as section 10 defines them: a bid row in
Postgres, the ``auction:{id}`` hash in Redis, and an event on ``auction:{id}:events``.
``RecordingPlaceBid`` stands in for ``app.bidding.service.place_bid``.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from httpx_ws import AsyncWebSocketSession, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contract import (
    AuctionClosed,
    AuctionUpdate,
    BidResult,
    auction_events_channel,
    auction_key,
)
from app.models import Auction, Bid
from app.realtime.hub import Hub

RECEIVE_TIMEOUT = 5.0

WsConnect = Callable[[str], AbstractAsyncContextManager[AsyncWebSocketSession]]


def end_at_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def in_hours(hours: float) -> datetime:
    return (datetime.now(UTC) + timedelta(hours=hours)).replace(microsecond=0)


async def wait_until(predicate: Callable[[], bool], seconds: float = RECEIVE_TIMEOUT) -> None:
    async with asyncio.timeout(seconds):
        while True:
            if predicate():
                return
            await asyncio.sleep(0.01)


@dataclass
class FakeEngine:
    """Applies bids the way section 4 says the bid engine does, without its checks."""

    db: AsyncSession
    redis: Redis

    async def create_auction(
        self,
        *,
        title: str = "Brass lamp",
        start_price: int = 1_000,
        end_at: datetime | None = None,
        status: str = "open",
        load_redis: bool = True,
    ) -> UUID:
        """Inserts the auction row and, unless told not to, loads its Redis hash."""
        end_at = end_at or in_hours(1)
        auction = Auction(title=title, start_price=start_price, end_at=end_at, status=status)
        self.db.add(auction)
        await self.db.commit()
        if load_redis:
            await self.redis.hset(
                auction_key(auction.id),
                mapping={
                    "status": status,
                    "end_at_ms": end_at_ms(end_at),
                    "start_price": start_price,
                    "seq": 0,
                },
            )
        return auction.id

    async def bid(
        self,
        auction_id: UUID,
        amount: int,
        *,
        bidder_name: str = "Asha",
        bidder_id: UUID | None = None,
        is_bot: bool = False,
        publish: bool = True,
    ) -> AuctionUpdate:
        """Accepts a bid: Redis hash first, then the Postgres commit, then the publish."""
        bidder_id = bidder_id or uuid.uuid4()
        key = auction_key(auction_id)
        seq = await self.redis.hincrby(key, "seq", 1)
        await self.redis.hset(
            key,
            mapping={
                "high_amount": amount,
                "high_bidder_id": str(bidder_id),
                "high_bidder_name": bidder_name,
                "high_is_bot": "1" if is_bot else "0",
            },
        )
        self.db.add(
            Bid(
                bid_id=uuid.uuid4(),
                auction_id=auction_id,
                seq=seq,
                bidder_id=bidder_id,
                bidder_name=bidder_name,
                amount=amount,
                is_bot=is_bot,
            )
        )
        await self.db.execute(
            update(Auction)
            .where(Auction.id == auction_id, Auction.high_seq < seq)
            .values(
                high_amount=amount,
                high_bidder_id=bidder_id,
                high_bidder_name=bidder_name,
                high_seq=seq,
            )
        )
        await self.db.commit()
        fields = await self.redis.hgetall(key)
        event = AuctionUpdate(
            auction_id=auction_id,
            seq=seq,
            high_amount=amount,
            high_bidder_id=bidder_id,
            high_bidder_name=bidder_name,
            is_bot=is_bot,
            end_at=datetime.fromtimestamp(int(fields["end_at_ms"]) / 1000, UTC),
            status=fields["status"],
        )
        if publish:
            await self.publish(event)
        return event

    async def publish(self, event: AuctionUpdate | AuctionClosed) -> None:
        await self.redis.publish(auction_events_channel(event.auction_id), event.model_dump_json())


@pytest.fixture
def engine(db_session: AsyncSession, redis: Redis) -> FakeEngine:
    return FakeEngine(db_session, redis)


def update_event(
    auction_id: UUID, seq: int, amount: int = 1_500, **overrides: Any
) -> AuctionUpdate:
    """An auction_update for publishing straight to the channel."""
    values: dict[str, Any] = {
        "auction_id": auction_id,
        "seq": seq,
        "high_amount": amount,
        "high_bidder_id": uuid.uuid4(),
        "high_bidder_name": "Asha",
        "is_bot": False,
        "end_at": in_hours(1),
        "status": "open",
    }
    values.update(overrides)
    return AuctionUpdate(**values)


@asynccontextmanager
async def _open_ws(app: FastAPI, path: str) -> AsyncIterator[AsyncWebSocketSession]:
    # The transport runs the app in an anyio task group, which must be entered and
    # exited in the same task, so each connection gets its own client in the test.
    transport = ASGIWebSocketTransport(app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with aconnect_ws(path, client) as ws:
            yield ws


@pytest.fixture
def ws_connect(app: FastAPI) -> WsConnect:
    """``async with ws_connect("/ws/auctions/...") as ws`` opens a WebSocket to the app."""
    return partial(_open_ws, app)


@pytest.fixture
def hub(app: FastAPI) -> Hub:
    return app.state.realtime_hub


@dataclass
class RecordingPlaceBid:
    """Stands in for app.bidding.service.place_bid: records each call, then answers."""

    status: str = "accepted"
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(
        self,
        auction_id: UUID,
        bid_id: UUID,
        bidder_id: UUID,
        bidder_name: str,
        amount: int,
        is_bot: bool = False,
    ) -> BidResult:
        self.calls.append(
            {
                "auction_id": auction_id,
                "bid_id": bid_id,
                "bidder_id": bidder_id,
                "bidder_name": bidder_name,
                "amount": amount,
                "is_bot": is_bot,
            }
        )
        if self.error is not None:
            raise self.error
        if self.status == "accepted":
            return BidResult(bid_id=bid_id, status="accepted", seq=len(self.calls))
        return BidResult(bid_id=bid_id, status="rejected", reason="too_low")
