from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, DB_SCHEMA, TimestampMixin


class SyncState(TimestampMixin, Base):
    __tablename__ = "sync_states"
    __table_args__ = {"schema": DB_SCHEMA}

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    cursor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_incremental_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_full_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
