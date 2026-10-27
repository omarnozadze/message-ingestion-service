"""add persistent 1C sync state

Revision ID: 20261007_0003
Revises: 20261005_0002
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa

revision = "20261007_0003"
down_revision = "20261005_0002"
branch_labels = None
depends_on = None

SCHEMA = "message_ingest"


def upgrade() -> None:
    op.create_table(
        "sync_states",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("cursor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_incremental_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_full_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name", name="pk_sync_states"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("sync_states", schema=SCHEMA)
