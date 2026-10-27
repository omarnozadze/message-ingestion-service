from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import delete, or_, select, text

from app.core.config import settings
from app.db.session import AsyncSessionFactory, engine
from app.integrations.one_c.odata import OneCEntityNotFoundError, OneCODataClient
from app.models.client import Client
from app.models.client_alias import ClientAlias
from app.models.sync_state import SyncState
from app.services.client_exclusions import is_excluded_client_name
from app.services.client_duplicates import detect_duplicate_clients, duplicate_client_count
from app.services.matching.candidate_cache import match_candidate_cache
from app.services.matching.normalizer import normalize_text
from app.services.plan_aliases import extract_plan_address_aliases, make_address_alias

logger = logging.getLogger(__name__)

SYNC_STATE_NAME = "one_c_search_index"
CONTACT_ENTITY = "Catalog_Партнеры_КонтактнаяИнформация"
ZERO_GUID = "00000000-0000-0000-0000-000000000000"
SYNC_ADVISORY_LOCK_KEY = 714_031_101


@dataclass(frozen=True)
class OneCSyncReport:
    mode: str
    partners_fetched: int
    clients_created: int
    clients_updated: int
    clients_deactivated: int
    clients_excluded: int
    duplicate_groups: int
    duplicate_clients: int
    duplicate_examples: tuple[dict[str, object], ...]
    address_aliases_created: int
    plan_aliases_created: int
    plan_rows_seen: int
    started_at: datetime
    finished_at: datetime


def _configured_agent_codes() -> list[str]:
    codes: set[str] = set()
    codes.update(str(value).strip() for value in settings.agent_sender_map.values())
    for values in settings.agent_multi_sender_map.values():
        codes.update(str(value).strip() for value in values)
    return sorted(code for code in codes if code)


def _nonzero_guid(value: object) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and text != ZERO_GUID)


def _looks_like_address(row: dict) -> bool:
    representation = str(row.get("Представление") or "").strip()
    if not representation:
        return False
    if str(row.get("НомерТелефона") or "").strip():
        return False
    if str(row.get("НомерТелефонаБезКодов") or "").strip():
        return False
    if str(row.get("АдресЭП") or "").strip():
        return False
    if str(row.get("ДоменноеИмяСервера") or "").strip():
        return False
    if "@" in representation:
        return False

    type_text = normalize_text(str(row.get("Тип") or ""))
    if "адрес" in type_text or "address" in type_text:
        return True
    return bool(
        str(row.get("Страна") or "").strip()
        or str(row.get("Регион") or "").strip()
        or _nonzero_guid(row.get("Город_Key"))
        or _nonzero_guid(row.get("Улица_Key"))
    )


