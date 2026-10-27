from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MatchMethod, MessageSource, MessageStatus
from app.integrations.one_c.base import OneCSyncResult
from app.models.client import Client
from app.schemas.incoming_message import IncomingMessageCreate
from app.services.agent_resolver import AgentResolution
from app.services.ai_matching import AIClientResolution, AIReviewRequired
from app.services.incoming_message_service import IncomingMessageService
from app.services.matching.types import MatchCandidate, MatchResult
from app.services.multi_visit_request import ClientVisitAction, VisitRequest
from app.services.visit_instruction import VisitInstruction, VisitOperation


CLIENT_ID = UUID("00000000-0000-0000-0000-000000000391")


@pytest.mark.asyncio
async def test_ai_resolves_client_when_local_match_is_not_strong() -> None:
    message_repo = MagicMock()
    message_repo.find_by_external_id = AsyncMock(return_value=None)
    message_repo.add = MagicMock()

    client = Client(
        id=CLIENT_ID,
        code="00-TEST",
        display_name="ორი ნაბიჯი 391",
        normalized_name="ორი ნაბიჯი 391",
        is_active=True,
        one_c_ref="ref391",
    )
    client_repo = MagicMock()
    client_repo.list_active_match_candidates = AsyncMock(
        return_value=[
            MatchCandidate(
                client_id=CLIENT_ID,
                code=client.code,
                display_name=client.display_name,
                normalized_name=client.normalized_name,
                aliases=("ორი ნაბიჯი 391",),
                one_c_ref="ref391",
            )
        ]
    )
    client_repo.get_active_by_id = AsyncMock(return_value=client)

    audit_repo = MagicMock()
    matcher = MagicMock()
    matcher.match.return_value = MatchResult(matched=False, ambiguous=True)
    matcher.rank_for_review.return_value = ()

    one_c_client = MagicMock()
    one_c_client.get_active_plan_partner_refs = AsyncMock(return_value={"ref391"})
    one_c_client.apply_visit_instruction = AsyncMock(
        return_value=OneCSyncResult(record_ref="plan-ref", details={"verified": True})
    )

    agent_resolver = MagicMock()
    agent_resolver.resolve.return_value = AgentResolution(
        agent_code="111",
        agent_description="PS111",
        source="sender_id_message_prefix",
        client_text="ორი ნაბიჯი 391 დღევანდელზე დაამატე",
    )

    instruction = VisitInstruction(
        agent_code="111",
        agent_description="PS111",
        target_days=(2,),
        operation=VisitOperation.ADD,
    )
    action = ClientVisitAction(client_text="ორი ნაბიჯი 391", instruction=instruction)

    ai = MagicMock()
    ai.parse_visit_request = AsyncMock(return_value=VisitRequest(actions=(action,), is_multi=False))
    ai.resolve_clients = AsyncMock(
        return_value=(
            AIClientResolution(
                action_index=0,
                match=MatchResult(
                    matched=True,
                    ambiguous=False,
                    client_id=CLIENT_ID,
                    method=MatchMethod.FUZZY,
                    score=96.0,
                    evidence="ai:number+name",
                ),
                reason="391 uniquely matches the chain branch",
            ),
        )
    )

    service = IncomingMessageService(
        message_repo=message_repo,
        client_repo=client_repo,
        audit_repo=audit_repo,
        matcher=matcher,
        one_c_client=one_c_client,
        agent_resolver=agent_resolver,
        ai_matching=ai,
    )
    session = AsyncMock(spec=AsyncSession)

    result = await service.process(
        session,
        IncomingMessageCreate(
            source=MessageSource.WHATSAPP,
            external_message_id="ai-1",
            sender_id="+995555000999",
            text="111 ორი ნაბიჯი 391 დღევანდელზე დაამატე",
        ),
    )

    assert result.status == MessageStatus.SYNCED
    assert result.matched_client_id == CLIENT_ID
    ai.resolve_clients.assert_awaited_once()
    one_c_client.apply_visit_instruction.assert_awaited_once()


