"""Persistent simulated wallet, trades and idempotent command/funding ledger."""

import time

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "paper_wallet",
        sa.Column("id", sa.Integer, primary_key=True),
        *(
            sa.Column(name, sa.Float, nullable=False)
            for name in ("initial_balance", "balance", "fee_bps", "slippage_bps")
        ),
        sa.Column("quote", sa.JSON),
        sa.Column("contract", sa.JSON),
        sa.Column("error", sa.String(500)),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("funding_cursor", sa.Integer, nullable=False),
        sa.Column("funding_synced_at", sa.Integer, nullable=False),
        sa.Column("funding_error", sa.String(500)),
    )
    op.bulk_insert(
        sa.table(
            "paper_wallet",
            sa.column("id"),
            sa.column("initial_balance"),
            sa.column("balance"),
            sa.column("fee_bps"),
            sa.column("slippage_bps"),
            sa.column("funding_cursor"),
            sa.column("funding_synced_at"),
        ),
        [
            dict(
                id=1,
                initial_balance=10000,
                balance=10000,
                fee_bps=5,
                slippage_bps=2,
                funding_cursor=int(time.time()) // 900 * 900,
                funding_synced_at=0,
            )
        ],
    )
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("entry_time", sa.Integer, nullable=False),
        *(
            sa.Column(name, sa.Float, nullable=False)
            for name in (
                "entry",
                "quantity",
                "contracts",
                "stop",
                "target",
                "entry_fee",
                "exit_fee",
                "funding_cost",
            )
        ),
        sa.Column("exit_time", sa.Integer),
        sa.Column("exit", sa.Float),
        sa.Column("exit_reason", sa.String(30)),
        sa.Column("net_pnl", sa.Float),
    )
    op.create_table(
        "paper_commands",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("result", sa.JSON, nullable=False),
    )
    op.create_table(
        "paper_funding",
        sa.Column("trade_id", sa.Integer, primary_key=True),
        sa.Column("time", sa.Integer, primary_key=True),
        *(sa.Column(name, sa.Float, nullable=False) for name in ("rate", "price", "cost")),
    )


def downgrade():
    raise RuntimeError("Preserve the paper ledger; restore a reviewed DB backup instead")
