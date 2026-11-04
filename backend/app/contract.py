"""The interface contract from CLAUDE.md section 10, as Pydantic models and key names.

Every session builds against these shapes, so treat this file as fixed: change
section 10 first, then this file, in the same PR.

Conventions:
- Ids are UUIDs, sent as strings in JSON.
- Amounts are integers in minor units (paise or cents), at most MAX_AMOUNT.
- Times are timezone-aware and serialised as ISO 8601 UTC.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, Field, StringConstraints, TypeAdapter

# 2**53 - 1 is the largest integer that is exact both as a JavaScript number and as
# a double inside Redis Lua, so every amount stays exact end to end.
MAX_AMOUNT = 2**53 - 1


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
Amount = Annotated[int, Field(ge=0, le=MAX_AMOUNT)]
PositiveAmount = Annotated[int, Field(gt=0, le=MAX_AMOUNT)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

AuctionStatus = Literal["open", "paused", "closed"]
BidStatus = Literal["accepted", "rejected", "error"]
RejectReason = Literal["too_low", "closed", "paused", "not_found"]


# --- REST: session --------------------------------------------------------------


class SessionCreate(BaseModel):
    """Body of POST /api/session."""

    display_name: DisplayName


class Bidder(BaseModel):
    """The signed-in guest: response of POST and GET /api/session."""

    bidder_id: UUID
    display_name: str


# --- REST: auctions ---------------------------------------------------------------


class AuctionSummary(BaseModel):
    """One item of GET /api/auctions."""

    id: UUID
    title: str
    status: AuctionStatus
    high_amount: int | None  # None until the first accepted bid
    end_at: UtcDatetime


class RecentBid(BaseModel):
    seq: int
    amount: int
    bidder_name: str
    is_bot: bool
    created_at: UtcDatetime


class AuctionSnapshot(BaseModel):
    """GET /api/auctions/{id}. Clients refetch it after a seq gap or a reconnect."""

    id: UUID
    title: str
    start_price: int
    end_at: UtcDatetime
    status: AuctionStatus
    seq: int
    # The high_* fields are None until the first accepted bid.
    high_amount: int | None
    high_bidder_id: UUID | None
    high_bidder_name: str | None
    high_is_bot: bool | None
    recent_bids: list[RecentBid]


# --- REST: bids -------------------------------------------------------------------


class BidCreate(BaseModel):
    """Body of POST /api/auctions/{id}/bids. The client makes bid_id and reuses it on retries."""

    bid_id: UUID
    amount: PositiveAmount


class BidResult(BaseModel):
    """Response of POST /api/auctions/{id}/bids, and what place_bid returns."""

    bid_id: UUID
    status: BidStatus
    reason: RejectReason | None = None  # set when status is "rejected"
    seq: int | None = None  # set when status is "accepted"


# --- REST: admin ------------------------------------------------------------------


class AuctionCreate(BaseModel):
    """Body of POST /api/admin/auctions (sent with an X-Admin-Password header)."""

    title: Title
    start_price: Amount
    end_at: UtcDatetime


# --- REST: health -----------------------------------------------------------------


class Health(BaseModel):
    """GET /api/health. Served with 200 when both are up, 503 otherwise."""

    status: Literal["ok", "degraded"]
    postgres: Literal["up", "down"]
    redis: Literal["up", "down"]


# --- WebSocket /ws/auctions/{id} (server to client only) ---------------------------


class AuctionUpdate(BaseModel):
    """Sent on every state change. Clients show "you've been outbid" when the high
    bidder changes from the current user to someone else."""

    type: Literal["auction_update"] = "auction_update"
    auction_id: UUID
    seq: int
    high_amount: int | None
    high_bidder_id: UUID | None
    high_bidder_name: str | None
    is_bot: bool | None  # None while there is no high bidder
    end_at: UtcDatetime
    status: AuctionStatus


class AuctionClosed(BaseModel):
    """Sent once when the auction closes. The winner fields are None if nobody bid."""

    type: Literal["auction_closed"] = "auction_closed"
    auction_id: UUID
    seq: int
    winner_id: UUID | None
    winner_name: str | None
    amount: int | None


AuctionEvent = Annotated[AuctionUpdate | AuctionClosed, Field(discriminator="type")]
# Parses a JSON event read from Redis Pub/Sub: auction_event_adapter.validate_json(raw)
auction_event_adapter: TypeAdapter[AuctionUpdate | AuctionClosed] = TypeAdapter(AuctionEvent)


# --- Redis keys ---------------------------------------------------------------------


def auction_key(auction_id: UUID) -> str:
    """Hash with status, end_at_ms, start_price, high_amount, high_bidder_id,
    high_bidder_name, high_is_bot and seq."""
    return f"auction:{auction_id}"


def auction_events_channel(auction_id: UUID) -> str:
    """Pub/Sub channel carrying the WebSocket messages above, as JSON."""
    return f"auction:{auction_id}:events"


def job_lock_key(name: str) -> str:
    """Lock held by the one instance running background job ``name``."""
    return f"lock:job:{name}"
