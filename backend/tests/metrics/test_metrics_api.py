"""GET /api/metrics, fed by every call to place_bid (session 6, CLAUDE.md section 15)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI

from app.bidding.service import place_bid
from app.contract import MetricsSnapshot


async def _metrics(client: httpx.AsyncClient) -> MetricsSnapshot:
    response = await client.get("/api/metrics")
    assert response.status_code == 200, response.text
    return MetricsSnapshot.model_validate(response.json())


async def _create_auction(
    client: httpx.AsyncClient, admin_headers: dict[str, str], start_price: int = 100
) -> str:
    end_at = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0)
    response = await client.post(
        "/api/admin/auctions",
        headers=admin_headers,
        json={"title": "Metrics lamp", "start_price": start_price, "end_at": end_at.isoformat()},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def test_a_fresh_app_reports_no_bids(app: FastAPI, client: httpx.AsyncClient) -> None:
    snapshot = await _metrics(client)

    assert snapshot.bids.accepted == 0
    assert sum(snapshot.bids.rejected.values()) == 0
    assert snapshot.bids.error == 0
    assert snapshot.latency_ms.samples == 0
    assert snapshot.started_at <= datetime.now(UTC)


async def test_metrics_need_no_sign_in(app: FastAPI, client: httpx.AsyncClient) -> None:
    response = await client.get("/api/metrics")
    assert response.status_code == 200


async def test_bids_through_the_api_are_counted_and_timed(
    app: FastAPI,
    client: httpx.AsyncClient,
    bidder_client: Callable[[str], Awaitable[httpx.AsyncClient]],
    admin_headers: dict[str, str],
) -> None:
    auction_id = await _create_auction(client, admin_headers)
    asha = await bidder_client("Asha")

    async def bid(on: str, amount: int) -> str:
        response = await asha.post(
            f"/api/auctions/{on}/bids", json={"bid_id": str(uuid.uuid4()), "amount": amount}
        )
        body = response.json()
        return body.get("reason", body["status"])

    assert await bid(auction_id, 150) == "accepted"
    assert await bid(auction_id, 120) == "too_low"
    assert await bid(str(uuid.uuid4()), 150) == "not_found"

    snapshot = await _metrics(client)
    assert snapshot.bids.accepted == 1
    assert snapshot.bids.rejected["too_low"] == 1
    assert snapshot.bids.rejected["not_found"] == 1
    assert snapshot.bids.error == 0
    assert snapshot.latency_ms.samples == 3
    assert snapshot.latency_ms.p50 is not None and snapshot.latency_ms.p50 > 0
    assert snapshot.latency_ms.max is not None
    assert snapshot.latency_ms.max >= snapshot.latency_ms.p99  # type: ignore[operator]


async def test_simulated_bidders_are_counted_too(
    app: FastAPI, client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    auction_id = await _create_auction(client, admin_headers)

    # The bots call place_bid directly, not through the HTTP endpoint.
    result = await place_bid(
        uuid.UUID(auction_id), uuid.uuid4(), uuid.uuid4(), "Bot Ravi", 150, is_bot=True
    )
    assert result.status == "accepted"

    snapshot = await _metrics(client)
    assert snapshot.bids.accepted == 1
    assert snapshot.latency_ms.samples == 1
