"""Fixed ten-pair Futures paper universe, preserving the existing BTC ledger."""

import time

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("paper_trades", sa.Column("symbol", sa.String(30), nullable=False, server_default="BTC_USDT"))
    op.create_index("ix_paper_trades_symbol", "paper_trades", ["symbol"])
    with op.batch_alter_table("auto_paper_decisions") as batch:
        batch.add_column(sa.Column("symbol", sa.String(30), nullable=False, server_default="BTC_USDT"))
    connection = op.get_bind()
    primary = sa.inspect(connection).get_pk_constraint("auto_paper_decisions")
    with op.batch_alter_table("auto_paper_decisions", naming_convention={"pk": "pk_%(table_name)s"}) as batch:
        batch.drop_constraint(primary["name"] or "pk_auto_paper_decisions", type_="primary")
        batch.create_primary_key("pk_auto_paper_decisions", ["symbol", "candle_time"])
    now = int(time.time()) // 900 * 900
    tables = {name: sa.Table(name, sa.MetaData(), autoload_with=connection) for name in (
        "paper_wallet", "market_state", "auto_paper_state", "futures_learning_state",
    )}
    for pair_id in range(2, 11):
        connection.execute(tables["paper_wallet"].insert().values(
            id=pair_id, initial_balance=10000, balance=10000, fee_bps=5, slippage_bps=2,
            funding_cursor=now, funding_synced_at=0,
        ))
        connection.execute(tables["market_state"].insert().values(id=pair_id, source="mexc_futures"))
        connection.execute(tables["auto_paper_state"].insert().values(
            id=pair_id, enabled=False, risk_percent=1, max_daily_trades=100,
            daily_loss_percent=3, day="", day_start_equity=10000, status="Auto Paper đang tắt",
        ))
        connection.execute(tables["futures_learning_state"].insert().values(id=pair_id, payload={}))

def downgrade():
    raise RuntimeError("Preserve multi-symbol paper ledgers; restore a reviewed backup instead")
