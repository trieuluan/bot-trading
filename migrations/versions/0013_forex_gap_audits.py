"""Persist read-only XAUUSD gap rechecks without treating empty history as a holiday."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forex_gap_audits",
        sa.Column("workspace_id", sa.String(30), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("after", sa.Integer, primary_key=True),
        sa.Column("before", sa.Integer, primary_key=True),
        sa.Column("checked_at", sa.Integer, nullable=False),
        sa.Column("retry_at", sa.Integer, nullable=False),
        sa.Column("snapshot", sa.JSON, nullable=False),
    )


def downgrade():
    raise RuntimeError("Preserve gap audit; restore a reviewed backup instead")
