"""Durable paper-only entry intents; no exchange orders."""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "paper_pending_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("signal_time", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("next_bar", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("entry_policy", sa.String(30), nullable=False),
        sa.Column("level", sa.Float(), nullable=False),
        sa.Column("stop", sa.Float(), nullable=False),
        sa.Column("target", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("trade_id", sa.Integer()),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.UniqueConstraint("symbol", "signal_time", name="uq_paper_pending_signal"),
    )
    op.create_index("ix_paper_pending_orders_symbol", "paper_pending_orders", ["symbol"])


def downgrade():
    op.drop_table("paper_pending_orders")
