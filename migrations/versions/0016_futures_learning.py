"""Isolated futures shadow learner; execution opt-in defaults off."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

def upgrade():
    table = op.create_table(
        "futures_learning_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.bulk_insert(table, [{"id": 1, "payload": {}}])

def downgrade():
    op.drop_table("futures_learning_state")
