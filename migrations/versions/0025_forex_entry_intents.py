"""Durable virtual entries; broker positions and V3 history stay unchanged."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "exness_entry_intents",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("setup_id", sa.String(64), nullable=False),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("available_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account", "symbol", "setup_id", "side", name="uq_exness_entry_setup"),
    )
    for name in ("account", "symbol", "setup_id", "status"):
        op.create_index(f"ix_exness_entry_intents_{name}", "exness_entry_intents", [name])


def downgrade():
    raise RuntimeError("Preserve entry intent audit and reconcile submissions before downgrade")
