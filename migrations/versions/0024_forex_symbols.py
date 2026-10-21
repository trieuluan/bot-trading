"""Partition Forex state by instrument without resetting XAUUSD history."""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    keys = {
        "workspace_feed_state": ["workspace_id", "symbol", "timeframe"],
        "forex_observations": ["workspace_id", "symbol", "candle_time"],
        "forex_gap_audits": ["workspace_id", "symbol", "after", "before"],
        "forex_instrument_state": ["workspace_id", "symbol"],
        "exness_execution_trades": ["account", "symbol", "signal_time"],
    }
    connection = op.get_bind()
    for table, columns in keys.items():
        primary = sa.inspect(connection).get_pk_constraint(table)
        with op.batch_alter_table(table, naming_convention={"pk": "pk_%(table_name)s"}) as batch:
            batch.add_column(sa.Column("symbol", sa.String(30), nullable=False, server_default="XAUUSD"))
            batch.drop_constraint(primary["name"] or f"pk_{table}", type_="primary")
            batch.create_primary_key(f"pk_{table}", columns)


def downgrade():
    raise RuntimeError("Preserve multi-symbol Forex history; downgrade requires reviewed export")
