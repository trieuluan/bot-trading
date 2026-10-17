"""Add leverage column to auto_paper_state and paper_trades."""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("auto_paper_state") as batch:
        batch.add_column(sa.Column("leverage", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("paper_trades") as batch:
        batch.add_column(sa.Column("leverage", sa.Integer(), nullable=False, server_default="1"))

def downgrade():
    with op.batch_alter_table("paper_trades") as batch:
        batch.drop_column("leverage")
    with op.batch_alter_table("auto_paper_state") as batch:
        batch.drop_column("leverage")
