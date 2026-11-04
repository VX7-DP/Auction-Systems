from uuid import UUID

from fastapi import APIRouter

from app.contract import AuctionSnapshot, AuctionSummary

router = APIRouter(prefix="/api/auctions", tags=["auctions"])


@router.get("")
async def list_auctions() -> list[AuctionSummary]:
    raise NotImplementedError


@router.get("/{auction_id}")
async def get_auction(auction_id: UUID) -> AuctionSnapshot:
    """The snapshot a client fetches on load, after a seq gap, or after a reconnect."""
    raise NotImplementedError
