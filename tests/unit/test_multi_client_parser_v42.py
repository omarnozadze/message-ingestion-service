"""Regression tests for the v42 multi-client parser (moved from multi-client-parser-v42/self_test_v42.py)."""
from __future__ import annotations

from datetime import date

import pytest

from app.services.multi_visit_request import parse_visit_request
from app.services.visit_instruction import VisitOperation

TODAY = date(2026, 10, 19)
TUESDAY = 2
WEDNESDAY = 3


@pytest.mark.parametrize(
    ("text", "expected_clients"),
    [
        (
            "გვირილა 0133 გვირილა 0018 რითეილ 238 ჩასვი სამშაბათზე",
            ["გვირილა 0133", "გვირილა 0018", "რითეილ 238"],
        ),
        (
            "გვირილა 0231, რითეილ 2322, რითეილ K223 ჩასვი სამშაბათზე",
            ["გვირილა 0231", "რითეილ 2322", "რითეილ K223"],
        ),
        (
            "გვირილა 0231 და რითეილ 2322 და რითეილ K223 ჩასვი სამშაბათზე",
            ["გვირილა 0231", "რითეილ 2322", "რითეილ K223"],
        ),
    ],
)
def test_multiple_clients_share_one_instruction(text: str, expected_clients: list[str]) -> None:
    request = parse_visit_request(text, agent_code="110", business_date=TODAY)

    assert request.is_multi is True
    assert [action.client_text for action in request.actions] == expected_clients
    for action in request.actions:
        assert action.instruction.target_days == (TUESDAY,)
        assert action.instruction.operation == VisitOperation.ADD


def test_only_weekday_is_set_operation() -> None:
    request = parse_visit_request(
        "გვირილა #0296 მხოლოდ ოთხშაბათზე ჩასვი",
        agent_code="110",
        business_date=TODAY,
    )

    assert len(request.actions) == 1
    assert request.actions[0].instruction.operation == VisitOperation.SET
    assert request.actions[0].instruction.target_days == (WEDNESDAY,)


def test_only_does_not_turn_removal_into_set() -> None:
    request = parse_visit_request(
        "რითეილ S225 მხოლოდ დღეს ამოიღე",
        agent_code="115",
        business_date=TODAY,
    )
    assert request.actions[0].instruction.operation == VisitOperation.REMOVE


def test_conversational_comma_is_not_a_client_list() -> None:
    request = parse_visit_request(
        "გაუმარჯოს ომარ, გვირილა 0133 ჩასვი სამშაბათზე",
        agent_code="110",
        business_date=TODAY,
    )
    assert request.is_multi is False
