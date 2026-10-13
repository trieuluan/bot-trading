"""Isolated closed-candle forex observations, without orders or wallets."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "forex_observations",
        sa.Column("workspace_id", sa.String(30), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("candle_time", sa.Integer, primary_key=True),
        sa.Column("snapshot", sa.JSON, nullable=False),
    )

def downgrade():
    raise RuntimeError("Preserve forex observations; restore a reviewed backup instead")
