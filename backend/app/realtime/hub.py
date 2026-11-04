"""This instance's WebSocket subscribers, and the fan-out of events to them.

Each socket gets a ``Subscription``: a bounded queue the listener fills and the
socket's own send loop drains, so a slow client never holds up the others. The hub
is in-process connection state only; everything it forwards comes from Redis.

Close codes the server sends (not in section 10 yet):

- ``CLOSE_NOT_FOUND`` (4404): no such auction. Don't reconnect.
- ``CLOSE_SERVICE_RESTART`` (1012): this server lost, regained or is closing its Redis
  subscription, so events may have been missed. Reconnect and resync.
- ``CLOSE_TRY_AGAIN`` (1013): not subscribed to Redis right now. Reconnect with backoff.
- ``CLOSE_TOO_SLOW`` (1008): the client fell too far behind. Reconnect and resync.
"""

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from app.contract import AuctionClosed, AuctionUpdate

logger = logging.getLogger(__name__)

CLOSE_NOT_FOUND = 4404
CLOSE_SERVICE_RESTART = 1012
CLOSE_TRY_AGAIN = 1013
CLOSE_TOO_SLOW = 1008

MAX_PENDING_MESSAGES = 100


@dataclass(frozen=True)
class Close:
    """Queued after a subscription is closed: the send loop closes the socket with it."""

    code: int
    reason: str


class Subscription:
    def __init__(self, auction_id: UUID, max_pending: int = MAX_PENDING_MESSAGES) -> None:
        self.auction_id = auction_id
        self._max_pending = max_pending
        self._queue: asyncio.Queue[str | Close] = asyncio.Queue()
        self.closed = False

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def offer(self, message: str) -> None:
        """Queue a message, or close the subscription if the client has fallen behind."""
        if self.closed:
            return
        if self._queue.qsize() >= self._max_pending:
            self.close(CLOSE_TOO_SLOW, "too slow")
            return
        self._queue.put_nowait(message)

    def close(self, code: int, reason: str) -> None:
        """Drop anything pending and queue the close: the client resyncs when it reconnects."""
        if self.closed:
            return
        self.closed = True
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(Close(code, reason))

    async def next(self) -> str | Close:
        return await self._queue.get()


class _AuctionSubscribers:
    def __init__(self) -> None:
        self.subscriptions: set[Subscription] = set()
        # The highest seq forwarded since the first subscriber of this group joined.
        self.last_seq: int | None = None


class Hub:
    def __init__(self) -> None:
        self._auctions: dict[UUID, _AuctionSubscribers] = {}
        self._ready = asyncio.Event()

    @property
    def ready(self) -> bool:
        """True while the listener is subscribed to Redis, so no event can be missed."""
        return self._ready.is_set()

    async def wait_ready(self, seconds: float) -> bool:
        try:
            async with asyncio.timeout(seconds):
                await self._ready.wait()
        except TimeoutError:
            return False
        return True

    def subscribe(self, auction_id: UUID) -> Subscription:
        subscription = Subscription(auction_id)
        self._auctions.setdefault(auction_id, _AuctionSubscribers()).subscriptions.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        group = self._auctions.get(subscription.auction_id)
        if group is None:
            return
        group.subscriptions.discard(subscription)
        if not group.subscriptions:
            del self._auctions[subscription.auction_id]

    def dispatch(self, event: AuctionUpdate | AuctionClosed) -> None:
        """Forward an event to the auction's local subscribers, never going back in seq.

        A strictly older seq is dropped: its bid committed after a later one, and
        forwarding it would make the high bid go backwards. The client sees the gap
        and resyncs. An equal seq is kept, since one state change can produce both
        an auction_update and an auction_closed.
        """
        group = self._auctions.get(event.auction_id)
        if group is None:
            return
        if group.last_seq is not None and event.seq < group.last_seq:
            logger.warning(
                "Dropped out-of-order event for auction %s: seq %d after %d",
                event.auction_id,
                event.seq,
                group.last_seq,
            )
            return
        group.last_seq = event.seq
        message = event.model_dump_json()
        for subscription in list(group.subscriptions):
            subscription.offer(message)

    def reset(self) -> None:
        """The listener has just (re)subscribed: start over from here.

        Sockets opened before this point may have missed events, so they are closed
        and their clients resync. Seq tracking restarts too, since a Redis restart
        can bring an auction back at a lower seq.
        """
        self.close_all(CLOSE_SERVICE_RESTART, "reconnect and resync")
        self._ready.set()

    def mark_down(self) -> None:
        """The listener lost Redis: refuse new sockets and close the current ones."""
        self._ready.clear()
        self.close_all(CLOSE_SERVICE_RESTART, "reconnect and resync")

    def close_all(self, code: int, reason: str) -> None:
        for group in self._auctions.values():
            for subscription in group.subscriptions:
                subscription.close(code, reason)
        self._auctions.clear()
