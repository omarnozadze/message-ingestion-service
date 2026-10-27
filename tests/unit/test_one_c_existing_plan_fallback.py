import inspect
from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.integrations.one_c import odata as odata_module
from app.integrations.one_c.base import OneCVisitChange
from app.integrations.one_c.odata import OneCODataClient
from app.services.visit_instruction import VisitInstruction, VisitOperation


def make_client() -> OneCODataClient:
    return OneCODataClient(
        base_url="http://1c-server/test/odata/standard.odata",
        username="u",
        password="p",
    )


def make_change(operation: VisitOperation) -> OneCVisitChange:
    return OneCVisitChange(
        partner_ref="partner",
        instruction=VisitInstruction(
            agent_code="103",
            agent_description="PS103",
            target_days=(3,),
            operation=operation,
            source_day=None,
        ),
        client_label="კალია კახაბერ",
    )


@pytest.mark.asyncio
async def test_expired_latest_posted_plan_is_reused_instead_of_creating_new_plan(monkeypatch) -> None:
    client = make_client()
    monkeypatch.setattr(
        client,
        "_get_value",
        AsyncMock(
            return_value=[
                {
                    "Ref_Key": "latest",
                    "DataVersion": "v2",
                    "Number": "00000000164",
                    "Date": "2025-06-01T13:19:30",
                    "Posted": True,
                    "Циклический": True,
                    "ДатаОкончанияЦиклическогоПлана": "2025-06-28T00:00:00",
                },
                {
                    "Ref_Key": "older",
                    "DataVersion": "v1",
                    "Number": "00000000115",
                    "Date": "2025-05-01T00:00:00",
                    "Posted": True,
                    "Циклический": True,
                    "ДатаОкончанияЦиклическогоПлана": "2025-05-31T00:00:00",
                },
            ]
        ),
    )

    plan = await client._find_active_plan(
        AsyncMock(),
        "0b21e4e4-717e-11ed-b960-000c29af9d33",
        date(2026, 10, 7),
    )

    assert plan.ref_key == "latest"
    assert plan.number == "00000000164"


def test_whatsapp_never_auto_creates_agent_plan() -> None:
    # Business rule: WhatsApp never creates a new апПланАгента document. The
    # dormant auto-creation code was removed, so no write path can POST one.
    source = inspect.getsource(odata_module)
    assert "_create_weekly_plan_draft" not in source
    assert "_create_plan_and_apply_additive_changes" not in source
