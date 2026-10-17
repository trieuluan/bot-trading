"""Track M1 history independently of the existing MTF feed."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    state = sa.table("workspace_feed_state", sa.column("workspace_id", sa.String),
                     sa.column("timeframe", sa.String), sa.column("enabled", sa.Boolean))
    op.bulk_insert(state, [{"workspace_id": "exness_forex", "timeframe": "1m", "enabled": False}])


def downgrade():
    raise RuntimeError("Preserve M1 feed progress; restore a reviewed backup instead")
