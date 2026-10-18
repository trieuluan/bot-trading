"""Update default PaperWallet fee_bps to 8 (MEXC API taker rate)."""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("UPDATE paper_wallet SET fee_bps = 8 WHERE fee_bps = 5")

def downgrade():
    op.execute("UPDATE paper_wallet SET fee_bps = 5 WHERE fee_bps = 8")