class OneCSearchIndexSync:
    def __init__(self) -> None:
        self.timezone = ZoneInfo(settings.business_timezone)
        self.lock = asyncio.Lock()
        self.one_c = OneCODataClient(
            base_url=settings.one_c_base_url,
            username=settings.one_c_username,
            password=settings.one_c_password,
            timeout_seconds=max(settings.one_c_timeout_seconds, 60.0),
        )

    async def run_once(self, *, force_full: bool = False) -> OneCSyncReport | None:
        async with self.lock:
            # Process-local lock above avoids duplicate work inside one worker.
            # PostgreSQL advisory lock below protects multiple workers/instances.
            if engine.dialect.name == "postgresql":
                async with engine.connect() as lock_connection:
                    acquired = bool(
                        await lock_connection.scalar(
                            text("SELECT pg_try_advisory_lock(:key)"),
                            {"key": SYNC_ADVISORY_LOCK_KEY},
                        )
                    )
                    if not acquired:
                        logger.info("1C search-index sync skipped: another instance holds the lock")
                        return None
                    try:
                        return await self._run_once_locked(force_full=force_full)
                    finally:
                        try:
                            await lock_connection.execute(
                                text("SELECT pg_advisory_unlock(:key)"),
                                {"key": SYNC_ADVISORY_LOCK_KEY},
                            )
                        except Exception:
                            logger.exception("failed to release 1C sync advisory lock")
            return await self._run_once_locked(force_full=force_full)

    async def _run_once_locked(self, *, force_full: bool = False) -> OneCSyncReport:
        started_at = datetime.now(self.timezone)
        state = await self._load_state()
        full = force_full or self._full_sync_due(state, started_at)

        if full:
            modified_since = None
            mode = "full"
        else:
            cursor = state.cursor_at if state and state.cursor_at else started_at
            if cursor.tzinfo is None:
                cursor = cursor.replace(tzinfo=self.timezone)
            modified_since = cursor.astimezone(self.timezone) - timedelta(
                minutes=max(0, settings.one_c_sync_overlap_minutes)
            )
            mode = "incremental"

        logger.info(
            "1C %s search-index sync starting; modified_since=%s",
            mode,
            modified_since,
        )

        try:
            partners = await self.one_c.list_partners(modified_since=modified_since)
            changed_refs, created, updated, deactivated, excluded = await self._sync_clients(
                partners,
                full=full,
            )
            address_created = await self._sync_address_aliases(
                None if full else changed_refs
            )
            plan_created, plan_rows_seen = await self._sync_plan_aliases()
            duplicate_groups = await self._detect_duplicates()

            finished_at = datetime.now(self.timezone)
            report = OneCSyncReport(
                mode=mode,
                partners_fetched=len(partners),
                clients_created=created,
                clients_updated=updated,
                clients_deactivated=deactivated,
                clients_excluded=excluded,
                duplicate_groups=len(duplicate_groups),
                duplicate_clients=duplicate_client_count(duplicate_groups),
                duplicate_examples=tuple(
                    {
                        "kind": group.kind,
                        "key": group.key,
                        "client_ids": list(group.client_ids),
                        "display_names": list(group.display_names),
                    }
                    for group in duplicate_groups[:10]
                ),
                address_aliases_created=address_created,
                plan_aliases_created=plan_created,
                plan_rows_seen=plan_rows_seen,
                started_at=started_at,
                finished_at=finished_at,
            )
            await self._mark_success(
                state,
                finished_at=finished_at,
                full=full,
                report=report,
            )
            await match_candidate_cache.invalidate()
            logger.info("1C search-index sync complete: %s", report)
            return report
        except Exception as exc:
            await self._mark_failure(state, exc)
            logger.exception("1C search-index sync failed")
            raise

    async def run_scheduler(self) -> None:
        delay = max(0, settings.one_c_sync_startup_delay_seconds)
        if delay:
            await asyncio.sleep(delay)

        interval_seconds = max(1, settings.one_c_sync_interval_minutes) * 60
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Error is persisted/logged by run_once. Keep the scheduler alive.
                pass
            await asyncio.sleep(interval_seconds)

    def _full_sync_due(self, state: SyncState | None, now: datetime) -> bool:
        if state is None or state.cursor_at is None or state.last_full_at is None:
            return True
        last_full = state.last_full_at
        if last_full.tzinfo is None:
            last_full = last_full.replace(tzinfo=self.timezone)
        last_full_local = last_full.astimezone(self.timezone)
        return (
            now.hour >= settings.one_c_full_sync_hour
            and last_full_local.date() < now.date()
        )

    async def _load_state(self) -> SyncState | None:
        async with AsyncSessionFactory() as session:
            return await session.get(SyncState, SYNC_STATE_NAME)

    async def _mark_success(
        self,
        state: SyncState | None,
        *,
        finished_at: datetime,
        full: bool,
        report: OneCSyncReport,
    ) -> None:
        async with AsyncSessionFactory() as session:
            current = await session.get(SyncState, SYNC_STATE_NAME)
            if current is None:
                current = SyncState(name=SYNC_STATE_NAME)
                session.add(current)
            current.cursor_at = finished_at
            current.last_success_at = finished_at
            current.last_error = None
            current.last_report_json = json.dumps(
                {
                    "mode": report.mode,
                    "partners_fetched": report.partners_fetched,
                    "clients_created": report.clients_created,
                    "clients_updated": report.clients_updated,
                    "clients_deactivated": report.clients_deactivated,
                    "clients_excluded": report.clients_excluded,
                    "duplicate_groups": report.duplicate_groups,
                    "duplicate_clients": report.duplicate_clients,
                    "duplicate_examples": list(report.duplicate_examples),
                    "address_aliases_created": report.address_aliases_created,
                    "plan_aliases_created": report.plan_aliases_created,
                    "plan_rows_seen": report.plan_rows_seen,
                    "started_at": report.started_at.isoformat(),
                    "finished_at": report.finished_at.isoformat(),
                },
                ensure_ascii=False,
            )
            if full:
                current.last_full_at = finished_at
            else:
                current.last_incremental_at = finished_at
            await session.commit()

    async def _mark_failure(self, state: SyncState | None, exc: Exception) -> None:
        try:
            async with AsyncSessionFactory() as session:
                current = await session.get(SyncState, SYNC_STATE_NAME)
                if current is None:
                    current = SyncState(name=SYNC_STATE_NAME)
                    session.add(current)
                current.last_error = f"{type(exc).__name__}: {exc}"[:4000]
                await session.commit()
        except Exception:
            logger.exception("failed to persist 1C sync failure state")

    async def _sync_clients(
        self,
        partners: list[dict],
        *,
        full: bool,
    ) -> tuple[set[str], int, int, int, int]:
        created = 0
        updated = 0
        deactivated = 0
        excluded = 0
        changed_refs: set[str] = set()

        async with AsyncSessionFactory() as session:
            local_clients = (await session.scalars(select(Client))).all()
            by_ref = {
                str(item.one_c_ref).strip().lower(): item
                for item in local_clients
                if item.one_c_ref
            }
            by_code = {
                str(item.code).strip(): item
                for item in local_clients
                if item.code
            }
            remote_active_refs: set[str] = set()
            aliases_to_delete: set[object] = set()

            # Persistent local cleanup: excluded chains are not searchable even
            # before the next full sync, and their aliases are removed.
            for client_obj in local_clients:
                if is_excluded_client_name(client_obj.display_name):
                    if client_obj.is_active:
                        client_obj.is_active = False
                        deactivated += 1
                    aliases_to_delete.add(client_obj.id)

            for item in partners:
                ref_key = str(item.get("Ref_Key") or "").strip()
                if not ref_key:
                    continue

                ref_norm = ref_key.lower()
                code = (item.get("Code") or "").strip() or None
                display_name = (
                    (item.get("Description") or "").strip()
                    or (item.get("НаименованиеПолное") or "").strip()
                    or code
                    or ref_key
                )

                client_obj = by_ref.get(ref_norm)
                if client_obj is None and code:
                    client_obj = by_code.get(code)

                # These chains are intentionally outside this application's
                # search/index scope. Keep an existing row inactive for audit
                # history, remove aliases, and never import/reactivate it.
                if is_excluded_client_name(display_name):
                    excluded += 1
                    if client_obj is not None:
                        if client_obj.is_active:
                            client_obj.is_active = False
                            deactivated += 1
                        aliases_to_delete.add(client_obj.id)
                    continue

                remote_active_refs.add(ref_norm)
                changed_refs.add(ref_norm)

                if client_obj is None:
                    client_obj = Client(
                        one_c_ref=ref_key,
                        code=code,
                        display_name=display_name,
                        normalized_name=normalize_text(display_name),
                        is_active=True,
                    )
                    session.add(client_obj)
                    by_ref[ref_norm] = client_obj
                    if code:
                        by_code[code] = client_obj
                    created += 1
                else:
                    client_obj.one_c_ref = ref_key
                    client_obj.code = code
                    client_obj.display_name = display_name
                    client_obj.normalized_name = normalize_text(display_name)
                    client_obj.is_active = True
                    updated += 1

            if aliases_to_delete:
                await session.execute(
                    delete(ClientAlias).where(ClientAlias.client_id.in_(aliases_to_delete))
                )

            if full:
                for ref_norm, client_obj in by_ref.items():
                    if (
                        client_obj.is_active
                        and ref_norm not in remote_active_refs
                    ):
                        client_obj.is_active = False
                        deactivated += 1

            await session.commit()

        return changed_refs, created, updated, deactivated, excluded

    async def _detect_duplicates(self):
        async with AsyncSessionFactory() as session:
            clients = (
                await session.scalars(
                    select(Client).where(Client.is_active.is_(True))
                )
            ).all()
        return detect_duplicate_clients(clients)

    async def _sync_address_aliases(self, partner_refs: set[str] | None) -> int:
        if partner_refs is not None and not partner_refs:
            return 0

        async with httpx.AsyncClient(
            auth=self.one_c.auth,
            timeout=self.one_c.timeout,
            headers={"Accept": "application/json"},
        ) as http_client:
            rows = await self._load_contact_rows(http_client, partner_refs)

        address_rows = [row for row in rows if _looks_like_address(row)]
        if not address_rows:
            return 0

        refs = {
            str(row.get("Ref_Key") or "").strip().lower()
            for row in address_rows
            if row.get("Ref_Key")
        }
        created = 0
        async with AsyncSessionFactory() as session:
            clients = (
                await session.scalars(
                    select(Client).where(
                        Client.is_active.is_(True),
                        Client.one_c_ref.is_not(None),
                    )
                )
            ).all()
            by_ref = {
                str(item.one_c_ref).strip().lower(): item
                for item in clients
                if item.one_c_ref and str(item.one_c_ref).strip().lower() in refs
            }
            if not by_ref:
                return 0

            existing_pairs = set(
                (str(client_id), normalized_alias)
                for client_id, normalized_alias in (
                    await session.execute(
                        select(ClientAlias.client_id, ClientAlias.normalized_alias).where(
                            ClientAlias.client_id.in_([item.id for item in by_ref.values()])
                        )
                    )
                ).all()
            )

            for row in address_rows:
                ref_norm = str(row.get("Ref_Key") or "").strip().lower()
                client_obj = by_ref.get(ref_norm)
                if client_obj is None:
                    continue
                representation = str(row.get("Представление") or "").strip()
                alias = make_address_alias(f"{client_obj.display_name} {representation}")
                if not alias:
                    continue
                normalized_alias = normalize_text(alias)
                key = (str(client_obj.id), normalized_alias)
                if key in existing_pairs:
                    continue
                session.add(
                    ClientAlias(
                        client_id=client_obj.id,
                        alias=alias,
                        normalized_alias=normalized_alias,
                    )
                )
                existing_pairs.add(key)
                created += 1

            await session.commit()
        return created

    async def _load_contact_rows(
        self,
        http_client: httpx.AsyncClient,
        partner_refs: set[str] | None,
    ) -> list[dict]:
        if partner_refs is None:
            return await self._load_contact_page_set(http_client, filter_text=None)

        refs = sorted(partner_refs)
        rows: list[dict] = []
        batch_size = 25
        for offset in range(0, len(refs), batch_size):
            batch = refs[offset : offset + batch_size]
            filter_text = " or ".join(
                f"Ref_Key eq guid'{ref}'" for ref in batch
            )
            rows.extend(
                await self._load_contact_page_set(
                    http_client,
                    filter_text=f"({filter_text})",
                )
            )
        return rows

    async def _load_contact_page_set(
        self,
        http_client: httpx.AsyncClient,
        *,
        filter_text: str | None,
    ) -> list[dict]:
        rows: list[dict] = []
        skip = 0
        page_size = 500
        while True:
            params = {
                "$format": "json",
                "$select": (
                    "Ref_Key,LineNumber,Тип,Представление,ЗначенияПолей,"
                    "Страна,Регион,Город_Key,Улица_Key,АдресЭП,"
                    "ДоменноеИмяСервера,НомерТелефона,НомерТелефонаБезКодов"
                ),
                "$orderby": "Ref_Key,LineNumber",
                "$top": str(page_size),
                "$skip": str(skip),
            }
            if filter_text:
                params["$filter"] = filter_text
            page = await self.one_c._get_value(
                http_client,
                CONTACT_ENTITY,
                params=params,
            )
            if not page:
                break
            rows.extend(page)
            skip += len(page)
            if len(page) < page_size:
                break
        return rows

    async def _sync_plan_aliases(self) -> tuple[int, int]:
        agent_codes = _configured_agent_codes()
        if not agent_codes:
            return 0, 0

        business_date = datetime.now(self.timezone).date()
        created = 0
        rows_seen = 0

        async with httpx.AsyncClient(
            auth=self.one_c.auth,
            timeout=self.one_c.timeout,
            headers={"Accept": "application/json"},
        ) as http_client, AsyncSessionFactory() as session:
            clients = (
                await session.scalars(
                    select(Client).where(
                        Client.is_active.is_(True),
                        Client.one_c_ref.is_not(None),
                    )
                )
            ).all()
            by_ref = {
                str(item.one_c_ref).strip().lower(): item
                for item in clients
                if item.one_c_ref
            }
            existing_pairs = set(
                (str(client_id), normalized_alias)
                for client_id, normalized_alias in (
                    await session.execute(
                        select(ClientAlias.client_id, ClientAlias.normalized_alias)
                    )
                ).all()
            )

            for agent_code in agent_codes:
                agent_description = f"{settings.agent_description_prefix}{agent_code}"
                try:
                    agent_ref = await self.one_c._resolve_agent_ref(
                        http_client,
                        agent_description,
                    )
                    plan = await self.one_c._find_active_plan(
                        http_client,
                        agent_ref,
                        business_date,
                    )
                except OneCEntityNotFoundError as exc:
                    logger.warning("plan alias sync skipped %s: %s", agent_description, exc)
                    continue

                rows = await self._load_plan_rows(http_client, plan.ref_key)
                rows_seen += len(rows)
                for row in rows:
                    partner_ref = str(row.get("Партнер_Key") or "").strip().lower()
                    client_obj = by_ref.get(partner_ref)
                    if client_obj is None:
                        continue
                    for alias in extract_plan_address_aliases(row.get("Адрес")):
                        normalized_alias = normalize_text(alias)
                        key = (str(client_obj.id), normalized_alias)
                        if key in existing_pairs:
                            continue
                        session.add(
                            ClientAlias(
                                client_id=client_obj.id,
                                alias=alias,
                                normalized_alias=normalized_alias,
                            )
                        )
                        existing_pairs.add(key)
                        created += 1

            await session.commit()

        return created, rows_seen

    async def _load_plan_rows(
        self,
        http_client: httpx.AsyncClient,
        plan_ref: str,
    ) -> list[dict]:
        rows: list[dict] = []
        skip = 0
        page_size = 500
        while True:
            page = await self.one_c._get_value(
                http_client,
                self.one_c.PLAN_ROWS_ENTITY,
                params={
                    "$format": "json",
                    "$filter": f"Ref_Key eq guid'{plan_ref}'",
                    "$select": "LineNumber,Партнер_Key,Адрес",
                    "$orderby": "LineNumber",
                    "$top": str(page_size),
                    "$skip": str(skip),
                },
            )
            if not page:
                break
            rows.extend(page)
            skip += len(page)
            if len(page) < page_size:
                break
        return rows
