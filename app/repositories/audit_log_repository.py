from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


class AuditLogRepository:
    def add(
        self,
        session: AsyncSession,
        *,
        incoming_message_id: UUID,
        event_type: str,
        payload: dict | None = None,
    ) -> None:
        session.add(
            AuditLog(
                incoming_message_id=incoming_message_id,
                event_type=event_type,
                payload=payload or {},
            )
        )
