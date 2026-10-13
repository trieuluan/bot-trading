"""Independent local Exness paper portfolios; never broker orders."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forex_paper_accounts",
        sa.Column("workspace_id", sa.String(30), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("channel", sa.String(10), primary_key=True),
        sa.Column("started_at", sa.Integer, nullable=False),
        sa.Column("last_candle", sa.Integer, nullable=False),
        sa.Column("balance", sa.Float, nullable=False),
        sa.Column("blocked", sa.Boolean, nullable=False),
    )
    op.create_table(
        "forex_paper_trades",
        sa.Column("workspace_id", sa.String(30), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("channel", sa.String(10), primary_key=True),
        sa.Column("confirm_time", sa.Integer, primary_key=True),
        sa.Column("snapshot", sa.JSON, nullable=False),
    )


def downgrade():
    raise RuntimeError("Preserve forex paper history; restore a reviewed backup instead")
