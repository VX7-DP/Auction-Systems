"""Postgres side of the bid engine. Postgres is the source of truth (CLAUDE.md section 4).

The load_epoch fence: every load of an auction into Redis bumps auctions.load_epoch
while holding the auction row FOR UPDATE. A bid write first locks the same row and
checks the epoch its Redis decision was made under. So a write either finishes before
a reload reads Postgres (the reload waits for its lock), or it sees the new epoch and
is refused: a bid decided against an older load can never be saved after a reload.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def to_ms(moment: datetime) -> int:
    return (moment - UNIX_EPOCH) // timedelta(milliseconds=1)


def from_ms(ms: int) -> datetime:
    return UNIX_EPOCH + timedelta(milliseconds=ms)


class Fenced(Exception):
    """The bid was decided against an older load of the auction; it was not saved."""


class BidConflict(Exception):
    """A different row holds this bid_id. Redis and Postgres disagree."""


@dataclass(frozen=True)
class BidRecord:
    """An accepted bid, as decided in Redis and remembered under its bid_id."""

    bid_id: uuid.UUID
    seq: int
    amount: int
    bidder_id: uuid.UUID
    bidder_name: str
    is_bot: bool

    def encode(self) -> str:
        """The bid memory's record. The name goes last, as it may contain "|"."""
        flag = "1" if self.is_bot else "0"
        return f"{self.seq}|{self.amount}|{self.bidder_id}|{flag}|{self.bidder_name}"

    @classmethod
    def decode(cls, bid_id: uuid.UUID, record: str) -> "BidRecord":
        seq, amount, bidder_id, is_bot, bidder_name = record.split("|", 4)
        return cls(bid_id, int(seq), int(amount), uuid.UUID(bidder_id), bidder_name, is_bot == "1")


@dataclass(frozen=True)
class StoredAuction:
    """What a load into Redis needs, read from Postgres."""

    id: uuid.UUID
    title: str
    start_price: int
    end_at: datetime
    status: str
    high_amount: int | None
    high_bidder_id: uuid.UUID | None
    high_bidder_name: str | None
    high_seq: int
    load_epoch: int
    bids: tuple[BidRecord, ...]

    @property
    def high_is_bot(self) -> bool | None:
        if self.high_amount is None:
            return None
        return self.bids[-1].is_bot if self.bids else False


async def _statement_timeout(connection: AsyncConnection, timeout_ms: int) -> None:
    await connection.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": str(timeout_ms)},
    )


async def save_bid(
    engine: AsyncEngine, auction_id: uuid.UUID, epoch: int, bid: BidRecord, timeout_ms: int
) -> None:
    """Saves an accepted bid in one transaction. Raises Fenced if the auction has been
    reloaded since the decision; any other error leaves nothing saved."""
    async with engine.begin() as connection:
        await _statement_timeout(connection, timeout_ms)
        # The fence. FOR NO KEY UPDATE, not a weaker lock: this transaction updates the
        # row below anyway, and taking the strongest lock first avoids lock upgrades.
        current = (
            await connection.execute(
                text("SELECT load_epoch FROM auctions WHERE id = :id FOR NO KEY UPDATE"),
                {"id": auction_id},
            )
        ).scalar_one_or_none()
        if current != epoch:
            raise Fenced
        inserted = (
            await connection.execute(
                text(
                    "INSERT INTO bids"
                    " (bid_id, auction_id, seq, bidder_id, bidder_name, amount, is_bot)"
                    " VALUES (:bid_id, :auction_id, :seq, :bidder_id, :bidder_name, :amount,"
                    " :is_bot)"
                    # Only a repeat of this very bid is harmless. Any other conflict,
                    # such as another bid at this seq, must fail rather than vanish.
                    " ON CONFLICT (bid_id) DO NOTHING RETURNING bid_id"
                ),
                {
                    "bid_id": bid.bid_id,
                    "auction_id": auction_id,
                    "seq": bid.seq,
                    "bidder_id": bid.bidder_id,
                    "bidder_name": bid.bidder_name,
                    "amount": bid.amount,
                    "is_bot": bid.is_bot,
                },
            )
        ).first()
        if inserted is None:
            stored = (
                await connection.execute(
                    text("SELECT auction_id, seq FROM bids WHERE bid_id = :bid_id"),
                    {"bid_id": bid.bid_id},
                )
            ).one()
            if (stored.auction_id, stored.seq) != (auction_id, bid.seq):
                raise BidConflict(bid.bid_id)
        # Arrival order doesn't matter: only a newer seq moves the high bid.
        await connection.execute(
            text(
                "UPDATE auctions SET high_amount = :amount, high_bidder_id = :bidder_id,"
                " high_bidder_name = :bidder_name, high_seq = :seq"
                " WHERE id = :id AND high_seq < :seq"
            ),
            {
                "id": auction_id,
                "amount": bid.amount,
                "bidder_id": bid.bidder_id,
                "bidder_name": bid.bidder_name,
                "seq": bid.seq,
            },
        )


