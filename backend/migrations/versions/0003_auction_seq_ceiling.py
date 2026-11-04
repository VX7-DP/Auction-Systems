"""auction seq ceiling

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-04 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auctions",
        sa.Column("seq_ceiling", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    # Existing auctions start from their recorded seq.
    op.execute("UPDATE auctions SET seq_ceiling = high_seq")


def downgrade() -> None:
    op.drop_column("auctions", "seq_ceiling")
