"""WebSocket fan-out: /ws/auctions/{id} carries the events published on the auction's channel."""

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from httpx_ws import WebSocketDisconnect
from redis.asyncio import Redis

from app.contract import AuctionClosed, auction_event_adapter, auction_events_channel
from app.realtime.hub import (
    CLOSE_NOT_FOUND,
    CLOSE_SERVICE_RESTART,
    CLOSE_TRY_AGAIN,
    Hub,
)
from app.realtime.listener import Listener
from tests.conftest import TEST_REDIS_URL
from tests.realtime.conftest import RECEIVE_TIMEOUT, FakeEngine, WsConnect, update_event, wait_until


def ws_url(auction_id: uuid.UUID) -> str:
    return f"/ws/auctions/{auction_id}"


async def test_two_clients_see_the_same_updates_in_seq_order(
    ws_connect: WsConnect, engine: FakeEngine
) -> None:
    auction_id = await engine.create_auction()
    async with (
        ws_connect(ws_url(auction_id)) as first,
        ws_connect(ws_url(auction_id)) as second,
    ):
        sent = [await engine.bid(auction_id, 1_000 + 100 * n) for n in range(1, 6)]

        for ws in (first, second):
            received = [await ws.receive_json(timeout=RECEIVE_TIMEOUT) for _ in sent]
            assert [message["seq"] for message in received] == [1, 2, 3, 4, 5]
            assert [auction_event_adapter.validate_python(m) for m in received] == sent


async def test_a_client_gets_only_its_own_auctions_events(
    ws_connect: WsConnect, engine: FakeEngine
) -> None:
    watched = await engine.create_auction(title="Watched")
    other = await engine.create_auction(title="Other")
    async with ws_connect(ws_url(watched)) as ws:
        await engine.bid(other, 2_000)
        mine = await engine.bid(watched, 1_500)

        message = await ws.receive_json(timeout=RECEIVE_TIMEOUT)
        assert auction_event_adapter.validate_python(message) == mine
        with pytest.raises(TimeoutError):
            await ws.receive_json(timeout=0.3)


async def test_strictly_older_seq_is_dropped_and_equal_seq_is_kept(
    ws_connect: WsConnect, engine: FakeEngine
) -> None:
    """Commits can finish out of order, so publishes can too. A late event must not make
    the high bid go backwards; the client sees the gap and resyncs instead."""
    auction_id = await engine.create_auction()
    async with ws_connect(ws_url(auction_id)) as ws:
        for seq in (1, 3, 2, 3, 4):
            await engine.publish(update_event(auction_id, seq, amount=1_000 + seq))

        received = [(await ws.receive_json(timeout=RECEIVE_TIMEOUT))["seq"] for _ in range(4)]
        assert received == [1, 3, 3, 4]
        with pytest.raises(TimeoutError):
            await ws.receive_json(timeout=0.3)


async def test_auction_closed_is_forwarded(ws_connect: WsConnect, engine: FakeEngine) -> None:
    auction_id = await engine.create_auction()
    async with ws_connect(ws_url(auction_id)) as ws:
        closed = AuctionClosed(
            auction_id=auction_id,
            seq=1,
            winner_id=uuid.uuid4(),
            winner_name="Asha",
            amount=1_500,
        )
        await engine.publish(closed)

        message = await ws.receive_json(timeout=RECEIVE_TIMEOUT)
        assert auction_event_adapter.validate_python(message) == closed


async def test_malformed_events_are_dropped_and_the_stream_continues(
    ws_connect: WsConnect, engine: FakeEngine, redis: Redis
) -> None:
    auction_id = await engine.create_auction()
    async with ws_connect(ws_url(auction_id)) as ws:
        channel = auction_events_channel(auction_id)
        await redis.publish(channel, "not json")
        await redis.publish(channel, '{"type": "auction_update", "seq": 1}')
        # A valid event for another auction, published on this auction's channel.
        await redis.publish(channel, update_event(uuid.uuid4(), 1).model_dump_json())
        good = await engine.bid(auction_id, 1_500)

        message = await ws.receive_json(timeout=RECEIVE_TIMEOUT)
        assert auction_event_adapter.validate_python(message) == good


async def test_unknown_auction_is_closed_with_not_found(ws_connect: WsConnect) -> None:
    async with ws_connect(ws_url(uuid.uuid4())) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            await ws.receive_json(timeout=RECEIVE_TIMEOUT)

    assert closed.value.code == CLOSE_NOT_FOUND


async def test_connections_are_refused_while_the_listener_is_down(
    ws_connect: WsConnect, engine: FakeEngine, hub: Hub
) -> None:
    auction_id = await engine.create_auction()
    hub.mark_down()
    try:
        async with ws_connect(ws_url(auction_id)) as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                await ws.receive_json(timeout=RECEIVE_TIMEOUT)
    finally:
        hub.reset()

    assert closed.value.code == CLOSE_TRY_AGAIN


async def test_losing_the_pubsub_connection_closes_sockets_and_recovers(
    app: FastAPI, ws_connect: WsConnect, engine: FakeEngine, redis: Redis, hub: Hub
) -> None:
    """Events published while the listener is cut off are lost, so every socket is closed
    and its client reconnects and resyncs. A Redis restart does the same."""
    auction_id = await engine.create_auction()
    listener: Listener = app.state.realtime_listener
    async with ws_connect(ws_url(auction_id)) as ws:
        killed = 0
        for client in await redis.client_list(_type="pubsub"):
            if client["name"] == listener.client_name:
                killed += await redis.client_kill_filter(_id=client["id"])
        assert killed == 1

        with pytest.raises(WebSocketDisconnect) as closed:
            await ws.receive_json(timeout=RECEIVE_TIMEOUT)
        assert closed.value.code == CLOSE_SERVICE_RESTART

    await wait_until(lambda: hub.ready)
    async with ws_connect(ws_url(auction_id)) as ws:
        sent = await engine.bid(auction_id, 1_500)

        message = await ws.receive_json(timeout=RECEIVE_TIMEOUT)
        assert auction_event_adapter.validate_python(message) == sent


async def test_every_instance_receives_every_event(redis: Redis) -> None:
    """Two hub and listener pairs stand in for two app instances on one Redis."""
    auction_id = uuid.uuid4()
    hubs = [Hub(), Hub()]
    listeners = [
        Listener(TEST_REDIS_URL, hub, client_name=f"test-{n}") for n, hub in enumerate(hubs)
    ]
    tasks = [asyncio.create_task(listener.run()) for listener in listeners]
    try:
        for hub in hubs:
            await hub.wait_ready(RECEIVE_TIMEOUT)
        subscriptions = [hub.subscribe(auction_id) for hub in hubs]

        events = [update_event(auction_id, seq) for seq in (1, 2)]
        for event in events:
            await redis.publish(auction_events_channel(auction_id), event.model_dump_json())

        for subscription in subscriptions:
            async with asyncio.timeout(RECEIVE_TIMEOUT):
                received = [await subscription.next() for _ in events]
            assert received == [event.model_dump_json() for event in events]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
