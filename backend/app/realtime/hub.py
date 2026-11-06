"""This instance's WebSocket subscribers, and the fan-out of events to them.

Each socket gets a ``Subscription``: a bounded queue the listener fills and the
socket's own send loop drains, so a slow client never holds up the others. The hub
is in-process connection state only; everything it forwards comes from Redis.

Events go out in seq order. Two bids saved at nearly the same moment can publish
out of order (the later seq's commit finished first), so an event that skips a seq
is held for up to a short reorder window while the missing ones arrive. Without it,
every open page would see the gap and refetch the snapshot at the same moment.

Close codes the server sends (CLAUDE.md section 10):

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
# How long an event that skips a seq waits for the missing ones before going out.
REORDER_WINDOW_SECONDS = 0.1


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
        # Events that skipped a seq, waiting for the missing ones: serialised messages
        # by seq (a list, since an update and a close can share one).
        self.held: dict[int, list[str]] = {}
        self.release: asyncio.TimerHandle | None = None

    def forward(self, seq: int, message: str) -> None:
        self.last_seq = seq if self.last_seq is None else max(self.last_seq, seq)
        for subscription in list(self.subscriptions):
            subscription.offer(message)

    def forward_following(self) -> None:
        """Forward held events that now follow on from last_seq, in order."""
        while self.last_seq is not None and self.last_seq + 1 in self.held:
            seq = self.last_seq + 1
            for message in self.held.pop(seq):
                self.forward(seq, message)
        if not self.held:
            self.cancel_release()

    def forward_all_held(self) -> None:
        for seq in sorted(self.held):
            for message in self.held.pop(seq):
                self.forward(seq, message)
        self.cancel_release()

    def cancel_release(self) -> None:
        if self.release is not None:
            self.release.cancel()
            self.release = None


class Hub:
    def __init__(self, reorder_window_seconds: float = REORDER_WINDOW_SECONDS) -> None:
        self._auctions: dict[UUID, _AuctionSubscribers] = {}
        self._ready = asyncio.Event()
        self._reorder_window = reorder_window_seconds

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
            group.cancel_release()
            del self._auctions[subscription.auction_id]

    def dispatch(self, event: AuctionUpdate | AuctionClosed) -> None:
        """Forward an event to the auction's local subscribers in seq order, never
        going back in seq.

        - The next seq (or an equal one: one state change can produce both an
          auction_update and an auction_closed) goes out at once, followed by any
          held events it lets through.
        - A seq further ahead is held for up to the reorder window while the missing
          ones arrive. If they don't, the held events go out anyway; the client sees
          the gap and resyncs.
        - A seq older than one already forwarded is dropped: it arrived after the
          window, and forwarding it would make the high bid go backwards.
        - The first event a group sees is its baseline and goes out at once.
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
        message = event.model_dump_json()
        if group.last_seq is None or event.seq <= group.last_seq + 1:
            group.forward(event.seq, message)
            group.forward_following()
            return

        group.held.setdefault(event.seq, []).append(message)
        if sum(len(messages) for messages in group.held.values()) >= MAX_PENDING_MESSAGES:
            self._give_up_waiting(event.auction_id, group)
        elif group.release is None:
            group.release = asyncio.get_running_loop().call_later(
                self._reorder_window, self._give_up_waiting, event.auction_id, group
            )

    def _give_up_waiting(self, auction_id: UUID, group: _AuctionSubscribers) -> None:
        """Forward held events despite the gap before them; clients will resync."""
        group.release = None
        if self._auctions.get(auction_id) is not group or not group.held:
            return  # closed, or replaced by a new group, since the hold started
        missing = (group.last_seq or 0) + 1
        logger.warning(
            "Gap not filled for auction %s: seq %d missing, forwarding seq %d onward",
            auction_id,
            missing,
            min(group.held),
        )
        group.forward_all_held()

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
            group.cancel_release()
            for subscription in group.subscriptions:
                subscription.close(code, reason)
        self._auctions.clear()
