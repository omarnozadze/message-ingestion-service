from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.incoming_messages import router as incoming_messages_router
from app.api.routes.whatsapp_webhook import router as whatsapp_webhook_router
from app.core.config import settings
from sqlalchemy import update

from app.core.enums import MessageStatus
from app.db.session import AsyncSessionFactory, engine
from app.models.incoming_message import IncomingMessage
from app.services.one_c_sync import OneCSearchIndexSync

logger = logging.getLogger(__name__)


async def _recover_orphaned_pending_messages() -> int:
    # A SYNC_PENDING row cannot still have an owning task after this API process
    # has restarted. Mark it for review instead of leaving the bridge polling forever.
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            update(IncomingMessage)
            .where(IncomingMessage.status == MessageStatus.SYNC_PENDING)
            .values(
                status=MessageStatus.NEEDS_REVIEW,
                error_message=(
                    "processing was interrupted by an API restart; "
                    "verify 1C state and resend if needed"
                ),
            )
        )
        await session.commit()
        return int(result.rowcount or 0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    sync_task: asyncio.Task | None = None

    recovered = await _recover_orphaned_pending_messages()
    if recovered:
        logger.warning("recovered %s orphaned SYNC_PENDING messages from a previous API process", recovered)

    if (
        settings.one_c_auto_sync_enabled
        and settings.one_c_mode.lower() == "odata"
        and settings.one_c_username
        and settings.one_c_password
    ):
        scheduler = OneCSearchIndexSync()
        app.state.one_c_sync = scheduler
        sync_task = asyncio.create_task(
            scheduler.run_scheduler(),
            name="one-c-search-index-sync",
        )
        logger.info(
            "1C auto sync enabled: every %s minutes, nightly full at %02d:00 %s",
            settings.one_c_sync_interval_minutes,
            settings.one_c_full_sync_hour,
            settings.business_timezone,
        )
    else:
        logger.info("1C auto sync disabled or OData credentials are not configured")

    try:
        yield
    finally:
        if sync_task is not None:
            sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await sync_task
        await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Message ingestion, client matching and 1C synchronization gateway.",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(incoming_messages_router, prefix="/api/v1")
app.include_router(whatsapp_webhook_router)
