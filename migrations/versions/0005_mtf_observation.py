"""Prospective MTF observations, isolated from signals and orders."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mtf_observation_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("started_at", sa.Integer),
        sa.Column("last_candle", sa.Integer),
        sa.Column("snapshot", sa.JSON),
        sa.Column("error", sa.String(500)),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(sa.table("mtf_observation_state", sa.column("id", sa.Integer)), [{"id": 1}])
    op.create_table(
        "mtf_observation_events",
        sa.Column("candle_time", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", sa.JSON, nullable=False),
    )


def downgrade():
    raise RuntimeError("Preserve prospective observations; restore a reviewed backup instead")
