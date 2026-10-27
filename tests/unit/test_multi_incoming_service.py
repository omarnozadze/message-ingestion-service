from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MatchMethod, MessageSource, MessageStatus
from app.integrations.one_c.base import OneCSyncResult
from app.models.client import Client
from app.schemas.incoming_message import IncomingMessageCreate
from app.services.agent_resolver import AgentResolution
from app.services.incoming_message_service import IncomingMessageService
from app.services.matching.types import MatchCandidate, MatchResult


IDS = [
    UUID("00000000-0000-0000-0000-000000000931"),
    UUID("00000000-0000-0000-0000-000000000932"),
    UUID("00000000-0000-0000-0000-000000000933"),
]
REFS = ["ref-94", "ref-101g", "ref-45"]


def match_by_query(*rules: tuple[str, MatchResult]):
    """Matcher stub keyed by the client text, independent of call count.

    The service may consult the matcher more than once per action (active plan
    first, then the full index), so a positional side_effect list is brittle.
    """

    def _match(query: str, _candidates, **_kwargs) -> MatchResult:
        for needle, result in rules:
            if needle in query:
                return result
        return MatchResult(False, False)

    return _match


def make_client(index: int, name: str) -> Client:
    return Client(
        id=IDS[index],
        code=None,
        display_name=name,
        normalized_name=name,
        is_active=True,
        one_c_ref=REFS[index],
    )


@pytest.mark.asyncio
async def test_multi_request_resolves_every_client_then_writes_once() -> None:
    message_repo = MagicMock()
    message_repo.find_by_external_id = AsyncMock(return_value=None)
    message_repo.add = MagicMock()

    clients = [
        make_client(0, "ორი ნაბიჯი ფრიდონ ხალვაში 94"),
        make_client(1, "ორი ნაბიჯი ფრიდონ ხალვაში 101გ"),
        make_client(2, "ნიკორა ტბელ აბუსერიძე 45"),
    ]
    client_repo = MagicMock()
    client_repo.list_active_match_candidates = AsyncMock(
        return_value=[
            MatchCandidate(
                client_id=client.id,
                code=None,
                display_name=client.display_name,
                normalized_name=client.normalized_name,
                aliases=(),
                one_c_ref=client.one_c_ref,
            )
            for client in clients
        ]
    )
    client_repo.get_active_by_id = AsyncMock(side_effect=clients)

    audit_repo = MagicMock()
    matcher = MagicMock()
    matcher.match.side_effect = match_by_query(
        ("94", MatchResult(True, False, IDS[0], MatchMethod.FUZZY, 96.0, "address:94")),
        ("101გ", MatchResult(True, False, IDS[1], MatchMethod.FUZZY, 95.0, "address:101g")),
        ("45", MatchResult(True, False, IDS[2], MatchMethod.FUZZY, 94.0, "address:45")),
    )

    one_c_client = MagicMock()
    one_c_client.get_active_plan_partner_refs = AsyncMock(
        return_value={REFS[0], REFS[1]}
    )
    one_c_client.apply_visit_instructions = AsyncMock(
        return_value=OneCSyncResult(record_ref="plan-ref")
    )

    agent_resolver = MagicMock()
    text = (
        "მხოლოდ დღევანდელ დღეზე ფრიდონ ხალვაშის 94 ორი ნაბიჯის და "
        "ფრიდონ ხალვაშის 101გ ორი ნაბიჯების ამოღება და მათნაცვლად "
        "ნიკორა ტბელ აბუსერიძის 45 ის ჩასმა გვინდა"
    )
    agent_resolver.resolve.return_value = AgentResolution(
        agent_code="111",
        agent_description="PS111",
        source="sender_id",
        client_text=text,
    )

    service = IncomingMessageService(
        message_repo=message_repo,
        client_repo=client_repo,
        audit_repo=audit_repo,
        matcher=matcher,
        one_c_client=one_c_client,
        agent_resolver=agent_resolver,
    )
    session = AsyncMock(spec=AsyncSession)

    result = await service.process(
        session,
        IncomingMessageCreate(
            source=MessageSource.WHATSAPP,
            external_message_id="multi-1",
            sender_id="+995500000000",
            text=text,
        ),
    )

    assert result.status == MessageStatus.SYNCED
    one_c_client.apply_visit_instructions.assert_awaited_once()
    call = one_c_client.apply_visit_instructions.await_args.kwargs
    assert len(call["changes"]) == 3
    assert [change.instruction.operation.value for change in call["changes"]] == [
        "remove",
        "remove",
        "add",
    ]
    assert result.match_evidence.startswith("multi:3 clients")
    assert "remove=2" in result.match_evidence
    assert "add=1" in result.match_evidence


@pytest.mark.asyncio
async def test_multi_request_does_not_touch_1c_if_one_client_is_ambiguous() -> None:
    message_repo = MagicMock()
    message_repo.find_by_external_id = AsyncMock(return_value=None)
    message_repo.add = MagicMock()

    client_repo = MagicMock()
    client_repo.list_active_match_candidates = AsyncMock(return_value=[])
    client_repo.get_active_by_id = AsyncMock(return_value=None)

    audit_repo = MagicMock()
    matcher = MagicMock()
    matcher.match.side_effect = match_by_query(
        ("94", MatchResult(True, False, IDS[0], MatchMethod.FUZZY, 96.0, "address:94")),
        ("101გ", MatchResult(False, True)),
        ("45", MatchResult(True, False, IDS[2], MatchMethod.FUZZY, 94.0, "address:45")),
    )

    client_repo.get_active_by_id = AsyncMock(
        side_effect=[make_client(0, "ორი ნაბიჯი 94"), make_client(2, "ნიკორა 45")]
    )

    one_c_client = MagicMock()
    one_c_client.get_active_plan_partner_refs = AsyncMock(return_value={REFS[0], REFS[1]})
    one_c_client.apply_visit_instructions = AsyncMock()

    text = (
        "მხოლოდ დღევანდელ დღეზე ფრიდონ ხალვაშის 94 ორი ნაბიჯის და "
        "ფრიდონ ხალვაშის 101გ ორი ნაბიჯების ამოღება და მათნაცვლად "
        "ნიკორა ტბელ აბუსერიძის 45 ის ჩასმა გვინდა"
    )
    agent_resolver = MagicMock()
    agent_resolver.resolve.return_value = AgentResolution(
        agent_code="111",
        agent_description="PS111",
        source="sender_id",
        client_text=text,
    )

    service = IncomingMessageService(
        message_repo=message_repo,
        client_repo=client_repo,
        audit_repo=audit_repo,
        matcher=matcher,
        one_c_client=one_c_client,
        agent_resolver=agent_resolver,
    )
    session = AsyncMock(spec=AsyncSession)

    result = await service.process(
        session,
        IncomingMessageCreate(
            source=MessageSource.WHATSAPP,
            external_message_id="multi-2",
            sender_id="+995500000000",
            text=text,
        ),
    )

    assert result.status == MessageStatus.NEEDS_REVIEW
    assert result.error_message.startswith("multiple possible clients found:")
    one_c_client.apply_visit_instructions.assert_not_awaited()
