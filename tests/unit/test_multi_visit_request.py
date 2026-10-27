from datetime import date

from app.services.multi_visit_request import parse_visit_request
from app.services.visit_instruction import VisitOperation

BUSINESS_DATE = date(2026, 10, 6)  # Tuesday


def test_real_replacement_message_becomes_two_removes_and_one_add() -> None:
    request = parse_visit_request(
        "გაუმარჯოს ომარ, როგორც ყოველთვის ცვლილებაა ჩეკლისტში. "
        "მხოლოდ დღევანდელ დღეზე ფრიდონ ხალვაშის 94 ორი ნაბიჯის და "
        "ფრიდონ ხალვაშის 101გ ორი ნაბიჯების ამოღება და მათნაცვლად "
        "ნიკორა ტბელ აბუსერიძის 45 ის ჩასმა გვინდა",
        agent_code="111",
        business_date=BUSINESS_DATE,
    )

    assert request.is_multi is True
    assert len(request.actions) == 3

    assert request.actions[0].client_text == "ფრიდონ ხალვაშის 94 ორი ნაბიჯის"
    assert request.actions[0].instruction.operation == VisitOperation.REMOVE
    assert request.actions[0].instruction.target_days == (2,)

    assert request.actions[1].client_text == "ფრიდონ ხალვაშის 101გ ორი ნაბიჯების"
    assert request.actions[1].instruction.operation == VisitOperation.REMOVE
    assert request.actions[1].instruction.target_days == (2,)

    assert request.actions[2].client_text == "ნიკორა ტბელ აბუსერიძის 45"
    assert request.actions[2].instruction.operation == VisitOperation.ADD
    assert request.actions[2].instruction.target_days == (2,)


def test_leading_imperatives_can_describe_replacement() -> None:
    request = parse_visit_request(
        "დღეს ამოიღე ორი ნაბიჯი ფრიდონ ხალვაში 94 და ორი ნაბიჯი ფრიდონ ხალვაში 101გ "
        "და ჩასვი ნიკორა ტბელ აბუსერიძე 45",
        agent_code="111",
        business_date=BUSINESS_DATE,
    )

    assert request.is_multi is True
    assert [a.instruction.operation for a in request.actions] == [
        VisitOperation.REMOVE,
        VisitOperation.REMOVE,
        VisitOperation.ADD,
    ]


def test_single_client_message_stays_single() -> None:
    request = parse_visit_request(
        "გვირილა 0203 პარასკევზე ჩასვი",
        agent_code="111",
        business_date=BUSINESS_DATE,
    )
    assert request.is_multi is False
    assert len(request.actions) == 1
    assert request.actions[0].instruction.operation == VisitOperation.ADD
