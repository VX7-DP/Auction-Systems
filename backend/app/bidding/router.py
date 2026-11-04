from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.auth import current_bidder
from app.bidding.service import place_bid
from app.contract import BidCreate, Bidder, BidResult

router = APIRouter(prefix="/api/auctions", tags=["bids"])


@router.post(
    "/{auction_id}/bids",
    response_model_exclude_none=True,
    responses={503: {"model": BidResult, "description": "The bid could not be saved."}},
)
async def create_bid(
    auction_id: UUID,
    body: BidCreate,
    bidder: Annotated[Bidder, Depends(current_bidder)],
    response: Response,
) -> BidResult:
    """Place a bid as the signed-in guest. Accepted and rejected bids answer 200;
    a bid that could not be saved answers 503 with status "error" (retry the same bid_id)."""
    result = await place_bid(
        auction_id, body.bid_id, bidder.bidder_id, bidder.display_name, body.amount
    )
    if result.status == "error":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
