"""Prospective MTF features and delayed research labels, not orders."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "mtf_research_samples",
        sa.Column("confirm_time", sa.Integer, primary_key=True),
        sa.Column("features", sa.JSON, nullable=False),
        sa.Column("evaluation", sa.JSON, nullable=False),
    )

def downgrade():
    raise RuntimeError("Preserve research samples; restore a reviewed backup instead")
