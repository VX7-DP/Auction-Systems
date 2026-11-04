import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_admin
from app.bidding import store
from app.bidding.loader import load_into_redis
from app.bidding.settings import get_bidding_settings
from app.contract import AuctionCreate, AuctionSnapshot
from app.resources import get_resources

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/auctions")
async def create_auction(body: AuctionCreate) -> AuctionSnapshot:
    """Create an auction in Postgres and load it into Redis; returns the new auction."""
    if body.end_at <= datetime.now(UTC):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "end_at must be in the future.")
    auction = await store.insert_auction(
        get_resources().db_engine,
        body.title,
        body.start_price,
        body.end_at,
        get_bidding_settings().seq_block,
    )
    try:
        await load_into_redis(auction)
    except Exception:
        # Postgres has it; the first bid loads it (NOT_LOADED) instead.
        logger.warning("Auction %s created but not loaded into Redis", auction.id, exc_info=True)
    return AuctionSnapshot(
        id=auction.id,
        title=auction.title,
        start_price=auction.start_price,
        end_at=auction.end_at,
        status="open",
        seq=0,
        high_amount=None,
        high_bidder_id=None,
        high_bidder_name=None,
        high_is_bot=None,
        recent_bids=[],
    )
