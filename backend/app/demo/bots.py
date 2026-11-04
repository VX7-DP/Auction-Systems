"""Simulated bidders, so a lone visitor sees live action (CLAUDE.md section 6).

Bots bid through the same ``place_bid`` as people, always with ``is_bot=True`` and a
name ending in "(bot)". They read the live high bid from the Redis hash (Postgres if
it isn't loaded), bid a little above it, never outbid themselves, and stop at a
per-auction ceiling of 2-4x the starting price so a human can still win.
"""

import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select

from app.auctions.live_state import read_live_states
from app.bidding import service as bidding_service
from app.contract import BidResult
from app.models import Auction
from app.resources import get_resources

logger = logging.getLogger(__name__)

_BOT_NAMESPACE = uuid.UUID("6f1d3c52-3b8e-4c0b-9a51-2f0c8e7d4a10")
CEILING_BASE_MIN = 1_000  # minor units; keeps bots busy on auctions that start at 0


@dataclass(frozen=True)
class Bot:
    bidder_id: UUID
    name: str


BOTS: tuple[Bot, ...] = tuple(
    Bot(uuid.uuid5(_BOT_NAMESPACE, name), f"{name} (bot)")
    for name in ("Asha", "Diego", "Emma", "Kenji", "Lena", "Omar")
)


class PlaceBid(Protocol):
    """The section 10 interface of app.bidding.service.place_bid."""

    def __call__(
        self,
        auction_id: UUID,
        bid_id: UUID,
        bidder_id: UUID,
        bidder_name: str,
        amount: int,
        is_bot: bool = False,
    ) -> Awaitable[BidResult]: ...


def ceiling_for(auction_id: UUID, start_price: int) -> int:
    """The most bots will bid on this auction: 2-4x the start price, fixed per auction."""
    multiplier = random.Random(auction_id.int).uniform(2.0, 4.0)
    return int(max(start_price, CEILING_BASE_MIN) * multiplier)


def next_amount(current: int, rng: random.Random) -> int:
    """A round-ish step above ``current``: 1, 2 or 5 units of its second digit."""
    step = 10 ** max(0, len(str(current)) - 2)
    return current + step * rng.choice((1, 2, 5))


@dataclass(frozen=True)
class _Candidate:
    auction_id: UUID
    status: str
    end_at: datetime
    start_price: int
    high_amount: int | None
    high_bidder_id: UUID | None


class SimulatedBidders:
    def __init__(
        self,
        *,
        place_bid: PlaceBid | None = None,
        rng: random.Random | None = None,
        bid_probability: float = 0.5,
        tick_seconds: float = 2.0,
        may_bid: Callable[[], bool] = lambda: True,
    ) -> None:
        self._place_bid = place_bid
        self._rng = rng or random.Random()
        self._bid_probability = bid_probability
        self._tick_seconds = tick_seconds
        # The job lock's lease check: bots stop the moment it may have lapsed.
        self._may_bid = may_bid

    async def run_forever(self) -> None:
        while True:
            try:
                await self.bid_once()
            except Exception:
                logger.exception("Simulated bidders failed a pass; trying again")
            # Jitter, so bots don't bid in lockstep.
            await asyncio.sleep(self._tick_seconds * self._rng.uniform(0.5, 1.5))

    async def bid_once(self) -> list[BidResult]:
        """One pass: maybe one bot bid on each running auction."""
        results = []
        now = datetime.now(UTC)
        for auction in await self._candidates():
            if auction.status != "open" or auction.end_at <= now:
                continue
            if self._rng.random() >= self._bid_probability:
                continue
            current = (
                auction.high_amount if auction.high_amount is not None else auction.start_price
            )
            amount = next_amount(current, self._rng)
            if amount > ceiling_for(auction.auction_id, auction.start_price):
                continue
            bot = self._rng.choice([b for b in BOTS if b.bidder_id != auction.high_bidder_id])
            if not self._may_bid():
                break
            try:
                result = await self._resolve_place_bid()(
                    auction_id=auction.auction_id,
                    bid_id=uuid.uuid4(),
                    bidder_id=bot.bidder_id,
                    bidder_name=bot.name,
                    amount=amount,
                    is_bot=True,
                )
            except Exception:
                logger.warning("Bot bid on auction %s failed", auction.auction_id, exc_info=True)
                continue
            results.append(result)
        return results

    def _resolve_place_bid(self) -> PlaceBid:
        # Looked up at call time, so the bid engine's module stays the one source.
        return self._place_bid or bidding_service.place_bid

    async def _candidates(self) -> list[_Candidate]:
        resources = get_resources()
        async with resources.db_sessionmaker() as session:
            rows = (
                await session.execute(
                    select(
                        Auction.id,
                        Auction.status,
                        Auction.end_at,
                        Auction.start_price,
                        Auction.high_amount,
                        Auction.high_bidder_id,
                    ).where(Auction.status != "closed", Auction.end_at > datetime.now(UTC))
                )
            ).all()
        live = await read_live_states(resources.redis, [row.id for row in rows])
        candidates = []
        for row in rows:
            state = live.get(row.id)
            if state is None:
                candidates.append(_Candidate(*row))
            else:
                candidates.append(
                    _Candidate(
                        auction_id=row.id,
                        status=state.status,
                        end_at=state.end_at,
                        start_price=state.start_price,
                        high_amount=state.high_amount,
                        high_bidder_id=state.high_bidder_id,
                    )
                )
        return candidates
