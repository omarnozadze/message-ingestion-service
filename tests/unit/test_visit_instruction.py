from datetime import date

import pytest

from app.services.visit_instruction import (
    VisitInstructionParseError,
    VisitOperation,
    parse_visit_instruction,
)

BUSINESS_DATE = date(2026, 10, 5)  # Monday


def parse(text: str):
    return parse_visit_instruction(
        text,
        agent_code="111",
        business_date=BUSINESS_DATE,
    )


def test_add_multiple_named_days() -> None:
    result = parse("გვირილა 0223 ჩასვი სამშაბათი ხუთშაბათზე")
    assert result.operation == VisitOperation.ADD
    assert result.target_days == (2, 4)
    assert result.agent_description == "PS111"


def test_today_is_resolved_from_business_date() -> None:
    result = parse("#452 ორი ნაბიჯი ჩამისვი დღევანდელზე")
    assert result.operation == VisitOperation.ADD
    assert result.target_days == (1,)


def test_today_and_friday_are_both_added() -> None:
    result = parse("#223 ნიკორა ჩასვი დღევანდელზე და პარასკევზე")
    assert result.operation == VisitOperation.ADD
    assert result.target_days == (1, 5)


def test_explicit_move_uses_first_day_as_source() -> None:
    result = parse("გვირილა სამშაბათიდან ხუთშაბათზე გადაიტანე")
    assert result.operation == VisitOperation.MOVE
    assert result.source_day == 2
    assert result.target_days == (4,)


def test_move_without_source_sets_requested_target() -> None:
    result = parse("ნიკორა ხუთშაბათზე გადაიტანე")
    assert result.operation == VisitOperation.MOVE
    assert result.source_day is None
    assert result.target_days == (4,)


def test_add_without_named_day_defaults_to_today() -> None:
    result = parse("გვირილა ჩასვი")
    assert result.operation == VisitOperation.ADD
    assert result.target_days == (1,)


def test_message_without_day_or_action_requires_review() -> None:
    with pytest.raises(VisitInstructionParseError):
        parse("გვირილა")


def test_remove_from_one_day_and_add_to_another_is_move() -> None:
    monday = date(2026, 10, 19)
    instruction = parse_visit_instruction(
        "შპს კალია კახაბერ დღევანდელიდან ამოიღე და ოთხშაბათს ჩასვი",
        agent_code="103",
        business_date=monday,
    )
    assert instruction.operation == VisitOperation.MOVE
    assert instruction.source_day == 1
    assert instruction.target_days == (3,)


def test_source_day_is_the_one_written_with_from_suffix() -> None:
    instruction = parse_visit_instruction(
        "კალია პარასკევზე ჩასვი და ორშაბათიდან ამოიღე",
        agent_code="103",
        business_date=date(2026, 10, 19),
    )
    assert instruction.operation == VisitOperation.MOVE
    assert instruction.source_day == 1
    assert instruction.target_days == (5,)


def test_remove_plus_add_with_three_days_is_not_guessed() -> None:
    with pytest.raises(VisitInstructionParseError):
        parse_visit_instruction(
            "კალია ორშაბათს და სამშაბათს ამოიღე და პარასკევზე ჩასვი",
            agent_code="103",
            business_date=date(2026, 10, 19),
        )
