"""GET /api/auctions and GET /api/auctions/{id}: live state from Redis, the rest from Postgres."""

import uuid
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis

from app.auctions.live_state import parse_live_state
from app.contract import AuctionSnapshot, AuctionSummary, auction_key
from app.resources import get_redis
from tests.realtime.conftest import FakeEngine, in_hours


async def snapshot(client: httpx.AsyncClient, auction_id: uuid.UUID) -> AuctionSnapshot:
    response = await client.get(f"/api/auctions/{auction_id}")
    assert response.status_code == 200, response.text
    return AuctionSnapshot.model_validate(response.json())


async def test_snapshot_of_an_auction_without_bids(
    client: httpx.AsyncClient, engine: FakeEngine
) -> None:
    end_at = in_hours(2)
    auction_id = await engine.create_auction(title="Brass lamp", start_price=1_000, end_at=end_at)

    response = await client.get(f"/api/auctions/{auction_id}")

    assert response.json() == {
        "id": str(auction_id),
        "title": "Brass lamp",
        "start_price": 1_000,
        "end_at": end_at.isoformat().replace("+00:00", "Z"),
        "status": "open",
        "seq": 0,
        "high_amount": None,
        "high_bidder_id": None,
        "high_bidder_name": None,
        "high_is_bot": None,
        "recent_bids": [],
    }


async def test_snapshot_takes_live_state_from_redis_and_history_from_postgres(
    client: httpx.AsyncClient, engine: FakeEngine, redis: Redis
) -> None:
    auction_id = await engine.create_auction()
    await engine.bid(auction_id, 1_100, bidder_name="Asha")
    top = await engine.bid(auction_id, 1_200, bidder_name="Riya (bot)", is_bot=True)
    # Live state Postgres doesn't have yet, e.g. a pause that couldn't be written.
    await redis.hset(auction_key(auction_id), mapping={"status": "paused", "seq": 3})

    result = await snapshot(client, auction_id)

    assert result.status == "paused"
    assert result.seq == 3
    assert (result.high_amount, result.high_bidder_id) == (1_200, top.high_bidder_id)
    assert (result.high_bidder_name, result.high_is_bot) == ("Riya (bot)", True)
    assert [(bid.seq, bid.amount, bid.is_bot) for bid in result.recent_bids] == [
        (2, 1_200, True),
        (1, 1_100, False),
    ]


async def test_snapshot_history_never_runs_ahead_of_its_seq(
    client: httpx.AsyncClient, engine: FakeEngine, redis: Redis
) -> None:
    """A bid committed between the Redis read and the Postgres read belongs to a later seq."""
    auction_id = await engine.create_auction()
    for amount in (1_100, 1_200, 1_300):
        await engine.bid(auction_id, amount)
    await redis.hset(auction_key(auction_id), "seq", 2)

    result = await snapshot(client, auction_id)

    assert result.seq == 2
    assert [bid.seq for bid in result.recent_bids] == [2, 1]


async def test_snapshot_falls_back_to_postgres_when_the_redis_hash_is_missing(
    client: httpx.AsyncClient, engine: FakeEngine, redis: Redis
) -> None:
    """After a Redis restart the hash is gone until the bid engine reloads it."""
    auction_id = await engine.create_auction()
    await engine.bid(auction_id, 1_100)
    top = await engine.bid(auction_id, 1_200, bidder_name="Riya (bot)", is_bot=True)
    await redis.delete(auction_key(auction_id))

    result = await snapshot(client, auction_id)

    assert (result.status, result.seq) == ("open", 2)
    assert (result.high_amount, result.high_bidder_id) == (1_200, top.high_bidder_id)
    assert (result.high_bidder_name, result.high_is_bot) == ("Riya (bot)", True)
    assert [bid.seq for bid in result.recent_bids] == [2, 1]


