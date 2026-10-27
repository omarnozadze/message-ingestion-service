import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request

from app.core.config import settings
from app.db.session import AsyncSessionFactory, engine
from app.models.sync_state import SyncState
from app.services.one_c_sync import SYNC_STATE_NAME

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/one-c-sync")
async def one_c_sync_health(request: Request) -> dict[str, object]:
    async with AsyncSessionFactory() as session:
        state = await session.get(SyncState, SYNC_STATE_NAME)

    enabled = bool(
        settings.one_c_auto_sync_enabled
        and settings.one_c_mode.lower() == "odata"
        and settings.one_c_username
        and settings.one_c_password
    )
    scheduler_running = getattr(request.app.state, "one_c_sync", None) is not None
    interval_seconds = max(1, settings.one_c_sync_interval_minutes) * 60
    stale_after_seconds = max(interval_seconds * 3, 3600)
    now = datetime.now(ZoneInfo(settings.business_timezone))

    if state is None:
        return {
            "status": "not_initialized" if enabled else "disabled",
            "auto_sync_enabled": enabled,
            "scheduler_running": scheduler_running,
            "distributed_lock": "postgresql_advisory" if engine.dialect.name == "postgresql" else "process_only",
            "interval_minutes": settings.one_c_sync_interval_minutes,
            "stale_after_seconds": stale_after_seconds,
            "last_success_at": None,
            "last_full_at": None,
            "last_incremental_at": None,
            "last_error": None,
            "seconds_since_success": None,
            "next_expected_at": None,
            "last_report": None,
        }

    last_success = state.last_success_at
    if last_success is not None and last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=ZoneInfo(settings.business_timezone))
    seconds_since_success = (now - last_success.astimezone(ZoneInfo(settings.business_timezone))).total_seconds() if last_success else None
    next_expected_at = last_success + timedelta(seconds=interval_seconds) if last_success else None

    last_report = None
    if state.last_report_json:
        try:
            last_report = json.loads(state.last_report_json)
        except (TypeError, ValueError):
            last_report = {"parse_error": True}

    if not enabled:
        status = "disabled"
    elif state.last_error:
        status = "error"
    elif seconds_since_success is None:
        status = "not_initialized"
    elif seconds_since_success > stale_after_seconds:
        status = "stale"
    else:
        status = "ok"

    return {
        "status": status,
        "auto_sync_enabled": enabled,
        "scheduler_running": scheduler_running,
        "distributed_lock": "postgresql_advisory" if engine.dialect.name == "postgresql" else "process_only",
        "interval_minutes": settings.one_c_sync_interval_minutes,
        "stale_after_seconds": stale_after_seconds,
        "cursor_at": state.cursor_at,
        "last_success_at": state.last_success_at,
        "last_full_at": state.last_full_at,
        "last_incremental_at": state.last_incremental_at,
        "last_error": state.last_error,
        "seconds_since_success": seconds_since_success,
        "next_expected_at": next_expected_at,
        "last_report": last_report,
    }
