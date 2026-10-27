from datetime import date
from uuid import UUID

from app.integrations.one_c.odata import OneCODataClient
from app.services.matching.client_query import extract_client_identity_text
from app.services.matching.engine import MatchingEngine
from app.services.matching.types import MatchCandidate
from app.services.visit_instruction import (
    VisitInstruction,
    VisitOperation,
    parse_visit_instruction,
)


def make_one_c() -> OneCODataClient:
    return OneCODataClient(
        base_url="http://1c-server/test/odata/standard.odata",
        username="u",
        password="p",
    )


def instruction(operation: VisitOperation, target_days=(2,)) -> VisitInstruction:
    return VisitInstruction(
        agent_code="111",
        agent_description="PS111",
        target_days=tuple(target_days),
        operation=operation,
    )


def test_remove_clears_only_requested_days() -> None:
    client = make_one_c()
    row = {
        "LineNumber": "3",
        "Партнер_Key": "partner",
        "Д1": True,
        "Д2": True,
        "Д4": True,
    }
    changed, info = client._apply_instruction_to_rows(
        [row], partner_ref="partner", instruction=instruction(VisitOperation.REMOVE)
    )
    assert changed is True
    assert row["Д1"] is True
    assert row["Д2"] is False
    assert row["Д4"] is True
    assert info["after_days"] == [1, 4]


def test_remove_missing_partner_does_not_create_row() -> None:
    client = make_one_c()
    rows = [{"LineNumber": "7", "Партнер_Key": "other", "Д2": True}]
    changed, info = client._apply_instruction_to_rows(
        rows, partner_ref="partner", instruction=instruction(VisitOperation.REMOVE)
    )
    assert changed is False
    assert len(rows) == 1
    assert info["missing_from_plan"] is True


def test_single_remove_parser() -> None:
    result = parse_visit_instruction(
        "გვირილა 0203 დღევანდელზე ამოიღე",
        agent_code="111",
        business_date=date(2026, 10, 5),
    )
    assert result.operation == VisitOperation.REMOVE
    assert result.target_days == (1,)


def test_remove_words_are_not_part_of_client_identity() -> None:
    assert extract_client_identity_text(
        "გვირილა 0203 დღევანდელზე ამოიღე"
    ) == "გვირილა 0203"


def test_address_number_disambiguates_real_possessive_phrase() -> None:
    branch_94 = UUID("00000000-0000-0000-0000-000000000921")
    branch_101 = UUID("00000000-0000-0000-0000-000000000922")
    result = MatchingEngine().match(
        "ფრიდონ ხალვაშის 101გ ორი ნაბიჯების",
        [
            MatchCandidate(
                client_id=branch_94,
                code=None,
                display_name="ორი ნაბიჯი",
                normalized_name="ორი ნაბიჯი",
                aliases=("addr ორი ნაბიჯი ბათუმი ფრიდონ ხალვაში 94",),
                one_c_ref="branch-94",
            ),
            MatchCandidate(
                client_id=branch_101,
                code=None,
                display_name="ორი ნაბიჯი",
                normalized_name="ორი ნაბიჯი",
                aliases=("addr ორი ნაბიჯი ბათუმი ფრიდონ ხალვაში 101გ",),
                one_c_ref="branch-101g",
            ),
        ],
        preferred_one_c_refs={"branch-94", "branch-101g"},
    )
    assert result.matched is True
    assert result.client_id == branch_101


def test_generic_chain_alias_does_not_make_numbered_branch_ambiguous() -> None:
    branch_391 = UUID("00000000-0000-0000-0000-000000000931")
    branch_074 = UUID("00000000-0000-0000-0000-000000000932")
    result = MatchingEngine().match(
        "ორი ნაბიჯი 391 დღევანდელზე დაამატე",
        [
            MatchCandidate(
                client_id=branch_391,
                code=None,
                display_name="ორი ნაბიჯი",
                normalized_name="ორი ნაბიჯი",
                aliases=("ორი ნაბიჯი", "ორი ნაბიჯი 391", "ორი ნაბიჯი n0391"),
                one_c_ref="branch-391",
            ),
            MatchCandidate(
                client_id=branch_074,
                code=None,
                display_name="ორი ნაბიჯი",
                normalized_name="ორი ნაბიჯი",
                aliases=("ორი ნაბიჯი", "ორი ნაბიჯი 074", "ორი ნაბიჯი n0074"),
                one_c_ref="branch-074",
            ),
        ],
        preferred_one_c_refs={"branch-391", "branch-074"},
    )
    assert result.matched is True
    assert result.client_id == branch_391


def test_number_before_chain_name_still_finds_unique_branch() -> None:
    branch_391 = UUID("00000000-0000-0000-0000-000000000941")
    branch_405 = UUID("00000000-0000-0000-0000-000000000942")
    result = MatchingEngine().match(
        "391 ორი ნაბიჯი დღევანდელზე დაამატე",
        [
            MatchCandidate(
                client_id=branch_391,
                code=None,
                display_name="ორი ნაბიჯი",
                normalized_name="ორი ნაბიჯი",
                aliases=("ორი ნაბიჯი", "ორი ნაბიჯი 391"),
                one_c_ref="branch-391",
            ),
            MatchCandidate(
                client_id=branch_405,
                code=None,
                display_name="ორი ნაბიჯი",
                normalized_name="ორი ნაბიჯი",
                aliases=("ორი ნაბიჯი", "ორი ნაბიჯი 405"),
                one_c_ref="branch-405",
            ),
        ],
        preferred_one_c_refs={"branch-391", "branch-405"},
    )
    assert result.matched is True
    assert result.client_id == branch_391


def test_generic_chain_alias_allows_address_number_to_disambiguate() -> None:
    branch_94 = UUID("00000000-0000-0000-0000-000000000951")
    branch_101 = UUID("00000000-0000-0000-0000-000000000952")
    result = MatchingEngine().match(
        "ფრიდონ ხალვაშის 101გ ორი ნაბიჯი",
        [
            MatchCandidate(
                client_id=branch_94,
                code=None,
                display_name="ორი ნაბიჯი",
                normalized_name="ორი ნაბიჯი",
                aliases=(
                    "ორი ნაბიჯი",
                    "addr ორი ნაბიჯი ბათუმი ფრიდონ ხალვაში 94",
                ),
                one_c_ref="branch-94",
            ),
            MatchCandidate(
                client_id=branch_101,
                code=None,
                display_name="ორი ნაბიჯი",
                normalized_name="ორი ნაბიჯი",
                aliases=(
                    "ორი ნაბიჯი",
                    "addr ორი ნაბიჯი ბათუმი ფრიდონ ხალვაში 101გ",
                ),
                one_c_ref="branch-101g",
            ),
        ],
        preferred_one_c_refs={"branch-94", "branch-101g"},
    )
    assert result.matched is True
    assert result.client_id == branch_101
