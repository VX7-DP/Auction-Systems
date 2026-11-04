"""Browser stand-ins for the end-to-end tests: a live auction page and a signed-in guest.

``Viewer`` follows the client rules of CLAUDE.md sections 4 and 10, like the front end's
``src/live/auctionFeed.ts``: open the WebSocket, then fetch the snapshot; apply events
strictly in seq order; refetch the snapshot after a seq gap or a reconnect; show
"you've been outbid" when the high bidder changes from this guest to someone else.
"""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import httpx
from httpx_ws import HTTPXWSException, WebSocketDisconnect, aconnect_ws

from app.realtime.hub import CLOSE_NOT_FOUND

STATE_FIELDS = ("seq", "status", "high_amount", "high_bidder_id", "high_bidder_name")
REQUEST_TIMEOUT_SECONDS = 20.0
RECONNECT_MIN_SECONDS = 0.1
RECONNECT_MAX_SECONDS = 2.0


@dataclass(frozen=True)
class Outbid:
    """One "you've been outbid" notice, raised at the state change with this seq."""

    seq: int
    by_id: str | None
    by_name: str | None
    by_is_bot: bool | None
    amount: int | None


@dataclass(frozen=True)
class SeenBid:
    """A bid announced by an event. Events go out only after the Postgres commit."""

    seq: int
    amount: int
    bidder_id: str
    bidder_name: str
    is_bot: bool


@dataclass
class Viewer:
    """One open auction page."""

    base_url: str
    auction_id: str
    me: str | None = None
    state: dict[str, Any] | None = None
    applied: list[dict[str, Any]] = field(default_factory=list)
    bids: list[SeenBid] = field(default_factory=list)
    outbids: list[Outbid] = field(default_factory=list)
    close_codes: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Sockets opened, and sockets whose snapshot has been applied.
    connects: int = 0
    synced: int = 0
    resyncs: int = 0
    gaps: int = 0
    # Snapshots older than the state already shown: seq went backwards on the server.
    stale_snapshots: int = 0
    closed: dict[str, Any] | None = None
    closed_at: datetime | None = None
    _connected: bool = False
    _pending: dict[int, dict[str, Any]] = field(default_factory=dict)
    _task: asyncio.Task[None] | None = None

    @property
    def seq(self) -> int:
        return -1 if self.state is None else self.state["seq"]

    @property
    def live(self) -> bool:
        return self._connected and self.synced == self.connects

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"viewer-{self.me}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def wait_until(self, condition: Callable[[], bool], *, within: float, what: str) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within
        while not condition():
            if self._task is not None and self._task.done() and self.closed is None:
                self._task.result()  # raises what stopped the page
                raise AssertionError(f"the page stopped before {what}\n{self.report()}")
            if loop.time() > deadline:
                raise AssertionError(
                    f"timed out after {within}s waiting for {what}\n{self.report()}"
                )
            await asyncio.sleep(0.05)

    def report(self) -> str:
        return (
            f"viewer {self.me}: state={self.state} connects={self.connects} synced={self.synced}"
            f" resyncs={self.resyncs} gaps={self.gaps} close_codes={self.close_codes}"
            f" errors={self.errors[-5:]} applied_seqs={[e['seq'] for e in self.applied]}"
            f" outbids={self.outbids}"
        )

    # --- the page's loop ---------------------------------------------------------------

    async def _run(self) -> None:
        backoff = RECONNECT_MIN_SECONDS
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=REQUEST_TIMEOUT_SECONDS
        ) as http:
            while True:
                try:
                    async with aconnect_ws(f"/ws/auctions/{self.auction_id}", http) as ws:
                        self.connects += 1
                        self._connected = True
                        # Events published before the socket went live are only in a snapshot.
                        await self._resync(http)
                        self.synced += 1
                        backoff = RECONNECT_MIN_SECONDS
                        while self.closed is None:
                            await self._on_event(http, await ws.receive_json())
                    return  # the auction ended: no more live updates will come
                # httpx-ws can raise these inside an exception group as the session closes.
                except* WebSocketDisconnect as closes:
                    codes = [closed.code for closed in _leaves(closes)]
                    self.close_codes += codes
                    if CLOSE_NOT_FOUND in codes:
                        raise AssertionError(f"auction {self.auction_id} not found") from None
                except* (HTTPXWSException, httpx.HTTPError, OSError) as errors:
                    self.errors += [repr(error) for error in _leaves(errors)]
                finally:
                    self._connected = False
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)

    async def _resync(self, http: httpx.AsyncClient) -> None:
        response = await http.get(f"/api/auctions/{self.auction_id}")
        response.raise_for_status()
        snapshot = response.json()
        self.resyncs += 1
        if self.state is not None and snapshot["seq"] < self.state["seq"]:
            self.stale_snapshots += 1
        elif self.state is None or snapshot["seq"] > self.state["seq"]:
            state = {name: snapshot[name] for name in STATE_FIELDS}
            state["high_is_bot"] = snapshot["high_is_bot"]
            self._change(state)
            if state["status"] == "closed" and self.closed is None:
                self._mark_closed(
                    {
                        "seq": state["seq"],
                        "winner_id": state["high_bidder_id"],
                        "winner_name": state["high_bidder_name"],
                        "amount": state["high_amount"],
                        "via": "snapshot",
                    }
                )
        self._drain()

    async def _on_event(self, http: httpx.AsyncClient, event: dict[str, Any]) -> None:
        if event.get("auction_id") != self.auction_id or not isinstance(event.get("seq"), int):
            self.errors.append(f"unexpected message {event!r}")
            return
        if event["seq"] <= self.seq:
            return  # a duplicate, or already in the snapshot
        self._pending[event["seq"]] = event
        self._drain()
        if self._pending:
            self.gaps += 1
            await self._resync(http)

    def _drain(self) -> None:
        for seq in [seq for seq in self._pending if seq <= self.seq]:
            del self._pending[seq]
        while self.closed is None and self.seq + 1 in self._pending:
            self._apply(self._pending.pop(self.seq + 1))

    def _apply(self, event: dict[str, Any]) -> None:
        previous = self.state or {}
        if event["type"] == "auction_update":
            state = {name: event[name] for name in STATE_FIELDS}
            state["high_is_bot"] = event["is_bot"]
            if state["high_amount"] is not None and state["high_amount"] != previous.get(
                "high_amount"
            ):
                self.bids.append(
                    SeenBid(
                        seq=event["seq"],
                        amount=event["high_amount"],
                        bidder_id=event["high_bidder_id"],
                        bidder_name=event["high_bidder_name"],
                        is_bot=event["is_bot"],
                    )
                )
        else:
            same_winner = event["winner_id"] == previous.get("high_bidder_id")
            state = {
                "seq": event["seq"],
                "status": "closed",
                "high_amount": event["amount"],
                "high_bidder_id": event["winner_id"],
                "high_bidder_name": event["winner_name"],
                "high_is_bot": previous.get("high_is_bot") if same_winner else None,
            }
        self.applied.append(event)
        self._change(state)
        if event["type"] == "auction_closed":
            self._mark_closed({**event, "via": "event"})

    def _change(self, state: dict[str, Any]) -> None:
        previous, self.state = self.state, state
        if (
            self.me is not None
            and previous is not None
            and previous["high_bidder_id"] == self.me
            and state["high_bidder_id"] not in (None, self.me)
        ):
            self.outbids.append(
                Outbid(
                    seq=state["seq"],
                    by_id=state["high_bidder_id"],
                    by_name=state["high_bidder_name"],
                    by_is_bot=state["high_is_bot"],
                    amount=state["high_amount"],
                )
            )

    def _mark_closed(self, closed: dict[str, Any]) -> None:
        self.closed = closed
        self.closed_at = datetime.now(UTC)


