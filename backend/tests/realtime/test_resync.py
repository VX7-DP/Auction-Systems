"""A client that misses an event, or reconnects, resyncs from the REST snapshot.

``Viewer`` is the reference client algorithm from section 4: open the socket, then
fetch the snapshot; apply an event whose seq is one past the last; ignore an event at
or below it; on a gap, fetch the snapshot again.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from httpx_ws import AsyncWebSocketSession

from tests.realtime.conftest import RECEIVE_TIMEOUT, FakeEngine, WsConnect


@dataclass
class Viewer:
    http: httpx.AsyncClient
    auction_id: uuid.UUID
    state: dict[str, Any] = field(default_factory=dict)
    resyncs: int = 0

    @property
    def seq(self) -> int:
        return self.state["seq"]

    async def resync(self) -> None:
        response = await self.http.get(f"/api/auctions/{self.auction_id}")
        response.raise_for_status()
        self.state = response.json()
        self.resyncs += 1

    async def handle_next(self, ws: AsyncWebSocketSession) -> None:
        event = await ws.receive_json(timeout=RECEIVE_TIMEOUT)
        if event["seq"] <= self.seq:
            return
        if event["seq"] > self.seq + 1:
            await self.resync()
            return
        self.state.update(
            seq=event["seq"],
            high_amount=event["high_amount"],
            high_bidder_name=event["high_bidder_name"],
            status=event["status"],
        )


async def test_a_client_that_misses_an_event_resyncs_from_the_snapshot(
    client: httpx.AsyncClient, ws_connect: WsConnect, engine: FakeEngine
) -> None:
    auction_id = await engine.create_auction(title="Brass lamp", start_price=1_000)
    viewer = Viewer(client, auction_id)
    async with ws_connect(f"/ws/auctions/{auction_id}") as ws:
        await viewer.resync()
        assert viewer.seq == 0

        await engine.bid(auction_id, 1_100, bidder_name="Asha")
        await viewer.handle_next(ws)
        assert (viewer.seq, viewer.state["high_amount"]) == (1, 1_100)

        # Seq 2 is accepted and saved, but its event never reaches this client.
        await engine.bid(auction_id, 1_200, bidder_name="Ben", publish=False)
        await engine.bid(auction_id, 1_300, bidder_name="Chloe")
        await viewer.handle_next(ws)

    assert viewer.resyncs == 2
    assert viewer.seq == 3
    assert viewer.state["high_amount"] == 1_300
    assert viewer.state["high_bidder_name"] == "Chloe"
    assert [bid["seq"] for bid in viewer.state["recent_bids"]] == [3, 2, 1]
    assert viewer.state["recent_bids"][1]["bidder_name"] == "Ben"


async def test_a_client_that_reconnects_resyncs_then_follows_live_events(
    client: httpx.AsyncClient, ws_connect: WsConnect, engine: FakeEngine
) -> None:
    auction_id = await engine.create_auction()
    viewer = Viewer(client, auction_id)
    async with ws_connect(f"/ws/auctions/{auction_id}") as ws:
        await viewer.resync()
        await engine.bid(auction_id, 1_100)
        await viewer.handle_next(ws)

    # Disconnected: these two are missed entirely.
    await engine.bid(auction_id, 1_200)
    await engine.bid(auction_id, 1_300)

    async with ws_connect(f"/ws/auctions/{auction_id}") as ws:
        await viewer.resync()
        assert (viewer.seq, viewer.state["high_amount"]) == (3, 1_300)

        await engine.bid(auction_id, 1_400, bidder_name="Diego")
        await viewer.handle_next(ws)

    assert viewer.seq == 4
    assert viewer.state["high_amount"] == 1_400
    assert viewer.state["high_bidder_name"] == "Diego"
