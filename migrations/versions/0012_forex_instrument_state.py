"""Persist read-only instrument specifications without credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "forex_instrument_state",
        sa.Column("workspace_id", sa.String(30), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("checked_at", sa.Integer, nullable=False),
        sa.Column("snapshot", sa.JSON, nullable=True),
        sa.Column("error", sa.String(200), nullable=True),
        sa.Column("retry_at", sa.Integer, nullable=False),
    )

def downgrade():
    raise RuntimeError("Preserve instrument audit; restore a reviewed backup instead")
