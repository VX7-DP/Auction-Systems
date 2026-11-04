"""The per-instance hub: subscriptions, slow consumers and resets. No Redis needed."""

import asyncio
import uuid

from app.realtime.hub import (
    CLOSE_SERVICE_RESTART,
    CLOSE_TOO_SLOW,
    Close,
    Hub,
    Subscription,
)
from tests.realtime.conftest import update_event


async def next_now(subscription: Subscription) -> str | Close:
    async with asyncio.timeout(1):
        return await subscription.next()


async def test_a_subscription_that_falls_behind_is_closed_as_too_slow() -> None:
    subscription = Subscription(uuid.uuid4(), max_pending=3)
    for n in range(4):
        subscription.offer(f"message {n}")

    assert subscription.closed
    # Pending messages are dropped: the client resyncs after reconnecting anyway.
    assert await next_now(subscription) == Close(CLOSE_TOO_SLOW, "too slow")


async def test_dispatch_reaches_every_subscription_for_that_auction_only() -> None:
    hub = Hub()
    hub.reset()
    auction_id = uuid.uuid4()
    first, second = hub.subscribe(auction_id), hub.subscribe(auction_id)
    elsewhere = hub.subscribe(uuid.uuid4())
    event = update_event(auction_id, 1)

    hub.dispatch(event)

    assert await next_now(first) == event.model_dump_json()
    assert await next_now(second) == event.model_dump_json()
    assert elsewhere.pending == 0


async def test_unsubscribed_connections_get_nothing() -> None:
    hub = Hub()
    hub.reset()
    auction_id = uuid.uuid4()
    subscription = hub.subscribe(auction_id)
    hub.unsubscribe(subscription)

    hub.dispatch(update_event(auction_id, 1))

    assert subscription.pending == 0


async def test_mark_down_closes_everything_and_stops_accepting() -> None:
    hub = Hub()
    hub.reset()
    subscription = hub.subscribe(uuid.uuid4())

    hub.mark_down()

    assert not hub.ready
    assert await next_now(subscription) == Close(CLOSE_SERVICE_RESTART, "reconnect and resync")


async def test_reset_closes_existing_subscriptions_and_forgets_seq() -> None:
    hub = Hub()
    hub.reset()
    auction_id = uuid.uuid4()
    old = hub.subscribe(auction_id)
    hub.dispatch(update_event(auction_id, 5))

    hub.reset()
    new = hub.subscribe(auction_id)
    # After a Redis restart seq may restart lower; the new stream must not hold it back.
    hub.dispatch(update_event(auction_id, 2))

    assert hub.ready
    assert await next_now(old) == Close(CLOSE_SERVICE_RESTART, "reconnect and resync")
    assert new.pending == 1
