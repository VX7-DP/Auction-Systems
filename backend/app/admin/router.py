from fastapi import APIRouter, Depends

from app.auth import require_admin
from app.contract import AuctionCreate, AuctionSnapshot

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/auctions")
async def create_auction(body: AuctionCreate) -> AuctionSnapshot:
    """Create an auction in Postgres and load it into Redis; returns the new auction."""
    raise NotImplementedError