def _leaves(group: BaseExceptionGroup) -> list[Any]:
    leaves: list[Any] = []
    for error in group.exceptions:
        leaves += _leaves(error) if isinstance(error, BaseExceptionGroup) else [error]
    return leaves


@dataclass(frozen=True)
class Attempt:
    """One bid request and what came of it. status "unknown": no answer arrived."""

    bid_id: str
    bidder_id: str
    amount: int
    status: str
    reason: str | None = None
    seq: int | None = None
    error: str | None = None


@dataclass
class Guest:
    """A signed-in guest with their own cookie jar and their own open auction page."""

    http: httpx.AsyncClient
    name: str
    bidder_id: str
    auction_id: str
    start_price: int
    viewer: Viewer

    def high(self) -> int:
        state = self.viewer.state
        if state is None or state["high_amount"] is None:
            return self.start_price
        return state["high_amount"]

    async def bid(self, amount: int, bid_id: str | None = None) -> Attempt:
        bid_id = bid_id or str(uuid.uuid4())
        attempt = Attempt(bid_id=bid_id, bidder_id=self.bidder_id, amount=amount, status="unknown")
        try:
            response = await self.http.post(
                f"/api/auctions/{self.auction_id}/bids", json={"bid_id": bid_id, "amount": amount}
            )
        except httpx.HTTPError as error:
            return replace(attempt, error=repr(error))
        # 503 carries status "error": the bid wasn't saved, retry with the same bid_id.
        assert response.status_code in (200, 503), response.text
        body = response.json()
        assert body["bid_id"] == bid_id, body
        return replace(
            attempt, status=body["status"], reason=body.get("reason"), seq=body.get("seq")
        )

    async def bid_above_high(self, step: int) -> Attempt:
        """Bid one step above the high bid this guest's page shows, like the one-tap
        button. A bot can get in first, so a too_low is retried a little higher."""
        floor = 0
        for _ in range(10):
            amount = max(self.high(), floor) + step
            attempt = await self.bid(amount)
            if attempt.status == "accepted":
                return attempt
            assert (attempt.status, attempt.reason) == ("rejected", "too_low"), attempt
            floor = amount
            await asyncio.sleep(0.1)
        raise AssertionError(f"{self.name} was outbid on every try\n{self.viewer.report()}")

    async def keep_bidding(self, stop: asyncio.Event, step: int) -> list[Attempt]:
        """Bid above the high bid every 0.2 s until ``stop`` is set."""
        attempts: list[Attempt] = []
        floor = 0
        while not stop.is_set():
            floor = max(self.high(), floor) + step
            attempts.append(await self.bid(floor))
            await asyncio.sleep(0.2)
        return attempts

    async def settle(self, attempt: Attempt) -> Attempt:
        """Retry a bid whose outcome is unknown or "error", reusing its bid_id, until it
        is accepted or rejected. A retry can never place the bid twice."""
        for _ in range(10):
            if attempt.status in ("accepted", "rejected"):
                return attempt
            await asyncio.sleep(1.0)
            attempt = await self.bid(attempt.amount, bid_id=attempt.bid_id)
        raise AssertionError(f"bid {attempt} never settled")
