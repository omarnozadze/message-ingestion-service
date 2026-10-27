from __future__ import annotations

from datetime import date

from app.services.visit_instruction import (
    VisitOperation,
    parse_visit_instruction,
)


def test_amomishale_plan_wide_remove() -> None:
    instruction = parse_visit_instruction(
        "ნესტან წურწუმია ნიკეა ამომიშალე გეგმიდან",
        agent_code="402",
        business_date=date(2026, 10, 7),
    )
    assert instruction.operation == VisitOperation.REMOVE_ALL
    assert instruction.target_days == ()


def test_amomishale_specific_day_remove() -> None:
    instruction = parse_visit_instruction(
        "ნიკეა სამშაბათიდან ამომიშალე",
        agent_code="402",
        business_date=date(2026, 10, 7),
    )
    assert instruction.operation == VisitOperation.REMOVE
    assert instruction.target_days == (2,)


def test_amomighe_is_remove_too() -> None:
    instruction = parse_visit_instruction(
        "ნიკეა სამშაბათიდან ამომიღე",
        agent_code="402",
        business_date=date(2026, 10, 7),
    )
    assert instruction.operation == VisitOperation.REMOVE
    assert instruction.target_days == (2,)
