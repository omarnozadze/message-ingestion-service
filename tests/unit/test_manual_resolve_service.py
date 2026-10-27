from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MatchMethod, MessageSource, MessageStatus
from app.core.exceptions import ActiveClientNotFoundError, InvalidMessageStateError
from app.integrations.one_c.base import OneCSyncResult
from app.models.client import Client
from app.models.incoming_message import IncomingMessage
from app.services.incoming_message_service import IncomingMessageService

MESSAGE_ID = UUID("00000000-0000-0000-0000-000000000999")
CLIENT_ID = UUID("00000000-0000-0000-0000-000000000043")


def make_message(status: MessageStatus = MessageStatus.NEEDS_REVIEW) -> IncomingMessage:
    return IncomingMessage(
        id=MESSAGE_ID,
        source=MessageSource.API,
        external_message_id="manual-test-1",
        raw_text="111 გვირილა 0101 ხუთშაბათზე",
        normalized_text="111 გვირილა 0101 ხუთშაბათზე",
        status=status,
    )


def make_client() -> Client:
    return Client(
        id=CLIENT_ID,
        code="0043",
        display_name="გვირილა",
        normalized_name="გვირილა",
        is_active=True,
        one_c_ref="e2973824-b424-4840-973c-852dfc96f721",
    )


def make_service(*, message: IncomingMessage, client: Client | None):
    message_repo = MagicMock()
    message_repo.get_by_id_for_update = AsyncMock(return_value=message)

    client_repo = MagicMock()
    client_repo.get_active_by_id = AsyncMock(return_value=client)

    audit_repo = MagicMock()

    one_c_client = MagicMock()
    one_c_client.apply_visit_instruction = AsyncMock(
        return_value=OneCSyncResult(record_ref=f"mock-1c:{MESSAGE_ID}")
    )

    service = IncomingMessageService(
        message_repo=message_repo,
        client_repo=client_repo,
        audit_repo=audit_repo,
        matcher=MagicMock(),
        one_c_client=one_c_client,
    )
    return service, audit_repo, one_c_client


@pytest.mark.asyncio
async def test_manual_resolve_syncs_message() -> None:
    message = make_message()
    client = make_client()
    service, audit_repo, one_c_client = make_service(message=message, client=client)
    session = AsyncMock(spec=AsyncSession)

    result = await service.resolve_manually(
        session,
        message_id=MESSAGE_ID,
        client_id=CLIENT_ID,
    )

    assert result.status == MessageStatus.SYNCED
    assert result.matched_client_id == CLIENT_ID
    assert result.match_method == MatchMethod.MANUAL
    assert result.match_score == 100.0
    assert result.one_c_record_ref == f"mock-1c:{MESSAGE_ID}"
    one_c_client.apply_visit_instruction.assert_awaited_once()

    event_types = [call.kwargs["event_type"] for call in audit_repo.add.call_args_list]
    assert event_types == ["MANUALLY_RESOLVED", "VISIT_INSTRUCTION_PARSED", "ONE_C_SYNCED"]


@pytest.mark.asyncio
async def test_manual_resolve_requires_needs_review_status() -> None:
    message = make_message(MessageStatus.SYNCED)
    service, _, one_c_client = make_service(message=message, client=make_client())
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(InvalidMessageStateError):
        await service.resolve_manually(
            session,
            message_id=MESSAGE_ID,
            client_id=CLIENT_ID,
        )

    one_c_client.apply_visit_instruction.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_resolve_requires_active_client() -> None:
    message = make_message()
    service, _, one_c_client = make_service(message=message, client=None)
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(ActiveClientNotFoundError):
        await service.resolve_manually(
            session,
            message_id=MESSAGE_ID,
            client_id=CLIENT_ID,
        )

    one_c_client.apply_visit_instruction.assert_not_awaited()
