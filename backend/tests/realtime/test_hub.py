"""The per-instance hub: subscriptions, slow consumers and resets. No Redis needed."""

import asyncio
import json
import uuid

from app.contract import AuctionClosed
from app.realtime.hub import (
    CLOSE_SERVICE_RESTART,
    CLOSE_TOO_SLOW,
    MAX_PENDING_MESSAGES,
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


# --- Reorder window: an event that skips a seq waits briefly for the missing ones ---

WINDOW = 0.05


def queued_seqs(subscription: Subscription) -> list[int]:
    """Drains what is queued right now and returns the seqs, in delivery order."""
    seqs = []
    while subscription.pending:
        item = subscription._queue.get_nowait()
        assert isinstance(item, str), item
        seqs.append(json.loads(item)["seq"])
    return seqs


def window_hub() -> tuple[Hub, uuid.UUID, Subscription]:
    hub = Hub(reorder_window_seconds=WINDOW)
    hub.reset()
    auction_id = uuid.uuid4()
    return hub, auction_id, hub.subscribe(auction_id)


async def test_a_reordered_pair_is_delivered_in_order() -> None:
    """Commits can finish out of order, so publishes can too: seq 3 before seq 2."""
    hub, auction_id, subscription = window_hub()
    for seq in (1, 3, 2):
        hub.dispatch(update_event(auction_id, seq))

    # No waiting: 2 fills the gap, so 2 and 3 go out together at once.
    assert queued_seqs(subscription) == [1, 2, 3]


async def test_held_events_go_out_as_soon_as_the_gap_fills() -> None:
    hub, auction_id, subscription = window_hub()
    hub.dispatch(update_event(auction_id, 1))
    hub.dispatch(update_event(auction_id, 4))
    hub.dispatch(update_event(auction_id, 3))
    assert queued_seqs(subscription) == [1]

    hub.dispatch(update_event(auction_id, 2))
    assert queued_seqs(subscription) == [2, 3, 4]


async def test_a_real_gap_is_forwarded_once_the_window_ends() -> None:
    """A seq that never comes (a failed save, a seq jump after a reload) still lets the
    later events through; the client sees the gap and resyncs."""
    hub, auction_id, subscription = window_hub()
    hub.dispatch(update_event(auction_id, 1))
    hub.dispatch(update_event(auction_id, 3))

    await asyncio.sleep(WINDOW / 2)
    assert queued_seqs(subscription) == [1]

    await asyncio.sleep(WINDOW)
    assert queued_seqs(subscription) == [3]


async def test_an_event_later_than_the_window_is_still_dropped() -> None:
    hub, auction_id, subscription = window_hub()
    hub.dispatch(update_event(auction_id, 1))
    hub.dispatch(update_event(auction_id, 3))
    await asyncio.sleep(WINDOW * 2)

    # Forwarding 2 now would make the high bid go backwards.
    hub.dispatch(update_event(auction_id, 2))
    assert queued_seqs(subscription) == [1, 3]


async def test_an_equal_seq_is_still_kept_while_reordering() -> None:
    """One state change can produce both an auction_update and an auction_closed."""
    hub, auction_id, subscription = window_hub()
    hub.dispatch(update_event(auction_id, 1))
    hub.dispatch(update_event(auction_id, 3))
    hub.dispatch(
        AuctionClosed(auction_id=auction_id, seq=3, winner_id=None, winner_name=None, amount=None)
    )
    hub.dispatch(update_event(auction_id, 2))

    assert queued_seqs(subscription) == [1, 2, 3, 3]


async def test_the_first_event_a_new_group_sees_is_not_held() -> None:
    """A new group has no seq to compare with: the first event is the baseline."""
    hub, auction_id, subscription = window_hub()
    hub.dispatch(update_event(auction_id, 42))

    assert queued_seqs(subscription) == [42]


async def test_closing_cancels_a_pending_hold() -> None:
    hub, auction_id, subscription = window_hub()
    hub.dispatch(update_event(auction_id, 1))
    hub.dispatch(update_event(auction_id, 3))
    assert queued_seqs(subscription) == [1]

    hub.close_all(CLOSE_SERVICE_RESTART, "reconnect and resync")
    await asyncio.sleep(WINDOW * 2)

    assert await next_now(subscription) == Close(CLOSE_SERVICE_RESTART, "reconnect and resync")
    assert subscription.pending == 0


async def test_the_last_unsubscribe_cancels_a_pending_hold() -> None:
    hub, auction_id, subscription = window_hub()
    hub.dispatch(update_event(auction_id, 1))
    hub.dispatch(update_event(auction_id, 3))
    hub.unsubscribe(subscription)

    # A new viewer starts a new group: the old hold must not leak into it.
    newcomer = hub.subscribe(auction_id)
    await asyncio.sleep(WINDOW * 2)

    assert queued_seqs(newcomer) == []
    assert queued_seqs(subscription) == [1]


async def test_too_many_held_events_are_released_at_once() -> None:
    hub, auction_id, subscription = window_hub()
    hub.dispatch(update_event(auction_id, 1))
    assert queued_seqs(subscription) == [1]
    for seq in range(3, 3 + MAX_PENDING_MESSAGES):
        hub.dispatch(update_event(auction_id, seq))

    # Released without waiting for the window, in seq order.
    assert queued_seqs(subscription) == list(range(3, 3 + MAX_PENDING_MESSAGES))
