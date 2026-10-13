"""Persist paper stop reconciliation progress across restarts."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("paper_trades", sa.Column("monitoring", sa.JSON, nullable=True))

def downgrade():
    raise RuntimeError("Preserve paper recovery audit; restore a reviewed backup instead")
