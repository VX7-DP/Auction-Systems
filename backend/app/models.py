"""SQLAlchemy models for the Postgres tables in CLAUDE.md section 10.

Postgres is the source of truth: Redis is rebuilt from these tables. Any change
here needs an Alembic migration (``uv run alembic revision --autogenerate``), and
the tests fail if the models and the migrations drift apart.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names, so migrations can refer to them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Auction(Base):
    __tablename__ = "auctions"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'paused', 'closed')", name="status"),
        CheckConstraint("start_price >= 0", name="start_price_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    title: Mapped[str] = mapped_column(Text)
    start_price: Mapped[int] = mapped_column(BigInteger)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    # The high_* fields stay NULL until the first accepted bid.
    high_amount: Mapped[int | None] = mapped_column(BigInteger)
    high_bidder_id: Mapped[uuid.UUID | None]
    high_bidder_name: Mapped[str | None] = mapped_column(Text)
    # seq of the latest state change applied here; writes use WHERE high_seq < :seq.
    high_seq: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    # Bumped each time the auction is loaded into Redis. A bid write commits only if
    # the epoch it was decided under is still current (CLAUDE.md section 4).
    load_epoch: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class Bid(Base):
    __tablename__ = "bids"
    __table_args__ = (
        UniqueConstraint("auction_id", "seq"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    bid_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    auction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auctions.id"))
    seq: Mapped[int] = mapped_column(BigInteger)
    bidder_id: Mapped[uuid.UUID]
    bidder_name: Mapped[str] = mapped_column(Text)
    amount: Mapped[int] = mapped_column(BigInteger)
    is_bot: Mapped[bool] = mapped_column(server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
