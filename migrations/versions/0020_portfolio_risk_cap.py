"""Add max_portfolio_risk_percent column to auto_paper_state."""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("auto_paper_state") as batch:
        batch.add_column(sa.Column("max_portfolio_risk_percent", sa.Float(), nullable=True))

def downgrade():
    with op.batch_alter_table("auto_paper_state") as batch:
        batch.drop_column("max_portfolio_risk_percent")
