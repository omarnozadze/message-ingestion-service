from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MessageSource, MessageStatus
from app.models.incoming_message import IncomingMessage


class IncomingMessageRepository:
    async def find_by_external_id(
        self, session: AsyncSession, *, source: MessageSource, external_message_id: str
    ) -> IncomingMessage | None:
        stmt = select(IncomingMessage).where(
            IncomingMessage.source == source,
            IncomingMessage.external_message_id == external_message_id,
        )
        return await session.scalar(stmt)

    async def get_by_id_for_update(
        self,
        session: AsyncSession,
        *,
        message_id: UUID,
    ) -> IncomingMessage | None:
        stmt = (
            select(IncomingMessage)
            .where(IncomingMessage.id == message_id)
            .with_for_update()
        )
        return await session.scalar(stmt)

    async def list_by_status(
        self,
        session: AsyncSession,
        *,
        status: MessageStatus | None,
        limit: int,
        offset: int,
    ) -> list[IncomingMessage]:
        stmt = select(IncomingMessage)
        if status is not None:
            stmt = stmt.where(IncomingMessage.status == status)
        stmt = stmt.order_by(IncomingMessage.created_at.desc()).limit(limit).offset(offset)
        return list((await session.scalars(stmt)).all())

    def add(self, session: AsyncSession, message: IncomingMessage) -> None:
        session.add(message)
