"""add sender and resolved-agent context to incoming messages

Revision ID: 20261005_0002
Revises: 20261001_0001
Create Date: 2026-10-05
"""

from alembic import op
import sqlalchemy as sa

revision = "20261005_0002"
down_revision = "20261001_0001"
branch_labels = None
depends_on = None

SCHEMA = "message_ingest"


def upgrade() -> None:
    op.add_column(
        "incoming_messages",
        sa.Column("sender_id", sa.String(length=256), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "incoming_messages",
        sa.Column("sender_name", sa.String(length=256), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "incoming_messages",
        sa.Column("agent_code", sa.String(length=16), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "incoming_messages",
        sa.Column("agent_description", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_incoming_messages_sender_id",
        "incoming_messages",
        ["sender_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_incoming_messages_agent_code",
        "incoming_messages",
        ["agent_code"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_incoming_messages_agent_code", table_name="incoming_messages", schema=SCHEMA)
    op.drop_index("ix_incoming_messages_sender_id", table_name="incoming_messages", schema=SCHEMA)
    op.drop_column("incoming_messages", "agent_description", schema=SCHEMA)
    op.drop_column("incoming_messages", "agent_code", schema=SCHEMA)
    op.drop_column("incoming_messages", "sender_name", schema=SCHEMA)
    op.drop_column("incoming_messages", "sender_id", schema=SCHEMA)
