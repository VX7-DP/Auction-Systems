"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-04 13:06:49.189333
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auctions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("start_price", sa.BigInteger(), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("high_amount", sa.BigInteger(), nullable=True),
        sa.Column("high_bidder_id", sa.Uuid(), nullable=True),
        sa.Column("high_bidder_name", sa.Text(), nullable=True),
        sa.Column("high_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'paused', 'closed')", name=op.f("ck_auctions_status")
        ),
        sa.CheckConstraint("start_price >= 0", name=op.f("ck_auctions_start_price_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auctions")),
    )
    op.create_table(
        "bids",
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("auction_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("bidder_id", sa.Uuid(), nullable=False),
        sa.Column("bidder_name", sa.Text(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("is_bot", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_bids_amount_positive")),
        sa.ForeignKeyConstraint(
            ["auction_id"], ["auctions.id"], name=op.f("fk_bids_auction_id_auctions")
        ),
        sa.PrimaryKeyConstraint("bid_id", name=op.f("pk_bids")),
        sa.UniqueConstraint("auction_id", "seq", name=op.f("uq_bids_auction_id_seq")),
    )


def downgrade() -> None:
    op.drop_table("bids")
    op.drop_table("auctions")
