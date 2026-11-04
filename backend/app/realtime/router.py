from uuid import UUID

from fastapi import APIRouter, WebSocket

router = APIRouter(tags=["realtime"])


@router.websocket("/ws/auctions/{auction_id}", name="auction_events")
async def auction_events(websocket: WebSocket, auction_id: UUID) -> None:
    """Server to client only: AuctionUpdate and AuctionClosed messages from app.contract."""
    raise NotImplementedError
