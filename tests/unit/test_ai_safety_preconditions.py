from app.integrations.one_c.odata import OneCODataClient
from app.services.visit_instruction import VisitInstruction, VisitOperation


def make_client() -> OneCODataClient:
    return OneCODataClient(
        base_url="http://1c-server/test/odata/standard.odata",
        username="u",
        password="p",
    )


def instruction(
    operation: VisitOperation,
    *,
    source_day: int | None = None,
    target_days: tuple[int, ...] = (4,),
) -> VisitInstruction:
    return VisitInstruction(
        agent_code="111",
        agent_description="PS111",
        target_days=target_days,
        operation=operation,
        source_day=source_day,
    )


def test_remove_does_not_claim_success_when_requested_day_is_not_active() -> None:
    client = make_client()
    row = {
        "LineNumber": "3",
        "Партнер_Key": "partner",
        "Д1": True,
        "Д2": False,
        "Д4": False,
    }
    changed, info = client._apply_instruction_to_rows(
        [row],
        partner_ref="partner",
        instruction=instruction(VisitOperation.REMOVE, target_days=(4,)),
    )
    assert changed is False
    assert info["precondition_error"] == "requested remove day is not active for this client"
    assert info["before_days"] == [1]
    assert info["after_days"] == [1]


def test_move_does_not_add_target_when_named_source_day_is_not_active() -> None:
    client = make_client()
    row = {
        "LineNumber": "3",
        "Партнер_Key": "partner",
        "Д1": True,
        "Д2": False,
        "Д4": False,
    }
    changed, info = client._apply_instruction_to_rows(
        [row],
        partner_ref="partner",
        instruction=instruction(VisitOperation.MOVE, source_day=4, target_days=(2,)),
    )
    assert changed is False
    assert "D4" in info["precondition_error"]
    assert row["Д2"] is False


import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_post_verification_requires_move_target_to_be_active() -> None:
    client = make_client()
    client._get_partner_plan_row = AsyncMock(
        return_value={
            "LineNumber": "3",
            "Партнер_Key": "partner",
            "Д2": False,
            "Д4": False,
        }
    )
    ok = await client._instruction_is_currently_satisfied(
        AsyncMock(),
        plan_ref="plan",
        partner_ref="partner",
        instruction=instruction(VisitOperation.MOVE, source_day=4, target_days=(2,)),
    )
    assert ok is False


@pytest.mark.asyncio
async def test_post_verification_accepts_completed_move() -> None:
    client = make_client()
    client._get_partner_plan_row = AsyncMock(
        return_value={
            "LineNumber": "3",
            "Партнер_Key": "partner",
            "Д2": True,
            "Д4": False,
        }
    )
    ok = await client._instruction_is_currently_satisfied(
        AsyncMock(),
        plan_ref="plan",
        partner_ref="partner",
        instruction=instruction(VisitOperation.MOVE, source_day=4, target_days=(2,)),
    )
    assert ok is True
