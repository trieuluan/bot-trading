"""Forward-recorded MEXC open interest (holdVol)."""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "futures_open_interest",
        sa.Column("symbol", sa.String(30), primary_key=True),
        sa.Column("time", sa.Integer(), primary_key=True),
        sa.Column("hold_vol", sa.Float(), nullable=False),
    )

def downgrade():
    op.drop_table("futures_open_interest")
