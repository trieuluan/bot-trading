"""Separate venue identity and bid/ask storage; preserve legacy MEXC state."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    workspaces = op.create_table(
        "workspaces",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("venue", sa.String(30), nullable=False, unique=True),
        sa.Column("symbol", sa.String(30), nullable=False),
    )
    op.bulk_insert(workspaces, [
        {"id": "mexc_futures", "name": "MEXC · Crypto Futures", "venue": "mexc_futures", "symbol": "BTC/USDT:USDT"},
        {"id": "exness_forex", "name": "Exness · Forex Demo", "venue": "exness_forex", "symbol": "XAUUSD"},
    ])
    op.create_table(
        "workspace_candles",
        sa.Column("workspace_id", sa.String(30), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("symbol", sa.String(30), primary_key=True),
        sa.Column("timeframe", sa.String(10), primary_key=True),
        sa.Column("price_type", sa.String(10), primary_key=True),
        sa.Column("time", sa.Integer, primary_key=True),
        *(sa.Column(name, sa.Float, nullable=False) for name in ("open", "high", "low", "close")),
        sa.CheckConstraint("price_type IN ('bid', 'ask')", name="ck_workspace_candle_price_type"),
        sa.CheckConstraint("timeframe IN ('1m', '15m', '1h', '4h')", name="ck_workspace_candle_timeframe"),
        sa.CheckConstraint("open > 0 AND low > 0 AND low <= open AND low <= close AND high >= open AND high >= close", name="ck_workspace_candle_prices"),
    )


def downgrade():
    raise RuntimeError("Preserve venue data; restore a reviewed backup instead")
