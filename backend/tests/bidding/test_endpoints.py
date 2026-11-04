"""POST /api/auctions/{id}/bids and POST /api/admin/auctions (CLAUDE.md section 10)."""

import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta

import httpx

from app.bidding.settings import get_bidding_settings
from app.contract import AuctionSnapshot
from tests.bidding.helpers import HeldInsert, Stores, to_ms

BidderClient = Callable[[str], Awaitable[httpx.AsyncClient]]
HoldBidInsert = Callable[[uuid.UUID, uuid.UUID], AbstractAsyncContextManager[HeldInsert]]


async def test_signed_in_guest_can_bid(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    bidder_client: BidderClient,
    stores: Stores,
) -> None:
    auction = await create_auction(start_price=100)
    asha = await bidder_client("Asha")
    me = (await asha.get("/api/session")).json()
    bid_id = uuid.uuid4()

    response = await asha.post(
        f"/api/auctions/{auction.id}/bids", json={"bid_id": str(bid_id), "amount": 150}
    )

    assert response.status_code == 200
    assert response.json() == {"bid_id": str(bid_id), "status": "accepted", "seq": 1}
    assert [
        (row["bid_id"], row["bidder_id"], row["bidder_name"], row["is_bot"])
        for row in await stores.bids(auction.id)
    ] == [(bid_id, uuid.UUID(me["bidder_id"]), "Asha", False)]


async def test_rejected_bids_answer_200_with_the_reason(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]], bidder_client: BidderClient
) -> None:
    auction = await create_auction(start_price=100)
    asha = await bidder_client("Asha")
    too_low, unknown = uuid.uuid4(), uuid.uuid4()

    low = await asha.post(
        f"/api/auctions/{auction.id}/bids", json={"bid_id": str(too_low), "amount": 100}
    )
    missing = await asha.post(
        f"/api/auctions/{uuid.uuid4()}/bids", json={"bid_id": str(unknown), "amount": 150}
    )

    assert (low.status_code, low.json()) == (
        200,
        {"bid_id": str(too_low), "status": "rejected", "reason": "too_low"},
    )
    assert (missing.status_code, missing.json()) == (
        200,
        {"bid_id": str(unknown), "status": "rejected", "reason": "not_found"},
    )


async def test_bidding_requires_sign_in(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"/api/auctions/{uuid.uuid4()}/bids", json={"bid_id": str(uuid.uuid4()), "amount": 150}
    )

    assert response.status_code == 401


async def test_failed_save_answers_503(
    create_auction: Callable[..., Awaitable[AuctionSnapshot]],
    bidder_client: BidderClient,
    hold_bid_insert: HoldBidInsert,
) -> None:
    auction = await create_auction(start_price=100)
    asha = await bidder_client("Asha")
    bid_id = uuid.uuid4()

    async with hold_bid_insert(auction.id, bid_id):
        response = await asha.post(
            f"/api/auctions/{auction.id}/bids", json={"bid_id": str(bid_id), "amount": 150}
        )

    assert (response.status_code, response.json()) == (
        503,
        {"bid_id": str(bid_id), "status": "error"},
    )


async def test_admin_creates_an_open_auction_loaded_into_redis(
    client: httpx.AsyncClient, admin_headers: dict[str, str], stores: Stores
) -> None:
    end_at = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)

    response = await client.post(
        "/api/admin/auctions",
        headers=admin_headers,
        json={"title": " Brass lamp ", "start_price": 500, "end_at": end_at.isoformat()},
    )

    assert response.status_code == 200, response.text
    snapshot = AuctionSnapshot.model_validate(response.json())
    assert snapshot == AuctionSnapshot(
        id=snapshot.id,
        title="Brass lamp",
        start_price=500,
        end_at=end_at,
        status="open",
        seq=0,
        high_amount=None,
        high_bidder_id=None,
        high_bidder_name=None,
        high_is_bot=None,
        recent_bids=[],
    )
    row = await stores.auction(snapshot.id)
    assert {
        column: row[column]
        for column in ("title", "start_price", "end_at", "status", "high_amount", "high_seq")
    } == {
        "title": "Brass lamp",
        "start_price": 500,
        "end_at": end_at,
        "status": "open",
        "high_amount": None,
        "high_seq": 0,
    }
    # The Redis hash of section 10. The high_* fields stay absent until the first bid.
    assert await stores.hash(snapshot.id) == {
        "status": "open",
        "start_price": "500",
        "end_at_ms": str(to_ms(end_at)),
        "seq": "0",
        "epoch": str(row["load_epoch"]),
        # The first block of seqs, reserved in Postgres before Redis can use it.
        "ceiling": str(get_bidding_settings().seq_block),
    }


async def test_admin_rejects_an_end_at_in_the_past(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    end_at = datetime.now(UTC) - timedelta(minutes=1)

    response = await client.post(
        "/api/admin/auctions",
        headers=admin_headers,
        json={"title": "Brass lamp", "start_price": 500, "end_at": end_at.isoformat()},
    )

    assert response.status_code == 422
