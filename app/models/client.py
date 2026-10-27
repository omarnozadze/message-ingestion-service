from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, DB_SCHEMA, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.client_alias import ClientAlias


class Client(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clients"
    __table_args__ = (
        Index("ix_clients_normalized_name", "normalized_name"),
        {"schema": DB_SCHEMA},
    )

    one_c_ref: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    code: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    aliases: Mapped[list[ClientAlias]] = relationship(
        back_populates="client",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
