"""Merge per-pair paper balances into the shared wallet (row 1)."""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

def upgrade():
    wallet = sa.table("paper_wallet", sa.column("id", sa.Integer),
                      sa.column("initial_balance", sa.Float), sa.column("balance", sa.Float))
    connection = op.get_bind()
    rows = connection.execute(sa.select(wallet.c.id, wallet.c.initial_balance, wallet.c.balance)).all()
    # Keep realised PnL of every pair: shared = row-1 initial + sum(balance - initial).
    pnl = sum(balance - initial for _, initial, balance in rows)
    first = next(initial for pair, initial, _ in rows if pair == 1)
    connection.execute(wallet.update().where(wallet.c.id == 1).values(balance=round(first + pnl, 8)))
    connection.execute(wallet.update().where(wallet.c.id != 1).values(balance=0, initial_balance=0))

def downgrade():
    raise RuntimeError("Shared wallet merge is not reversible; restore a reviewed backup instead")