@pytest.mark.asyncio
async def test_ai_review_stops_before_loading_candidates_or_writing_1c() -> None:
    message_repo = MagicMock()
    message_repo.find_by_external_id = AsyncMock(return_value=None)
    message_repo.add = MagicMock()

    client_repo = MagicMock()
    client_repo.list_active_match_candidates = AsyncMock()
    audit_repo = MagicMock()
    matcher = MagicMock()
    one_c_client = MagicMock()
    one_c_client.get_active_plan_partner_refs = AsyncMock()
    one_c_client.apply_visit_instruction = AsyncMock()

    agent_resolver = MagicMock()
    agent_resolver.resolve.return_value = AgentResolution(
        agent_code="112",
        agent_description="PS112",
        source="sender_id_message_prefix",
        client_text="გაურკვეველი ტექსტი",
    )

    ai = MagicMock()
    ai.parse_visit_request = AsyncMock(side_effect=AIReviewRequired("client/day unclear"))

    service = IncomingMessageService(
        message_repo=message_repo,
        client_repo=client_repo,
        audit_repo=audit_repo,
        matcher=matcher,
        one_c_client=one_c_client,
        agent_resolver=agent_resolver,
        ai_matching=ai,
    )
    session = AsyncMock(spec=AsyncSession)

    result = await service.process(
        session,
        IncomingMessageCreate(
            source=MessageSource.WHATSAPP,
            external_message_id="ai-2",
            sender_id="+995555000999",
            text="112 გაურკვეველი ტექსტი",
        ),
    )

    assert result.status == MessageStatus.NEEDS_REVIEW
    assert result.error_message.startswith("AI review required:")
    client_repo.list_active_match_candidates.assert_not_awaited()
    one_c_client.apply_visit_instruction.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_outside_requesting_plan_is_allowed_and_does_not_touch_other_agents() -> None:
    message_repo = MagicMock()
    message_repo.find_by_external_id = AsyncMock(return_value=None)
    message_repo.add = MagicMock()

    client = Client(
        id=CLIENT_ID,
        code="00-TEST",
        display_name="შპს კალია კახაბერ",
        normalized_name="შპს კალია კახაბერ",
        is_active=True,
        one_c_ref="ref-kalia",
    )
    client_repo = MagicMock()
    client_repo.list_active_match_candidates = AsyncMock(
        return_value=[
            MatchCandidate(
                client_id=CLIENT_ID,
                code=client.code,
                display_name=client.display_name,
                normalized_name=client.normalized_name,
                aliases=("შპს კალია კახაბერ",),
                one_c_ref="ref-kalia",
            )
        ]
    )
    client_repo.get_active_by_id = AsyncMock(return_value=client)

    audit_repo = MagicMock()
    matcher = MagicMock()
    one_c_client = MagicMock()
    # Client is not currently in PS103. This is legitimate for ADD.
    one_c_client.get_active_plan_partner_refs = AsyncMock(return_value={"different-ref"})
    one_c_client.apply_visit_instruction = AsyncMock(
        return_value=OneCSyncResult(record_ref="ps103-plan", details={"verified": True})
    )

    agent_resolver = MagicMock()
    agent_resolver.resolve.return_value = AgentResolution(
        agent_code="103",
        agent_description="PS103",
        source="sender_id_message_prefix",
        client_text="შპს კალია კახაბერ ოთხშაბათს ჩასვი",
    )

    instruction = VisitInstruction(
        agent_code="103",
        agent_description="PS103",
        target_days=(3,),
        operation=VisitOperation.ADD,
    )
    action = ClientVisitAction(client_text="შპს კალია კახაბერ", instruction=instruction)
    ai = MagicMock()
    ai.parse_visit_request = AsyncMock(return_value=VisitRequest(actions=(action,), is_multi=False))
    ai.resolve_clients = AsyncMock(
        return_value=(
            AIClientResolution(
                action_index=0,
                match=MatchResult(
                    matched=True,
                    ambiguous=False,
                    client_id=CLIENT_ID,
                    method=MatchMethod.FUZZY,
                    score=99.0,
                    evidence="ai:exact global client",
                ),
                reason="exact client identity",
            ),
        )
    )

    service = IncomingMessageService(
        message_repo=message_repo, client_repo=client_repo, audit_repo=audit_repo,
        matcher=matcher, one_c_client=one_c_client, agent_resolver=agent_resolver,
        ai_matching=ai,
    )
    session = AsyncMock(spec=AsyncSession)
    result = await service.process(
        session,
        IncomingMessageCreate(
            source=MessageSource.WHATSAPP, external_message_id="scope-add-1",
            sender_id="+995555000999", text="103 შპს კალია კახაბერ ოთხშაბათს ჩასვი",
        ),
    )

    assert result.status == MessageStatus.SYNCED
    one_c_client.apply_visit_instruction.assert_awaited_once()
    kwargs = one_c_client.apply_visit_instruction.await_args.kwargs
    assert kwargs["instruction"].agent_code == "103"


