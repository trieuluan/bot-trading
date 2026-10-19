"""Transactional Telegram outbox for paper trade events."""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "paper_notifications",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("message", sa.String(4000), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.Integer(), nullable=True),
    )
    op.create_index("ix_paper_notifications_delivery", "paper_notifications", ["sent_at", "retry_at"])

def downgrade():
    op.drop_table("paper_notifications")
