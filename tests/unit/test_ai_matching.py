from datetime import date
from types import MethodType
from uuid import UUID

import pytest

from app.services.ai_matching import (
    AIReviewRequired,
    AIServiceError,
    OpenAIAssistedMatching,
    _extract_response_text,
)
from app.services.matching.types import MatchCandidate
from app.services.multi_visit_request import ClientVisitAction
from app.services.visit_instruction import VisitInstruction, VisitOperation


ID_088 = UUID("00000000-0000-0000-0000-000000000088")
ID_089 = UUID("00000000-0000-0000-0000-000000000089")
ID_391 = UUID("00000000-0000-0000-0000-000000000391")
ID_392 = UUID("00000000-0000-0000-0000-000000000392")


def assistant(**kwargs) -> OpenAIAssistedMatching:
    return OpenAIAssistedMatching(api_key="test-key", **kwargs)


def candidate(
    client_id: UUID,
    name: str,
    aliases: tuple[str, ...],
    one_c_ref: str,
) -> MatchCandidate:
    return MatchCandidate(
        client_id=client_id,
        code=None,
        display_name=name,
        normalized_name=name,
        aliases=aliases,
        one_c_ref=one_c_ref,
    )


@pytest.mark.asyncio
async def test_ai_parser_understands_move_from_thursday_to_today() -> None:
    service = assistant()

    async def fake(self, **kwargs):
        return {
            "needs_review": False,
            "reason": "same client moves from Thursday to today",
            "confidence": 0.96,
            "actions": [
                {
                    "client_text": "ფუდმარტი 088 კახეთის გზატკეცილი",
                    "operation": "move",
                    "source_day": 4,
                    "target_days": [2],
                }
            ],
        }

    service._structured_response = MethodType(fake, service)
    parsed = await service.parse_visit_request(
        "ფუდმარტი 088 კახეთის გზატკეცილიდან ხუთშაბათიდან ამომიღე და დღევანდელზე ჩასვი",
        agent_code="112",
        business_date=date(2026, 10, 6),  # Tuesday
    )

    assert not parsed.is_multi
    action = parsed.actions[0]
    assert action.client_text == "ფუდმარტი 088 კახეთის გზატკეცილი"
    assert action.instruction.operation == VisitOperation.MOVE
    assert action.instruction.source_day == 4
    assert action.instruction.target_days == (2,)


@pytest.mark.asyncio
async def test_ai_parser_understands_multi_remove_and_add() -> None:
    service = assistant()

    async def fake(self, **kwargs):
        return {
            "needs_review": False,
            "reason": "two removals and one replacement",
            "confidence": 0.94,
            "actions": [
                {
                    "client_text": "ორი ნაბიჯი ფრიდონ ხალვაში 94",
                    "operation": "remove",
                    "source_day": None,
                    "target_days": [2],
                },
                {
                    "client_text": "ორი ნაბიჯი ფრიდონ ხალვაში 101გ",
                    "operation": "remove",
                    "source_day": None,
                    "target_days": [2],
                },
                {
                    "client_text": "ნიკორა ტბელ აბუსერიძე 45",
                    "operation": "add",
                    "source_day": None,
                    "target_days": [2],
                },
            ],
        }

    service._structured_response = MethodType(fake, service)
    parsed = await service.parse_visit_request(
        "მხოლოდ დღევანდელზე ორი ნაბიჯები ამოიღე და ნიკორა ჩასვი",
        agent_code="111",
        business_date=date(2026, 10, 6),
    )

    assert parsed.is_multi
    assert [x.instruction.operation for x in parsed.actions] == [
        VisitOperation.REMOVE,
        VisitOperation.REMOVE,
        VisitOperation.ADD,
    ]


@pytest.mark.asyncio
async def test_ai_parser_low_confidence_requires_review() -> None:
    service = assistant(min_parse_confidence=0.80)

    async def fake(self, **kwargs):
        return {
            "needs_review": False,
            "reason": "unclear day",
            "confidence": 0.52,
            "actions": [
                {
                    "client_text": "ორი ნაბიჯი 391",
                    "operation": "add",
                    "source_day": None,
                    "target_days": [2],
                }
            ],
        }

    service._structured_response = MethodType(fake, service)
    with pytest.raises(AIReviewRequired):
        await service.parse_visit_request(
            "ორი ნაბიჯი 391 ჩასვი",
            agent_code="111",
            business_date=date(2026, 10, 6),
        )


