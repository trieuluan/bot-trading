"""Initial schema and stopped bot state."""
from alembic import op
import sqlalchemy as sa
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("bot_state", sa.Column("id", sa.Integer, primary_key=True), sa.Column("enabled", sa.Boolean, nullable=False), sa.Column("heartbeat", sa.DateTime(timezone=True)), sa.Column("last_bar", sa.Integer, nullable=False))
    op.create_table("events", sa.Column("id", sa.Integer, primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("kind", sa.String(30), nullable=False), sa.Column("message", sa.String(500), nullable=False))
    op.create_table("signals", sa.Column("id", sa.Integer, primary_key=True), sa.Column("candle_time", sa.Integer, nullable=False, unique=True), sa.Column("symbol", sa.String(30), nullable=False), sa.Column("side", sa.String(10), nullable=False), sa.Column("entry", sa.Float, nullable=False), sa.Column("stop", sa.Float, nullable=False), sa.Column("target", sa.Float, nullable=False), sa.Column("reason", sa.String(500), nullable=False))
    op.execute(sa.text("INSERT INTO bot_state (id, enabled, last_bar) VALUES (1, false, 60)"))

def downgrade():
    op.drop_table("signals")
    op.drop_table("events")
    op.drop_table("bot_state")
