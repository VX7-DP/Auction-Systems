"""WebSocket /ws/auctions/{id}: server to client only (CLAUDE.md section 10).

Clients open the socket, then fetch the REST snapshot, then apply events whose seq
is past the snapshot's. To make that safe, the socket is registered with the hub
before the handshake completes: any event published after the client's snapshot
read is already queued for it. Close codes are listed in app.realtime.hub.

Keepalive: uvicorn sends protocol-level pings every 20 seconds by default (its
--ws-ping-interval), under the 60-second idle timeout of an AWS ALB.
"""

import asyncio
import logging
from contextlib import suppress
from uuid import UUID

from fastapi import APIRouter, WebSocket
from sqlalchemy import select

from app.models import Auction
from app.realtime.hub import CLOSE_NOT_FOUND, CLOSE_TRY_AGAIN, Close, Hub, Subscription
from app.resources import get_resources

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])


async def _auction_exists(auction_id: UUID) -> bool:
    # A short-lived session, not the get_db_session dependency: that would hold a
    # pooled connection for as long as the socket stays open.
    async with get_resources().db_sessionmaker() as session:
        found = await session.scalar(select(Auction.id).where(Auction.id == auction_id))
    return found is not None


@router.websocket("/ws/auctions/{auction_id}", name="auction_events")
async def auction_events(websocket: WebSocket, auction_id: UUID) -> None:
    """Server to client only: AuctionUpdate and AuctionClosed messages from app.contract."""
    hub: Hub = websocket.app.state.realtime_hub
    if not await _auction_exists(auction_id):
        await websocket.accept()
        await websocket.close(CLOSE_NOT_FOUND, "auction not found")
        return
    if not hub.ready:
        await websocket.accept()
        await websocket.close(CLOSE_TRY_AGAIN, "not ready, try again")
        return

    subscription = hub.subscribe(auction_id)
    try:
        await websocket.accept()
        await _serve(websocket, subscription)
    finally:
        hub.unsubscribe(subscription)


async def _serve(websocket: WebSocket, subscription: Subscription) -> None:
    sender = asyncio.create_task(_send_events(websocket, subscription))
    watcher = asyncio.create_task(_wait_for_disconnect(websocket))
    try:
        await asyncio.wait({sender, watcher}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (sender, watcher):
            task.cancel()
        results = await asyncio.gather(sender, watcher, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            # Usually the client went away mid-send; nothing to recover.
            logger.debug("WebSocket for auction %s ended: %r", subscription.auction_id, result)


async def _send_events(websocket: WebSocket, subscription: Subscription) -> None:
    while True:
        item = await subscription.next()
        if isinstance(item, Close):
            with suppress(Exception):
                await websocket.close(item.code, item.reason)
            return
        await websocket.send_text(item)


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    """Read until the client goes away. Anything a client sends is ignored."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
