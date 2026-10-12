"""Separate market data, signal identities and reproducible research reports."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    constraints = sa.inspect(op.get_bind()).get_unique_constraints("signals")
    original = next(c for c in constraints if c["column_names"] == ["candle_time"])
    convention = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table("signals", naming_convention=convention) as batch:
        batch.add_column(
            sa.Column("source", sa.String(30), nullable=False, server_default="synthetic")
        )
        batch.add_column(
            sa.Column("timeframe", sa.String(10), nullable=False, server_default="15m")
        )
        batch.drop_constraint(original["name"] or "uq_signals_candle_time", type_="unique")
        batch.create_unique_constraint(
            "uq_signal_market_time", ["source", "symbol", "timeframe", "candle_time"]
        )
    op.create_table(
        "market_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("last_processed", sa.Integer),
        sa.Column("last_sync", sa.DateTime(timezone=True)),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(500)),
    )
    op.execute(sa.text("INSERT INTO market_state (id, source) VALUES (1, 'synthetic')"))
    op.create_table(
        "market_candles",
        sa.Column("source", sa.String(30), primary_key=True),
        sa.Column("symbol", sa.String(30), primary_key=True),
        sa.Column("timeframe", sa.String(10), primary_key=True),
        sa.Column("time", sa.Integer, primary_key=True),
        *(
            sa.Column(name, sa.Float, nullable=False)
            for name in ("open", "high", "low", "close", "volume")
        ),
    )
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("result", sa.JSON, nullable=False),
    )


def downgrade():
    # Restoring the old timestamp-only constraint would lose market-specific signals.
    raise RuntimeError("0002 downgrade requires a reviewed data conversion; restore a DB backup")
