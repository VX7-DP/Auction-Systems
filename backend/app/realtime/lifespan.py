import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.realtime.hub import CLOSE_SERVICE_RESTART, Hub
from app.realtime.listener import Listener
from app.realtime.settings import RealtimeSettings
from app.resources import get_resources

logger = logging.getLogger(__name__)

# Startup waits this long for the Redis subscription, then carries on: sockets are
# refused with "try again" until the listener is subscribed.
READY_WAIT_SECONDS = 3.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs this instance's one Pub/Sub listener. Per instance, not a locked job."""
    hub = Hub(reorder_window_seconds=RealtimeSettings().reorder_window_ms / 1000)
    listener = Listener(
        get_resources().settings.redis_url,
        hub,
        client_name=f"auction-realtime-{uuid.uuid4().hex[:12]}",
    )
    app.state.realtime_hub = hub
    app.state.realtime_listener = listener
    task = asyncio.create_task(listener.run(), name="realtime-listener")
    if not await hub.wait_ready(READY_WAIT_SECONDS):
        logger.warning("Not subscribed to auction events yet; WebSockets wait for Redis")
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        hub.close_all(CLOSE_SERVICE_RESTART, "server restarting")
