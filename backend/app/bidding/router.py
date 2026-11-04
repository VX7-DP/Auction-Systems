from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.auth import current_bidder
from app.contract import BidCreate, Bidder, BidResult

router = APIRouter(prefix="/api/auctions", tags=["bids"])


@router.post("/{auction_id}/bids", response_model_exclude_none=True)
async def create_bid(
    auction_id: UUID,
    body: BidCreate,
    bidder: Annotated[Bidder, Depends(current_bidder)],
) -> BidResult:
    """Place a bid as the signed-in guest, through app.bidding.service.place_bid."""
    raise NotImplementedError
