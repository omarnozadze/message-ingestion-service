from datetime import date
from uuid import UUID

from app.integrations.one_c.base import OneCSyncResult, OneCVisitChange
from app.services.visit_instruction import VisitInstruction


class MockOneCClient:
    async def get_active_plan_partner_refs(
        self,
        *,
        agent_description: str,
        business_date: date,
    ) -> set[str]:
        del agent_description, business_date
        return set()

    async def apply_visit_instruction(
        self,
        *,
        partner_ref: str,
        business_date: date,
        source_message_id: UUID,
        raw_text: str,
        instruction: VisitInstruction,
    ) -> OneCSyncResult:
        return await self.apply_visit_instructions(
            changes=(
                OneCVisitChange(
                    partner_ref=partner_ref,
                    instruction=instruction,
                ),
            ),
            business_date=business_date,
            source_message_id=source_message_id,
            raw_text=raw_text,
        )

    async def apply_visit_instructions(
        self,
        *,
        changes: tuple[OneCVisitChange, ...],
        business_date: date,
        source_message_id: UUID,
        raw_text: str,
    ) -> OneCSyncResult:
        del business_date, raw_text
        return OneCSyncResult(
            record_ref=f"mock-1c:{source_message_id}",
            details={
                "changes": [
                    {
                        "partner_ref": change.partner_ref,
                        "client_label": change.client_label,
                        "agent": change.instruction.agent_description,
                        "days": list(change.instruction.target_days),
                        "operation": change.instruction.operation.value,
                    }
                    for change in changes
                ]
            },
        )
