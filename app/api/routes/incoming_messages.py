import asyncio
from datetime import datetime
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incoming_message import IncomingMessage
from app.services.matching.client_query import canonical_search_text, extract_client_identity_text

from app.api.deps import get_incoming_message_service
from app.core.enums import MessageSource, MessageStatus
from app.core.exceptions import (
    ActiveClientNotFoundError,
    IncomingMessageNotFoundError,
    InvalidMessageStateError,
)
from app.db.session import AsyncSessionFactory, get_db_session
from app.schemas.incoming_message import (
    IncomingMessageCreate,
    IncomingMessageResponse,
    ManualResolveRequest,
)
from app.services.agent_resolver import AgentResolutionError
from app.services.incoming_message_service import IncomingMessageService

router = APIRouter(tags=["incoming-messages"])
logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task] = set()
_inflight_external_ids: set[str] = set()


def _task_key(payload: IncomingMessageCreate) -> str | None:
    if not payload.external_message_id:
        return None
    return f"{payload.source.value}:{payload.external_message_id}"


async def _process_in_background(
    payload: IncomingMessageCreate,
    service: IncomingMessageService,
    key: str | None,
) -> None:
    try:
        async with AsyncSessionFactory() as session:
            await service.process(session, payload)
    except Exception as exc:
        logger.exception("background incoming-message processing failed")
        # Do not leave an accepted WhatsApp request stuck in SYNC_PENDING forever
        # after an unexpected exception outside the service's normal error handling.
        if payload.external_message_id:
            try:
                async with AsyncSessionFactory() as session:
                    message = await service.get_by_external_id(
                        session,
                        source=payload.source,
                        external_message_id=payload.external_message_id,
                    )
                    if message is not None and str(message.status.value).lower() not in {
                        "synced", "needs_review", "failed"
                    }:
                        message.status = MessageStatus.FAILED
                        message.error_message = (
                            "background processing failed: "
                            f"{type(exc).__name__}: {str(exc).strip() or type(exc).__name__}"
                        )
                        await session.commit()
            except Exception:
                logger.exception("failed to persist background processing failure")
    finally:
        if key:
            _inflight_external_ids.discard(key)


def _start_background_processing(
    payload: IncomingMessageCreate,
    service: IncomingMessageService,
) -> None:
    key = _task_key(payload)
    if key and key in _inflight_external_ids:
        return
    if key:
        _inflight_external_ids.add(key)

    task = asyncio.create_task(
        _process_in_background(payload, service, key),
        name=f"incoming-message:{payload.external_message_id or 'no-id'}",
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.post("/incoming-message/preflight")
async def preflight_incoming_message(
    payload: IncomingMessageCreate,
    service: Annotated[IncomingMessageService, Depends(get_incoming_message_service)],
) -> dict[str, str | bool | None]:
    """Validate sender/agent routing without creating a DB message or touching 1C."""
    try:
        agent = service.agent_resolver.resolve(
            raw_text=payload.text,
            sender_id=payload.sender_id,
            sender_name=payload.sender_name,
        )
    except AgentResolutionError:
        # Deliberately do not expose mapping details to unknown WhatsApp senders.
        return {"authorized": False}

    return {
        "authorized": True,
        "agent_code": agent.agent_code,
        "agent_description": agent.agent_description,
        "resolution_source": agent.source,
    }


@router.post(
    "/incoming-message",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_incoming_message(
    payload: IncomingMessageCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[IncomingMessageService, Depends(get_incoming_message_service)],
):
    # Idempotency: if this WhatsApp message already exists, return its current state.
    if payload.external_message_id:
        existing = await service.get_by_external_id(
            session,
            source=payload.source,
            external_message_id=payload.external_message_id,
        )
        if existing is not None:
            return IncomingMessageResponse.model_validate(existing)

    # Detach the long AI/1C workflow from the HTTP client connection. The bridge
    # receives 202 immediately and polls by external_message_id. Closing or timing
    # out the HTTP request can no longer cancel a long 1C operation.
    _start_background_processing(payload, service)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "external_message_id": payload.external_message_id,
        },
    )


