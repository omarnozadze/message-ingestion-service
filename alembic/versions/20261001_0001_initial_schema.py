"""initial message ingestion schema

Revision ID: 20261001_0001
Revises:
Create Date: 2026-10-01
"""

from alembic import op
import sqlalchemy as sa

revision = "20261001_0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "message_ingest"


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

    op.create_table(
        "clients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("one_c_ref", sa.String(length=128), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("last_name", sa.String(length=128), nullable=True),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("normalized_name", sa.String(length=256), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_clients"),
        sa.UniqueConstraint("code", name="uq_clients_code"),
        sa.UniqueConstraint("one_c_ref", name="uq_clients_one_c_ref"),
        schema=SCHEMA,
    )
    op.create_index("ix_clients_code", "clients", ["code"], unique=False, schema=SCHEMA)
    op.create_index(
        "ix_clients_normalized_name", "clients", ["normalized_name"], unique=False, schema=SCHEMA
    )

    op.create_table(
        "client_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("alias", sa.String(length=256), nullable=False),
        sa.Column("normalized_alias", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"], [f"{SCHEMA}.clients.id"], name="fk_client_aliases_client_id_clients", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_client_aliases"),
        sa.UniqueConstraint("client_id", "normalized_alias", name="uq_client_alias_client_normalized"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_client_aliases_client_id", "client_aliases", ["client_id"], unique=False, schema=SCHEMA
    )
    op.create_index(
        "ix_client_aliases_normalized_alias",
        "client_aliases",
        ["normalized_alias"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "incoming_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_message_id", sa.String(length=256), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("matched_client_id", sa.Uuid(), nullable=True),
        sa.Column("match_method", sa.String(length=32), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("match_evidence", sa.String(length=512), nullable=True),
        sa.Column("one_c_record_ref", sa.String(length=256), nullable=True),
        sa.Column("one_c_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["matched_client_id"], [f"{SCHEMA}.clients.id"], name="fk_incoming_messages_matched_client_id_clients", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incoming_messages"),
        sa.UniqueConstraint(
            "source", "external_message_id", name="uq_incoming_message_source_external_id"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_incoming_messages_matched_client_id",
        "incoming_messages",
        ["matched_client_id"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incoming_message_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["incoming_message_id"],
            [f"{SCHEMA}.incoming_messages.id"],
            name="fk_audit_logs_incoming_message_id_incoming_messages",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_audit_logs_event_type", "audit_logs", ["event_type"], unique=False, schema=SCHEMA
    )
    op.create_index(
        "ix_audit_logs_incoming_message_id",
        "audit_logs",
        ["incoming_message_id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("audit_logs", schema=SCHEMA)
    op.drop_table("incoming_messages", schema=SCHEMA)
    op.drop_table("client_aliases", schema=SCHEMA)
    op.drop_table("clients", schema=SCHEMA)
    op.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}"')
