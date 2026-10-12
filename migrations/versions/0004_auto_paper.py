"""Opt-in auto-paper controls and one durable decision per closed MEXC bar."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "paper_trades", sa.Column("origin", sa.String(10), nullable=False, server_default="manual")
    )
    op.add_column("paper_trades", sa.Column("signal_time", sa.Integer))
    op.create_table(
        "auto_paper_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("risk_percent", sa.Float, nullable=False),
        sa.Column("max_daily_trades", sa.Integer, nullable=False),
        sa.Column("daily_loss_percent", sa.Float, nullable=False),
        sa.Column("last_candle", sa.Integer),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("day_start_equity", sa.Float, nullable=False),
        sa.Column("status", sa.String(500), nullable=False),
    )
    op.bulk_insert(
        sa.table(
            "auto_paper_state",
            *[
                sa.column(name)
                for name in (
                    "id",
                    "enabled",
                    "risk_percent",
                    "max_daily_trades",
                    "daily_loss_percent",
                    "day",
                    "day_start_equity",
                    "status",
                )
            ],
        ),
        [
            dict(
                id=1,
                enabled=False,
                risk_percent=1,
                max_daily_trades=5,
                daily_loss_percent=3,
                day="",
                day_start_equity=10000,
                status="Auto Paper đang tắt",
            )
        ],
    )
    op.create_table(
        "auto_paper_decisions",
        sa.Column("candle_time", sa.Integer, primary_key=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("setup", sa.JSON),
        sa.Column("trade_id", sa.Integer),
        sa.Column("parameters", sa.JSON, nullable=False),
    )


def downgrade():
    raise RuntimeError("Preserve auto-paper decisions; restore a reviewed backup instead")
