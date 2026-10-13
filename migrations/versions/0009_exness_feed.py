"""Persist Exness backfill progress and independent worker health."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workspace_candles", sa.Column("tick_volume", sa.BigInteger, nullable=True))
    state = op.create_table(
        "workspace_feed_state",
        sa.Column("workspace_id", sa.String(30), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("timeframe", sa.String(10), primary_key=True),
        sa.Column("started_at", sa.Integer, nullable=True),
        sa.Column("checked_until", sa.Integer, nullable=True),
        sa.Column("last_sync", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("error", sa.String(200), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.bulk_insert(state, [{"workspace_id": "exness_forex", "timeframe": frame, "enabled": False}
                           for frame in ("15m", "1h", "4h")])


def downgrade():
    raise RuntimeError("Preserve Exness feed progress; restore a reviewed backup instead")
