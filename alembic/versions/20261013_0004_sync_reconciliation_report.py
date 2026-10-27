"""store last 1C sync reconciliation report

Revision ID: 20261013_0004
Revises: 20261007_0003
"""

from alembic import op
import sqlalchemy as sa

SCHEMA = "message_ingest"

revision = "20261013_0004"
down_revision = "20261007_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sync_states",
        sa.Column("last_report_json", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("sync_states", "last_report_json", schema=SCHEMA)
