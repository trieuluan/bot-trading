"""Persist demo execution intents and broker fills separately from paper trades."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("exness_execution_state",
        sa.Column("account", sa.String(32), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("started_at", sa.Integer, nullable=False),
        sa.Column("snapshot", sa.JSON, nullable=False))
    op.create_table("exness_execution_trades",
        sa.Column("account", sa.String(32), primary_key=True),
        sa.Column("signal_time", sa.Integer, primary_key=True),
        sa.Column("snapshot", sa.JSON, nullable=False))
    op.create_table("exness_deals",
        sa.Column("account", sa.String(32), primary_key=True),
        sa.Column("deal_id", sa.String(30), primary_key=True),
        sa.Column("position_id", sa.String(30), nullable=True),
        sa.Column("event_time", sa.Integer, nullable=False),
        sa.Column("snapshot", sa.JSON, nullable=False))
    op.create_index("ix_exness_deals_position_id", "exness_deals", ["position_id"])
    op.create_index("ix_exness_deals_event_time", "exness_deals", ["event_time"])

def downgrade():
    raise RuntimeError("Preserve execution journal; restore a reviewed backup instead")