async def test_snapshot_falls_back_to_postgres_when_the_hash_is_unreadable(
    client: httpx.AsyncClient, engine: FakeEngine, redis: Redis
) -> None:
    auction_id = await engine.create_auction()
    await engine.bid(auction_id, 1_100)
    await redis.hset(auction_key(auction_id), "seq", "not a number")

    result = await snapshot(client, auction_id)

    assert (result.seq, result.high_amount) == (1, 1_100)


async def test_snapshot_and_list_fall_back_to_postgres_while_redis_is_down(
    app: FastAPI, client: httpx.AsyncClient, engine: FakeEngine
) -> None:
    auction_id = await engine.create_auction()
    await engine.bid(auction_id, 1_100)
    dead_redis = Redis.from_url("redis://127.0.0.1:1/0")  # nothing listens on port 1
    app.dependency_overrides[get_redis] = lambda: dead_redis
    try:
        result = await snapshot(client, auction_id)
        listed = (await client.get("/api/auctions")).json()
    finally:
        app.dependency_overrides.clear()
        await dead_redis.aclose()

    assert (result.status, result.seq, result.high_amount) == ("open", 1, 1_100)
    assert [(item["id"], item["high_amount"]) for item in listed] == [(str(auction_id), 1_100)]


async def test_snapshot_of_an_unknown_auction_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"/api/auctions/{uuid.uuid4()}")).status_code == 404


async def test_recent_bids_are_capped(client: httpx.AsyncClient, engine: FakeEngine) -> None:
    auction_id = await engine.create_auction()
    for n in range(25):
        await engine.bid(auction_id, 1_100 + n, publish=False)

    result = await snapshot(client, auction_id)

    assert [bid.seq for bid in result.recent_bids] == list(range(25, 5, -1))


async def test_list_overlays_live_state_and_puts_running_auctions_first(
    client: httpx.AsyncClient, engine: FakeEngine, redis: Redis
) -> None:
    later = await engine.create_auction(title="Later", end_at=in_hours(2))
    sooner = await engine.create_auction(title="Sooner", end_at=in_hours(1))
    paused = await engine.create_auction(title="Paused", end_at=in_hours(3), status="paused")
    old = await engine.create_auction(title="Old", end_at=in_hours(-2), status="closed")
    # Postgres still says open, but Redis has closed it.
    just_closed = await engine.create_auction(title="Just closed", end_at=in_hours(-1))
    await redis.hset(auction_key(just_closed), "status", "closed")
    await engine.bid(sooner, 1_500)
    # No Redis hash at all (not loaded since a restart): Postgres values are used.
    unloaded = await engine.create_auction(title="Unloaded", end_at=in_hours(4), load_redis=False)

    response = await client.get("/api/auctions")

    assert response.status_code == 200
    items = [AuctionSummary.model_validate(item) for item in response.json()]
    assert [(item.id, item.status) for item in items] == [
        (sooner, "open"),
        (later, "open"),
        (paused, "paused"),
        (unloaded, "open"),
        (just_closed, "closed"),
        (old, "closed"),
    ]
    assert items[0].high_amount == 1_500
    assert items[1].high_amount is None


async def test_list_is_empty_without_auctions(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/auctions")

    assert (response.status_code, response.json()) == (200, [])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("0", False),
        ("true", True),
        ("false", False),
        ("", None),
    ],
)
def test_high_is_bot_encodings(raw: str, expected: bool | None) -> None:
    state = parse_live_state(
        {
            "status": "open",
            "end_at_ms": "1767225600123",
            "start_price": "1000",
            "seq": "4",
            "high_is_bot": raw,
        }
    )

    assert state is not None
    assert state.high_is_bot is expected
    assert state.end_at == datetime.fromisoformat("2026-01-01T00:00:00.123+00:00")


def test_missing_or_broken_hash_parses_as_none() -> None:
    assert parse_live_state({}) is None
    assert parse_live_state({"status": "open", "seq": "x"}) is None