@router.get(
    "/incoming-message/by-external-id",
    response_model=IncomingMessageResponse,
)
async def get_incoming_message_by_external_id(
    source: Annotated[MessageSource, Query()],
    external_message_id: Annotated[str, Query(min_length=1, max_length=256)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[IncomingMessageService, Depends(get_incoming_message_service)],
) -> IncomingMessageResponse:
    message = await service.get_by_external_id(
        session,
        source=source,
        external_message_id=external_message_id,
    )
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="incoming message not found",
        )
    return IncomingMessageResponse.model_validate(message)


@router.get("/incoming-message/{message_id}/choices")
async def get_incoming_message_choices(
    message_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[IncomingMessageService, Depends(get_incoming_message_service)],
) -> dict:
    """Return deterministic local client choices for a reviewable match.

    The endpoint never calls AI and never writes to 1C. Every returned client
    is an active row from the local search index.
    """
    message = await session.get(IncomingMessage, message_id)
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="incoming message not found",
        )
    if message.status != MessageStatus.NEEDS_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="client choices are only available while message needs review",
        )

    error = (message.error_message or "").lower()
    match_review = any(
        marker in error
        for marker in (
            "multiple possible clients",
            "client not found",
            "multiple candidates",
            "ambiguous",
            "similar client",
        )
    )
    if not match_review:
        return {
            "message_id": str(message.id),
            "external_message_id": message.external_message_id,
            "reason": message.error_message,
            "query": None,
            "normalized_query": None,
            "choices": [],
        }

    try:
        agent = service.agent_resolver.resolve(
            raw_text=message.raw_text,
            sender_id=message.sender_id,
            sender_name=message.sender_name,
        )
    except AgentResolutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    candidates = await service.client_repo.list_active_match_candidates(session)
    identity = extract_client_identity_text(agent.client_text) or agent.client_text

    # Use the same agent-plan context as the real matching workflow. Previously
    # this review endpoint passed an empty preferred set, so the WhatsApp choice
    # list could surface globally similar but operationally irrelevant clients.
    preferred_one_c_refs: set[str] = set()
    try:
        preferred_one_c_refs = await service.one_c_client.get_active_plan_partner_refs(
            agent_description=agent.agent_description,
            business_date=datetime.now(service.business_timezone).date(),
        )
    except Exception as exc:
        logger.warning(
            "client-choice active-plan context unavailable message=%s error=%s",
            message.id,
            exc,
        )

    ranked = service.matcher.rank_for_review(
        identity,
        candidates,
        preferred_one_c_refs=preferred_one_c_refs,
        limit=5,
    )
    candidate_by_id = {candidate.client_id: candidate for candidate in candidates}

    # Do not show arbitrary low-similarity database rows. For a hard ambiguity
    # keep the close cluster; for a not-found result show only credible nearby
    # candidates so the user can see what the matcher actually considered.
    selected = []
    if ranked:
        top_score = float(ranked[0].score)
        floor = max(55.0, top_score - 18.0)
        selected = [item for item in ranked if float(item.score) >= floor][:5]

    choices = []
    for item in selected:
        candidate = candidate_by_id.get(item.client_id)
        if candidate is None:
            continue
        choices.append(
            {
                "client_id": str(candidate.client_id),
                "label": candidate.display_name,
                "score": round(float(item.score), 1),
                "method": item.method.value,
                "evidence": item.evidence,
            }
        )

    return {
        "message_id": str(message.id),
        "external_message_id": message.external_message_id,
        "reason": message.error_message,
        "query": identity,
        "normalized_query": canonical_search_text(identity),
        "choices": choices,
    }


@router.get(
    "/incoming-messages",
    response_model=list[IncomingMessageResponse],
)
async def list_incoming_messages(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[IncomingMessageService, Depends(get_incoming_message_service)],
    message_status: Annotated[MessageStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IncomingMessageResponse]:
    messages = await service.list_messages(
        session,
        status=message_status,
        limit=limit,
        offset=offset,
    )
    return [IncomingMessageResponse.model_validate(message) for message in messages]


@router.post(
    "/incoming-message/{message_id}/resolve",
    response_model=IncomingMessageResponse,
)
async def resolve_incoming_message(
    message_id: UUID,
    payload: ManualResolveRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[IncomingMessageService, Depends(get_incoming_message_service)],
) -> IncomingMessageResponse:
    try:
        message = await service.resolve_manually(
            session,
            message_id=message_id,
            client_id=payload.client_id,
        )
    except IncomingMessageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ActiveClientNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InvalidMessageStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return IncomingMessageResponse.model_validate(message)
