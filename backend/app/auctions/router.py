"""GET /api/auctions and GET /api/auctions/{id}.

Live fields (status, seq, high bid, end_at, start_price) come from the Redis hash the
bid engine keeps. Title and bid history come from Postgres. When an auction's hash is
missing or Redis is unreachable, every field comes from Postgres, which holds the
confirmed state.

A closed auction's winner is Postgres's to say (app.bidding.store.record_close). Until
the sweeper reloads it, Redis can still hold a bid the close fenced off as its high
bid, and before record_close commits the winner isn't known at all. So once Redis says
closed, the snapshot answers from Postgres (the pre-close state until the close is
recorded), and the list takes the amount from Postgres.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auctions.live_state import read_live_states
from app.contract import AuctionSnapshot, AuctionSummary, RecentBid
from app.models import Auction, Bid
from app.resources import get_db_session, get_redis

RECENT_BIDS_LIMIT = 20

router = APIRouter(prefix="/api/auctions", tags=["auctions"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]


def _list_order(item: AuctionSummary) -> tuple[int, float]:
    """Running auctions first, ending soonest first; then closed ones, latest first."""
    timestamp = item.end_at.timestamp()
    return (1, -timestamp) if item.status == "closed" else (0, timestamp)


@router.get("")
async def list_auctions(session: DbSession, redis: RedisClient) -> list[AuctionSummary]:
    rows = (
        await session.execute(
            select(Auction.id, Auction.title, Auction.status, Auction.high_amount, Auction.end_at)
        )
    ).all()
    live = await read_live_states(redis, [row.id for row in rows])
    items = []
    for row in rows:
        state = live.get(row.id)
        items.append(
            AuctionSummary(
                id=row.id,
                title=row.title,
                status=state.status if state else row.status,
                high_amount=(
                    state.high_amount if state and state.status != "closed" else row.high_amount
                ),
                end_at=state.end_at if state else row.end_at,
            )
        )
    return sorted(items, key=_list_order)


@router.get("/{auction_id}")
async def get_auction(auction_id: UUID, session: DbSession, redis: RedisClient) -> AuctionSnapshot:
    """The snapshot a client fetches on load, after a seq gap, or after a reconnect.

    Redis is read before Postgres, and the history is cut at the snapshot's seq, so a
    bid committed in between belongs to a later event, not to this snapshot.
    """
    state = (await read_live_states(redis, [auction_id])).get(auction_id)
    if state is not None and state.status == "closed":
        state = None  # the winner comes from Postgres (see the module docstring)
    auction = await session.get(Auction, auction_id)
    if auction is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such auction.")

    seq = state.seq if state else auction.high_seq
    bids = (
        await session.scalars(
            select(Bid)
            .where(Bid.auction_id == auction_id, Bid.seq <= seq)
            .order_by(Bid.seq.desc())
            .limit(RECENT_BIDS_LIMIT)
        )
    ).all()
    recent_bids = [
        RecentBid(
            seq=bid.seq,
            amount=bid.amount,
            bidder_name=bid.bidder_name,
            is_bot=bid.is_bot,
            created_at=bid.created_at,
        )
        for bid in bids
    ]

    if state is not None:
        return AuctionSnapshot(
            id=auction.id,
            title=auction.title,
            start_price=state.start_price,
            end_at=state.end_at,
            status=state.status,
            seq=state.seq,
            high_amount=state.high_amount,
            high_bidder_id=state.high_bidder_id,
            high_bidder_name=state.high_bidder_name,
            high_is_bot=state.high_is_bot,
            recent_bids=recent_bids,
        )

    # Accepted bids only ever raise the price, so the latest bid is the high bid.
    has_high = auction.high_amount is not None
    return AuctionSnapshot(
        id=auction.id,
        title=auction.title,
        start_price=auction.start_price,
        end_at=auction.end_at,
        status=auction.status,  # type: ignore[arg-type]  # the column has a CHECK constraint
        seq=auction.high_seq,
        high_amount=auction.high_amount,
        high_bidder_id=auction.high_bidder_id,
        high_bidder_name=auction.high_bidder_name,
        high_is_bot=bids[0].is_bot if has_high and bids else None,
        recent_bids=recent_bids,
    )
