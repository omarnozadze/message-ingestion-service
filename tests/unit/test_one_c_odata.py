from datetime import date, datetime

import pytest

from app.integrations.one_c.odata import AgentPlan, OneCODataClient
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


def test_set_replaces_existing_weekdays() -> None:
    client = make_client()
    row = {
        "LineNumber": "3",
        "Партнер_Key": "partner",
        "Д1": False,
        "Д2": True,
        "Д3": False,
        "Д4": False,
        "Д5": True,
        "Д6": False,
        "Д7": False,
    }
    changed, info = client._apply_instruction_to_rows(
        [row], partner_ref="partner", instruction=instruction(VisitOperation.SET)
    )
    assert changed is True
    assert row["Д2"] is False
    assert row["Д4"] is True
    assert row["Д5"] is False
    assert info["after_days"] == [4]


def test_add_keeps_existing_and_adds_multiple_days() -> None:
    client = make_client()
    row = {
        "LineNumber": "3",
        "Партнер_Key": "partner",
        "Д1": False,
        "Д2": True,
        "Д3": False,
        "Д4": False,
        "Д5": False,
        "Д6": False,
        "Д7": False,
    }
    changed, info = client._apply_instruction_to_rows(
        [row],
        partner_ref="partner",
        instruction=instruction(VisitOperation.ADD, target_days=(4, 5)),
    )
    assert changed is True
    assert row["Д2"] is True
    assert row["Д4"] is True
    assert row["Д5"] is True
    assert info["after_days"] == [2, 4, 5]


def test_move_with_explicit_source_only_clears_source() -> None:
    client = make_client()
    row = {
        "LineNumber": "3",
        "Партнер_Key": "partner",
        "Д2": True,
        "Д4": False,
        "Д5": True,
    }
    changed, info = client._apply_instruction_to_rows(
        [row],
        partner_ref="partner",
        instruction=instruction(VisitOperation.MOVE, source_day=2),
    )
    assert changed is True
    assert row["Д2"] is False
    assert row["Д4"] is True
    assert row["Д5"] is True
    assert info["after_days"] == [4, 5]


def test_move_without_source_replaces_all_weekdays() -> None:
    client = make_client()
    row = {
        "LineNumber": "3",
        "Партнер_Key": "partner",
        "Д1": True,
        "Д2": True,
        "Д4": False,
    }
    changed, info = client._apply_instruction_to_rows(
        [row], partner_ref="partner", instruction=instruction(VisitOperation.MOVE)
    )
    assert changed is True
    assert row["Д1"] is False
    assert row["Д2"] is False
    assert row["Д4"] is True
    assert info["after_days"] == [4]


def test_missing_partner_creates_row_with_all_targets() -> None:
    client = make_client()
    rows = [{"LineNumber": "7", "Партнер_Key": "other", "Д1": True}]
    changed, info = client._apply_instruction_to_rows(
        rows,
        partner_ref="partner",
        instruction=instruction(VisitOperation.ADD, target_days=(2, 4)),
    )
    assert changed is True
    assert len(rows) == 2
    assert rows[1]["LineNumber"] == "8"
    assert rows[1]["Партнер_Key"] == "partner"
    assert rows[1]["Д2"] is True
    assert rows[1]["Д4"] is True
    assert info["created"] is True


def test_active_plan_rules_cover_ps111_current_plan() -> None:
    plan = AgentPlan(
        ref_key="cc9afc91-55a1-11f0-b98e-000c29af9d33",
        number="00000000188",
        date=datetime(2025, 12, 28),
        data_version="v",
        posted=True,
        cyclic=True,
        cyclic_end_date=datetime(2026, 12, 31),
    )
    assert plan.date.date() <= date(2026, 10, 2) <= plan.cyclic_end_date.date()


@pytest.mark.asyncio
async def test_list_partners_continues_after_server_capped_short_pages(monkeypatch) -> None:
    client = make_client()
    calls: list[int] = []

    pages = {
        0: [{"Ref_Key": f"r{i}", "Code": str(i)} for i in range(31)],
        31: [{"Ref_Key": f"r{i}", "Code": str(i)} for i in range(31, 62)],
        62: [{"Ref_Key": f"r{i}", "Code": str(i)} for i in range(62, 67)],
        67: [],
    }

    async def fake_get_value(_http_client, _entity, *, params):
        skip = int(params["$skip"])
        calls.append(skip)
        return pages[skip]

    monkeypatch.setattr(client, "_get_value", fake_get_value)

    partners = await client.list_partners(page_size=500)

    assert len(partners) == 67
    assert calls == [0, 31, 62, 67]


@pytest.mark.asyncio
async def test_list_partners_incremental_filter_uses_creation_and_edit_dates(monkeypatch) -> None:
    from datetime import timezone

    client = make_client()
    captured: list[str] = []

    async def fake_get_value(_http_client, _entity, *, params):
        captured.append(params["$filter"])
        return []

    monkeypatch.setattr(client, "_get_value", fake_get_value)

    since = datetime(2026, 10, 7, 10, 25, tzinfo=timezone.utc)
    result = await client.list_partners(modified_since=since)

    assert result == []
    assert "DeletionMark eq false" in captured[0]
    assert "Клиент eq true" in captured[0]
    assert "ИБР_ДатаРедактирования ge datetime'2026-10-07T10:25:00'" in captured[0]
    assert "ИБР_ДатаСоздания ge datetime'2026-10-07T10:25:00'" in captured[0]
