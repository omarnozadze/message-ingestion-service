from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, DB_SCHEMA, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.client import Client


class ClientAlias(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_aliases"
    __table_args__ = (
        UniqueConstraint("client_id", "normalized_alias", name="uq_client_alias_client_normalized"),
        Index("ix_client_aliases_normalized_alias", "normalized_alias"),
        {"schema": DB_SCHEMA},
    )

    client_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(256), nullable=False)

    client: Mapped[Client] = relationship(back_populates="aliases")