def test_shortlist_strongly_prefers_exact_chain_branch_number() -> None:
    service = assistant(candidate_limit=5)
    candidates = [
        candidate(ID_391, "ორი ნაბიჯი", ("ორი ნაბიჯი 391", "addr თბილისი ფილიალი 391"), "ref391"),
        candidate(ID_392, "ორი ნაბიჯი", ("ორი ნაბიჯი 392", "addr თბილისი ფილიალი 392"), "ref392"),
    ]

    ranked = service._shortlist(
        "ორი ნაბიჯი 391",
        candidates,
        preferred_one_c_refs={"ref391", "ref392"},
    )

    assert ranked[0].client_id == ID_391
    assert ranked[0].retrieval_score > ranked[1].retrieval_score


def test_shortlist_uses_name_number_and_address_together() -> None:
    service = assistant(candidate_limit=5)
    candidates = [
        candidate(
            ID_088,
            "ფუდმარტი",
            (
                "ფუდმარტი 088",
                "addr ფუდმარტი n088 კახეთის გზატკეცილი 12",
            ),
            "ref088",
        ),
        candidate(
            ID_089,
            "ფუდმარტი",
            (
                "ფუდმარტი 089",
                "addr ფუდმარტი n089 წერეთლის გამზირი 5",
            ),
            "ref089",
        ),
    ]

    ranked = service._shortlist(
        "ფუდმარტი 088 კახეთის გზატკეცილი",
        candidates,
        preferred_one_c_refs={"ref088", "ref089"},
    )

    assert ranked[0].client_id == ID_088
    assert "კახეთის" in " ".join(ranked[0].aliases)


@pytest.mark.asyncio
async def test_ai_resolver_can_choose_only_from_local_shortlist() -> None:
    service = assistant(candidate_limit=5)
    candidates = [
        candidate(ID_391, "ორი ნაბიჯი", ("ორი ნაბიჯი 391",), "ref391"),
    ]
    action = ClientVisitAction(
        client_text="ორი ნაბიჯი 391",
        instruction=VisitInstruction(
            agent_code="111",
            agent_description="PS111",
            target_days=(2,),
            operation=VisitOperation.ADD,
        ),
    )

    async def fake(self, **kwargs):
        return {
            "resolutions": [
                {
                    "action_index": 0,
                    "selected_candidate_id": "00000000-0000-0000-0000-000000009999",
                    "confidence": 0.99,
                    "needs_review": False,
                    "reason": "invented",
                }
            ]
        }

    service._structured_response = MethodType(fake, service)
    with pytest.raises(AIServiceError):
        await service.resolve_clients((action,), candidates, preferred_one_c_refs={"ref391"})


@pytest.mark.asyncio
async def test_ai_resolver_identifies_move_candidate_even_outside_active_plan() -> None:
    service = assistant(candidate_limit=5)
    candidates = [
        candidate(ID_088, "ფუდმარტი", ("ფუდმარტი 088",), "ref088"),
    ]
    action = ClientVisitAction(
        client_text="ფუდმარტი 088",
        instruction=VisitInstruction(
            agent_code="112",
            agent_description="PS112",
            target_days=(2,),
            operation=VisitOperation.MOVE,
            source_day=4,
        ),
    )

    async def fake(self, **kwargs):
        return {
            "resolutions": [
                {
                    "action_index": 0,
                    "selected_candidate_id": str(ID_088),
                    "confidence": 0.98,
                    "needs_review": False,
                    "reason": "name and number match",
                }
            ]
        }

    service._structured_response = MethodType(fake, service)
    resolved = await service.resolve_clients(
        (action,), candidates, preferred_one_c_refs={"another-ref"}
    )
    assert resolved[0].match.matched is True
    assert resolved[0].match.client_id == ID_088


def test_extract_response_text_from_raw_responses_api_shape() -> None:
    body = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": '{"needs_review":false}',
                    }
                ],
            }
        ],
    }
    assert _extract_response_text(body) == '{"needs_review":false}'
