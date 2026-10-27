from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from app.services.visit_instruction import VisitInstruction


@dataclass(frozen=True, slots=True)
class OneCSyncResult:
    record_ref: str
    details: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class OneCVisitChange:
    partner_ref: str
    instruction: VisitInstruction
    client_label: str | None = None


class OneCClient(Protocol):
    async def get_active_plan_partner_refs(
        self,
        *,
        agent_description: str,
        business_date: date,
    ) -> set[str]: ...

    async def apply_visit_instruction(
        self,
        *,
        partner_ref: str,
        business_date: date,
        source_message_id: UUID,
        raw_text: str,
        instruction: VisitInstruction,
    ) -> OneCSyncResult: ...

    async def apply_visit_instructions(
        self,
        *,
        changes: tuple[OneCVisitChange, ...],
        business_date: date,
        source_message_id: UUID,
        raw_text: str,
    ) -> OneCSyncResult: ...
