from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.api.deps import get_incoming_message_service
from app.core.config import settings
from app.core.enums import MessageSource
from app.db.session import AsyncSessionFactory
from app.integrations.whatsapp.meta import (
    parse_meta_whatsapp_text_messages,
    verify_meta_signature,
)
from app.schemas.incoming_message import IncomingMessageCreate
from app.services.incoming_message_service import IncomingMessageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp-webhook"])


@router.get("", response_class=PlainTextResponse)
async def verify_whatsapp_webhook(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> PlainTextResponse:
    if not settings.whatsapp_verify_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WHATSAPP_VERIFY_TOKEN is not configured",
        )

    if (
        hub_mode == "subscribe"
        and hub_verify_token == settings.whatsapp_verify_token
        and hub_challenge is not None
    ):
        return PlainTextResponse(hub_challenge, status_code=status.HTTP_200_OK)

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="webhook verification failed")


async def _process_whatsapp_message(
    service: IncomingMessageService,
    payload: IncomingMessageCreate,
) -> None:
    try:
        async with AsyncSessionFactory() as session:
            await service.process(session, payload)
    except Exception:
        # The webhook has already been acknowledged. Keep the worker exception
        # visible in application logs without causing Meta to retry the request.
        logger.exception(
            "WhatsApp background processing failed for external_message_id=%s",
            payload.external_message_id,
        )


@router.post("")
async def receive_whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    service: Annotated[IncomingMessageService, Depends(get_incoming_message_service)],
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> dict[str, Any]:
    raw_body = await request.body()

    if settings.whatsapp_app_secret:
        if not verify_meta_signature(
            body=raw_body,
            signature=x_hub_signature_256,
            app_secret=settings.whatsapp_app_secret,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid WhatsApp webhook signature",
            )

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid JSON payload",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid webhook payload",
        )

    messages = parse_meta_whatsapp_text_messages(payload)

    for item in messages:
        incoming = IncomingMessageCreate(
            source=MessageSource.WHATSAPP,
            external_message_id=item.external_message_id,
            sender_id=item.sender_id,
            sender_name=item.sender_name,
            text=item.text,
        )
        background_tasks.add_task(_process_whatsapp_message, service, incoming)

    # Acknowledge immediately. 1C synchronization can take minutes and must not
    # hold the Meta webhook request open.
    return {
        "status": "accepted",
        "text_messages": len(messages),
    }
