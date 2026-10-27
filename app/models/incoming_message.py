from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum as SAEnum, Float, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import MatchMethod, MessageSource, MessageStatus
from app.db.base import Base, DB_SCHEMA, TimestampMixin, UUIDPrimaryKeyMixin


class IncomingMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "incoming_messages"
    __table_args__ = (
        UniqueConstraint(
            "source", "external_message_id", name="uq_incoming_message_source_external_id"
        ),
        {"schema": DB_SCHEMA},
    )

    source: Mapped[MessageSource] = mapped_column(
        SAEnum(MessageSource, native_enum=False, length=32), nullable=False
    )
    external_message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sender_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    sender_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    agent_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    agent_description: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[MessageStatus] = mapped_column(
        SAEnum(MessageStatus, native_enum=False, length=32),
        nullable=False,
        default=MessageStatus.RECEIVED,
    )

    matched_client_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.clients.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    match_method: Mapped[MatchMethod | None] = mapped_column(
        SAEnum(MatchMethod, native_enum=False, length=32), nullable=True
    )
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_evidence: Mapped[str | None] = mapped_column(String(512), nullable=True)

    one_c_record_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    one_c_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