@pytest.mark.asyncio
async def test_move_outside_requesting_plan_is_blocked_before_1c_write() -> None:
    message_repo = MagicMock()
    message_repo.find_by_external_id = AsyncMock(return_value=None)
    message_repo.add = MagicMock()

    client = Client(
        id=CLIENT_ID, code="00-TEST", display_name="შპს კალია კახაბერ",
        normalized_name="შპს კალია კახაბერ", is_active=True, one_c_ref="ref-kalia",
    )
    client_repo = MagicMock()
    client_repo.list_active_match_candidates = AsyncMock(
        return_value=[MatchCandidate(
            client_id=CLIENT_ID, code=client.code, display_name=client.display_name,
            normalized_name=client.normalized_name, aliases=("შპს კალია კახაბერ",),
            one_c_ref="ref-kalia",
        )]
    )
    client_repo.get_active_by_id = AsyncMock(return_value=client)
    audit_repo = MagicMock()
    matcher = MagicMock()
    one_c_client = MagicMock()
    one_c_client.get_active_plan_partner_refs = AsyncMock(return_value={"different-ref"})
    one_c_client.apply_visit_instruction = AsyncMock()

    agent_resolver = MagicMock()
    agent_resolver.resolve.return_value = AgentResolution(
        agent_code="103", agent_description="PS103", source="sender_id_message_prefix",
        client_text="შპს კალია კახაბერ დღევანდელიდან ამოიღე და ოთხშაბათს ჩასვი",
    )
    instruction = VisitInstruction(
        agent_code="103", agent_description="PS103", target_days=(3,),
        operation=VisitOperation.MOVE, source_day=2,
    )
    action = ClientVisitAction(client_text="შპს კალია კახაბერ", instruction=instruction)
    ai = MagicMock()
    ai.parse_visit_request = AsyncMock(return_value=VisitRequest(actions=(action,), is_multi=False))
    ai.resolve_clients = AsyncMock(return_value=(AIClientResolution(
        action_index=0,
        match=MatchResult(
            matched=True, ambiguous=False, client_id=CLIENT_ID,
            method=MatchMethod.FUZZY, score=99.0, evidence="ai:exact global client",
        ),
        reason="exact client identity",
    ),))

    service = IncomingMessageService(
        message_repo=message_repo, client_repo=client_repo, audit_repo=audit_repo,
        matcher=matcher, one_c_client=one_c_client, agent_resolver=agent_resolver,
        ai_matching=ai,
    )
    session = AsyncMock(spec=AsyncSession)
    result = await service.process(
        session,
        IncomingMessageCreate(
            source=MessageSource.WHATSAPP, external_message_id="scope-move-1",
            sender_id="+995555000999",
            text="103 შპს კალია კახაბერ დღევანდელიდან ამოიღე და ოთხშაბათს ჩასვი",
        ),
    )

    assert result.status == MessageStatus.NEEDS_REVIEW
    assert "requesting agent plan for move" in result.error_message
    one_c_client.apply_visit_instruction.assert_not_awaited()
