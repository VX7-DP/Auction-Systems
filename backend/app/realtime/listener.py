"""This instance's one Redis Pub/Sub listener (CLAUDE.md section 4).

It pattern-subscribes to every auction's events channel, so viewers coming and going
never need a subscribe or unsubscribe. The cost is that every instance receives every
auction's events, which is negligible at demo scale.

Redis Pub/Sub doesn't redeliver, so the listener treats each (re)subscription as a
fresh start: the hub closes every socket and clients resync from the snapshot. That
covers both a dropped connection this loop reconnects and one redis-py reconnects
on its own, since the server confirms each new subscription with a message.

Pub/Sub channels ignore the Redis database number, so instances sharing one Redis
server see each other's events even on different databases. Only auction ids with
local subscribers are forwarded.
"""

import asyncio
import logging
from uuid import UUID

from pydantic import ValidationError
from redis.asyncio import Redis

from app.contract import auction_event_adapter
from app.realtime.hub import Hub

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "auction:"
CHANNEL_SUFFIX = ":events"
# Matches app.contract.auction_events_channel(auction_id) for every auction.
EVENTS_PATTERN = f"{CHANNEL_PREFIX}*{CHANNEL_SUFFIX}"

POLL_SECONDS = 5.0
HEALTH_CHECK_SECONDS = 15
MIN_BACKOFF_SECONDS = 0.1
MAX_BACKOFF_SECONDS = 5.0


def auction_id_from_channel(channel: str) -> UUID | None:
    if not (channel.startswith(CHANNEL_PREFIX) and channel.endswith(CHANNEL_SUFFIX)):
        return None
    try:
        return UUID(channel[len(CHANNEL_PREFIX) : -len(CHANNEL_SUFFIX)])
    except ValueError:
        return None


class Listener:
    def __init__(self, redis_url: str, hub: Hub, *, client_name: str) -> None:
        self.redis_url = redis_url
        self.hub = hub
        # Shows up in CLIENT LIST, which tells instances' connections apart.
        self.client_name = client_name

    async def run(self) -> None:
        """Listen until cancelled, reconnecting with backoff whenever Redis is lost."""
        backoff = MIN_BACKOFF_SECONDS
        redis = Redis.from_url(
            self.redis_url,
            decode_responses=True,
            client_name=self.client_name,
            health_check_interval=HEALTH_CHECK_SECONDS,
            socket_keepalive=True,
        )
        try:
            while True:
                try:
                    async with redis.pubsub() as pubsub:
                        await pubsub.psubscribe(EVENTS_PATTERN)
                        while True:
                            message = await pubsub.get_message(timeout=POLL_SECONDS)
                            if message is None:
                                continue
                            if message["type"] == "psubscribe":
                                logger.info("Subscribed to auction events")
                                self.hub.reset()
                                backoff = MIN_BACKOFF_SECONDS
                            elif message["type"] == "pmessage":
                                self.handle(message["channel"], message["data"])
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "Lost the auction events subscription; retrying in %.1fs",
                        backoff,
                        exc_info=True,
                    )
                    self.hub.mark_down()
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
        finally:
            self.hub.mark_down()
            await redis.aclose()

    def handle(self, channel: str, data: str) -> None:
        """Validate one published event against the contract, then hand it to the hub."""
        auction_id = auction_id_from_channel(channel)
        if auction_id is None:
            logger.warning("Ignored a message on unexpected channel %r", channel)
            return
        try:
            event = auction_event_adapter.validate_json(data)
        except ValidationError:
            logger.warning("Dropped a malformed event on %s: %.200r", channel, data)
            return
        if event.auction_id != auction_id:
            logger.warning("Dropped an event for auction %s sent on %s", event.auction_id, channel)
            return
        self.hub.dispatch(event)
