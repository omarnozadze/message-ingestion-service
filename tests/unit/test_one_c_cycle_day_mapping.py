from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.integrations.one_c.odata import (
    OneCODataClient,
    _active_weekdays,
    _weekday_field,
)
from app.services.visit_instruction import VisitInstruction, VisitOperation


def make_client() -> OneCODataClient:
    return OneCODataClient(
        base_url="http://1c-server/test/odata/standard.odata",
        username="u",
        password="p",
    )


def move_thursday_to_tuesday() -> VisitInstruction:
    return VisitInstruction(
        agent_code="112",
        agent_description="PS112",
        target_days=(2,),
        operation=VisitOperation.MOVE,
        source_day=4,
    )


PLAN_DATE = datetime(2025, 12, 28)  # Sunday


def test_sunday_anchored_week_maps_real_weekdays_to_1c_cycle_slots() -> None:
    assert _weekday_field(1, PLAN_DATE) == "Д2"  # Monday
    assert _weekday_field(2, PLAN_DATE) == "Д3"  # Tuesday
    assert _weekday_field(4, PLAN_DATE) == "Д5"  # Thursday
    assert _weekday_field(7, PLAN_DATE) == "Д1"  # Sunday


def test_observed_ps112_row_d2_d5_means_monday_and_thursday() -> None:
    row = {f"Д{i}": False for i in range(1, 8)}
    row["Д2"] = True
    row["Д5"] = True

    assert _active_weekdays(row, PLAN_DATE) == [1, 4]


def test_move_thursday_to_tuesday_changes_d5_to_d3_for_sunday_anchor() -> None:
    client = make_client()
    row = {
        "LineNumber": "202",
        "Партнер_Key": "partner",
        **{f"Д{i}": False for i in range(1, 8)},
    }
    row["Д2"] = True   # Monday stays
    row["Д5"] = True   # Thursday source

    changed, info = client._apply_instruction_to_rows(
        [row],
        partner_ref="partner",
        instruction=move_thursday_to_tuesday(),
        plan_date=PLAN_DATE,
    )

    assert changed is True
    assert info["before_days"] == [1, 4]
    assert info["after_days"] == [1, 2]
    assert row["Д2"] is True
    assert row["Д3"] is True
    assert row["Д5"] is False


@pytest.mark.asyncio
async def test_verification_uses_cycle_mapping_not_hardcoded_d_numbers() -> None:
    client = make_client()
    row = {
        "LineNumber": "202",
        "Партнер_Key": "partner",
        **{f"Д{i}": False for i in range(1, 8)},
    }
    row["Д2"] = True   # Monday
    row["Д3"] = True   # Tuesday target
    client._get_partner_plan_row = AsyncMock(return_value=row)

    ok = await client._instruction_is_currently_satisfied(
        AsyncMock(),
        plan_ref="plan",
        partner_ref="partner",
        instruction=move_thursday_to_tuesday(),
        plan_date=PLAN_DATE,
    )

    assert ok is True