async def read_for_load(
    engine: AsyncEngine, auction_id: uuid.UUID, timeout_ms: int
) -> StoredAuction | None:
    """Bumps load_epoch and reads the auction with all its bids, or returns None if
    there is no such auction. Waits for bid writes that already passed the fence."""
    async with engine.begin() as connection:
        await _statement_timeout(connection, timeout_ms)
        row = (
            await connection.execute(
                text(
                    "SELECT id, title, start_price, end_at, status, high_amount, high_bidder_id,"
                    " high_bidder_name, high_seq FROM auctions WHERE id = :id FOR UPDATE"
                ),
                {"id": auction_id},
            )
        ).one_or_none()
        if row is None:
            return None
        epoch = (
            await connection.execute(
                text(
                    "UPDATE auctions SET load_epoch = load_epoch + 1 WHERE id = :id"
                    " RETURNING load_epoch"
                ),
                {"id": auction_id},
            )
        ).scalar_one()
        bids = await connection.execute(
            text(
                "SELECT bid_id, seq, amount, bidder_id, bidder_name, is_bot FROM bids"
                " WHERE auction_id = :id ORDER BY seq"
            ),
            {"id": auction_id},
        )
        return StoredAuction(
            **row._asdict(),
            load_epoch=epoch,
            bids=tuple(
                BidRecord(b.bid_id, b.seq, b.amount, b.bidder_id, b.bidder_name, b.is_bot)
                for b in bids
            ),
        )


async def insert_auction(
    engine: AsyncEngine, title: str, start_price: int, end_at: datetime
) -> StoredAuction:
    """Creates an auction at load_epoch 1: the load into Redis that follows is its first."""
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    "INSERT INTO auctions (title, start_price, end_at, load_epoch)"
                    " VALUES (:title, :start_price, :end_at, 1)"
                    " RETURNING id, title, start_price, end_at, status, high_amount,"
                    " high_bidder_id, high_bidder_name, high_seq, load_epoch"
                ),
                {"title": title, "start_price": start_price, "end_at": end_at},
            )
        ).one()
    return StoredAuction(**row._asdict(), bids=())


async def record_seq(engine: AsyncEngine, auction_id: uuid.UUID, epoch: int, seq: int) -> bool:
    """Records a state change that is not a bid (an unpause). False if fenced off."""
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                "UPDATE auctions SET high_seq = GREATEST(high_seq, :seq)"
                " WHERE id = :id AND load_epoch = :epoch"
            ),
            {"id": auction_id, "epoch": epoch, "seq": seq},
        )
        return result.rowcount == 1


@dataclass(frozen=True)
class Winner:
    amount: int | None
    bidder_id: uuid.UUID | None
    bidder_name: str | None


async def record_close(
    engine: AsyncEngine, auction_id: uuid.UUID, epoch: int, seq: int
) -> Winner | None:
    """Marks the auction closed. Returns the winner only to the call that closed it, so
    the close is announced once; None if it was closed already or fenced off."""
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    "UPDATE auctions SET status = 'closed', high_seq = GREATEST(high_seq, :seq)"
                    " WHERE id = :id AND status <> 'closed' AND load_epoch = :epoch"
                    " RETURNING high_amount, high_bidder_id, high_bidder_name"
                ),
                {"id": auction_id, "epoch": epoch, "seq": seq},
            )
        ).one_or_none()
    return None if row is None else Winner(*row)


async def auction_status(engine: AsyncEngine, auction_id: uuid.UUID) -> str | None:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text("SELECT status FROM auctions WHERE id = :id"), {"id": auction_id}
            )
        ).scalar_one_or_none()


async def due_auction_ids(engine: AsyncEngine) -> list[uuid.UUID]:
    """Auctions that may need closing. The close script decides, by Redis TIME."""
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT id FROM auctions WHERE status <> 'closed' AND end_at <= now()")
        )
        return list(rows.scalars())
