from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID

import httpx

from app.integrations.one_c.base import OneCSyncResult, OneCVisitChange
from app.services.visit_instruction import VisitInstruction, VisitOperation

ZERO_GUID = "00000000-0000-0000-0000-000000000000"
BUILD_MARKER = "one-c-stable-system-v41"

# Row reads: keyed GETs with bounded concurrency. The wide collection query
# takes ~180 s on this publication, 18 parallel keyed GETs take seconds.
FULL_ROW_CONCURRENCY = 18

# Parent-document PATCH/Post timeouts. Large plans (> LARGE_PLAN_ROWS) need
# longer: aborting the HTTP request early can cancel the server-side write.
LARGE_PLAN_ROWS = 300
PARENT_PATCH_TIMEOUT_SECONDS = 180.0
LARGE_PLAN_PATCH_TIMEOUT_SECONDS = 360.0
POST_TIMEOUT_SECONDS = 180.0
LARGE_PLAN_POST_TIMEOUT_SECONDS = 300.0
VERIFY_TIMEOUT_SECONDS = 45.0

def _timing_log(event: str, **fields: Any) -> None:
    """Emit diagnostics without ever crashing on Windows console encodings.

    Scheduled Tasks launched through Windows PowerShell can expose a legacy
    stdout encoding. 1C field names are Cyrillic (for example "Д1"), so a
    plain print() may raise UnicodeEncodeError and abort the sync itself.
    Pre-escape only characters unsupported by the active stdout encoding.
    """
    stamp = datetime.now().isoformat(timespec="seconds")
    parts = [f"[1C-TIMING] {stamp}", f"build={BUILD_MARKER}", f"event={event}"]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    line = " ".join(parts)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        safe_line = line.encode(encoding, errors="backslashreplace").decode(
            encoding, errors="replace"
        )
    except LookupError:
        safe_line = line.encode("ascii", errors="backslashreplace").decode("ascii")
    print(safe_line, flush=True)

# One in-process lock per 1C agent. The WhatsApp bridge already serializes
# messages per sender, but the backend lock is the authoritative protection:
# the same agent may be reached through different senders or API clients.
_AGENT_PLAN_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


# 1C performs these large parent-document PATCH/Post operations slowly. Running
# several of them concurrently (even for different agents) caused requests to sit
# in SYNC_PENDING for many minutes. Serialize all plan mutations in-process.
_GLOBAL_PLAN_WRITE_LOCKS: dict[int, asyncio.Lock] = {}
_GLOBAL_PLAN_READ_SEMAPHORES: dict[int, asyncio.Semaphore] = {}


def _global_plan_read_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    key = id(loop)
    semaphore = _GLOBAL_PLAN_READ_SEMAPHORES.get(key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(2)
        _GLOBAL_PLAN_READ_SEMAPHORES[key] = semaphore
    return semaphore


def _global_plan_write_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = id(loop)
    lock = _GLOBAL_PLAN_WRITE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _GLOBAL_PLAN_WRITE_LOCKS[key] = lock
    return lock


def _agent_plan_lock(agent_ref: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = (id(loop), agent_ref)
    lock = _AGENT_PLAN_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _AGENT_PLAN_LOCKS[key] = lock
    return lock


def _is_retryable_plan_conflict(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return (
        "document changed while visit-plan rows were being read" in text
        or "document changed after it was read" in text
    )


class OneCError(RuntimeError):
    pass


class OneCEntityNotFoundError(OneCError):
    pass


class OneCPlanReviewRequired(OneCError):
    pass


@dataclass(frozen=True, slots=True)
class AgentPlan:
    ref_key: str
    number: str | None
    date: datetime
    data_version: str | None
    posted: bool
    cyclic: bool
    cyclic_end_date: datetime | None



class OneCODataClient:
    EXCHANGE_PLAN_ENTITY = "ExchangePlan_апМобильноеПриложениеТорговыйПредставитель"
    PLAN_ENTITY = "Document_апПланАгента"
    PLAN_ROWS_ENTITY = "Document_апПланАгента_ПланПосещений"

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = httpx.BasicAuth(username, password)
        self.timeout = httpx.Timeout(timeout_seconds)

    async def get_active_plan_partner_refs(
        self,
        *,
        agent_description: str,
        business_date: date,
    ) -> set[str]:
        """Return Partner Ref_Keys from the agent's current active plan.

        This uses only the light tabular-row index (Partner_Key + LineNumber),
        so it is fast enough to use as matching context for each message.
        """
        async with httpx.AsyncClient(
            auth=self.auth,
            timeout=self.timeout,
            headers={"Accept": "application/json"},
        ) as client:
            agent_ref = await self._resolve_agent_ref(client, agent_description)
            plan = await self._find_active_plan(client, agent_ref, business_date)
            rows = await self._get_all_plan_row_keys(client, plan.ref_key)
            return {
                str(row.get("Партнер_Key") or "").strip().lower()
                for row in rows
                if str(row.get("Партнер_Key") or "").strip()
            }

    async def apply_visit_instruction(
        self,
        *,
        partner_ref: str,
        business_date: date,
        source_message_id: UUID,
        raw_text: str,
        instruction: VisitInstruction,
    ) -> OneCSyncResult:
        return await self.apply_visit_instructions(
            changes=(
                OneCVisitChange(
                    partner_ref=partner_ref,
                    instruction=instruction,
                ),
            ),
            business_date=business_date,
            source_message_id=source_message_id,
            raw_text=raw_text,
        )

    async def apply_visit_instructions(
        self,
        *,
        changes: tuple[OneCVisitChange, ...],
        business_date: date,
        source_message_id: UUID,
        raw_text: str,
    ) -> OneCSyncResult:
        del source_message_id, raw_text
        workflow_started = time.perf_counter()
        _timing_log(
            "workflow_start",
            change_count=len(changes),
            business_date=business_date.isoformat(),
        )

        if not changes:
            raise OneCPlanReviewRequired("no visit-plan changes were supplied")

        agent_descriptions = {change.instruction.agent_description for change in changes}
        if len(agent_descriptions) != 1:
            raise OneCPlanReviewRequired(
                "all visit-plan changes in one message must target the same agent"
            )
        agent_description = next(iter(agent_descriptions))

        stage = "connecting to 1C"
        try:
            async with httpx.AsyncClient(
                auth=self.auth,
                timeout=self.timeout,
                headers={"Accept": "application/json"},
            ) as client:
                stage = f"resolving agent {agent_description}"
                agent_started = time.perf_counter()
                agent_ref = await self._resolve_agent_ref(client, agent_description)
                _timing_log(
                    "agent_resolved",
                    agent=agent_description,
                    seconds=round(time.perf_counter() - agent_started, 3),
                )

                # One workflow at a time for the same agent. Different agents may
                # prepare concurrently; the expensive parent PATCH/Post itself is
                # serialized globally below.
                async with _agent_plan_lock(agent_ref):
                    for attempt in range(2):
                        try:
                            stage = "finding requesting-agent plan"
                            plan_started = time.perf_counter()
                            plan = await self._find_active_plan(
                                client, agent_ref, business_date
                            )
                            _timing_log(
                                "plan_found",
                                plan=plan.number or plan.ref_key,
                                seconds=round(time.perf_counter() - plan_started, 3),
                                posted=plan.posted,
                            )

                            # v38: production verification proved that this 1C
                            # publication accepts child-row PATCH with HTTP 200 but
                            # does not persist D1..D7. Skip that no-op path entirely
                            # and use the supported parent-document workflow.
                            _timing_log(
                                "direct_row_skipped",
                                reason="publication_does_not_persist_child_patch",
                            )

                            stage = (
                                "loading full visit-plan rows for document "
                                f"{plan.number or plan.ref_key}"
                            )
                            row_load_started = time.perf_counter()
                            rows = await self._get_plan_rows(client, plan.ref_key)
                            row_load_seconds = round(
                                time.perf_counter() - row_load_started, 3
                            )
                            _timing_log(
                                "rows_loaded",
                                plan=plan.number or plan.ref_key,
                                rows=len(rows),
                                seconds=row_load_seconds,
                            )
                            write_lock_wait_seconds = 0.0
                            write_seconds = 0.0
                            write_timings: dict[str, Any] = {}

                            row_results: list[dict[str, Any]] = []
                            any_changed = False
                            stage = "applying requested visit-plan changes in memory"
                            for change in changes:
                                changed, row_info = self._apply_instruction_to_rows(
                                    rows,
                                    partner_ref=change.partner_ref,
                                    instruction=change.instruction,
                                    plan_ref=plan.ref_key,
                                    plan_date=plan.date,
                                )
                                if (
                                    change.instruction.operation
                                    in {VisitOperation.REMOVE, VisitOperation.REMOVE_ALL}
                                    and row_info.get("missing_from_plan")
                                ):
                                    label = change.client_label or change.partner_ref
                                    raise OneCPlanReviewRequired(
                                        "client to remove is not present in active "
                                        f"agent plan: {label}"
                                    )
                                if row_info.get("precondition_error"):
                                    label = change.client_label or change.partner_ref
                                    raise OneCPlanReviewRequired(
                                        f"{row_info['precondition_error']}: {label}"
                                    )
                                any_changed = any_changed or changed
                                row_results.append(
                                    {
                                        "partner_ref": change.partner_ref,
                                        "client_label": change.client_label,
                                        "operation": change.instruction.operation.value,
                                        "target_fields": [
                                            _weekday_field(day, plan.date)
                                            for day in change.instruction.target_days
                                        ],
                                        "changed": changed,
                                        "row": row_info,
                                    }
                                )

                            if any_changed:
                                stage = (
                                    "waiting for 1C write slot / PATCHing visit-plan document "
                                    f"{plan.number or plan.ref_key}"
                                )
                                # Only the mutation is global-serial. Do not hold this
                                # lock during the 30-60s row read; that created long
                                # head-of-line blocking across unrelated agents.
                                write_wait_started = time.perf_counter()
                                _timing_log(
                                    "write_lock_wait_start",
                                    plan=plan.number or plan.ref_key,
                                )
                                async with _global_plan_write_lock():
                                    write_lock_wait_seconds = round(
                                        time.perf_counter() - write_wait_started, 3
                                    )
                                    _timing_log(
                                        "write_lock_acquired",
                                        plan=plan.number or plan.ref_key,
                                        seconds=write_lock_wait_seconds,
                                    )
                                    write_started = time.perf_counter()
                                    _timing_log(
                                        "write_batch_start",
                                        plan=plan.number or plan.ref_key,
                                        rows=len(rows),
                                    )
                                    write_timings = await self._patch_plan_rows_batch(
                                        client,
                                        plan,
                                        rows,
                                        changes=changes,
                                    )
                                    write_seconds = round(
                                        time.perf_counter() - write_started, 3
                                    )
                                    _timing_log(
                                        "write_batch_done",
                                        plan=plan.number or plan.ref_key,
                                        seconds=write_seconds,
                                        patch=write_timings.get("patch"),
                                        post=write_timings.get("post"),
                                        final_verify=write_timings.get("final_verify"),
                                    )

                            _timing_log(
                                "workflow_done",
                                plan=plan.number or plan.ref_key,
                                changed=any_changed,
                                total_seconds=round(time.perf_counter() - workflow_started, 3),
                                row_load=row_load_seconds,
                                write_lock_wait=write_lock_wait_seconds,
                                write=write_seconds,
                            )
                            return OneCSyncResult(
                                record_ref=plan.ref_key,
                                details={
                                    "plan_number": plan.number,
                                    "agent_ref": agent_ref,
                                    "agent": agent_description,
                                    "changes": row_results,
                                    "changed": any_changed,
                                    "rows_loaded": len(rows),
                                    "batch": len(changes) > 1,
                                    "conflict_retry_count": attempt,
                                    "timings_seconds": {
                                        "row_load": row_load_seconds,
                                        "write_lock_wait": write_lock_wait_seconds,
                                        "write": write_seconds,
                                        **(write_timings if any_changed else {}),
                                    },
                                },
                            )
                        except OneCPlanReviewRequired as exc:
                            if attempt == 0 and _is_retryable_plan_conflict(exc):
                                await asyncio.sleep(0.25)
                                continue
                            raise

                raise OneCPlanReviewRequired(
                    "1C plan conflict persisted after one automatic retry"
                )
        except httpx.TimeoutException as exc:
            raise OneCError(
                f"1C timeout while {stage}: {type(exc).__name__}"
            ) from exc
        except httpx.RequestError as exc:
            raise OneCError(
                f"1C network error while {stage}: {type(exc).__name__}: {exc}"
            ) from exc

    async def list_partners(
        self,
        *,
        page_size: int = 500,
        modified_since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        partners: list[dict[str, Any]] = []
        skip = 0
        effective_page_size = page_size

        filters = ["DeletionMark eq false", "Клиент eq true"]
        if modified_since is not None:
            stamp = modified_since
            if stamp.tzinfo is not None:
                stamp = stamp.replace(tzinfo=None)
            literal = stamp.strftime("%Y-%m-%dT%H:%M:%S")
            filters.append(
                "("
                f"ИБР_ДатаРедактирования ge datetime'{literal}' or "
                f"ИБР_ДатаСоздания ge datetime'{literal}'"
                ")"
            )

        async with httpx.AsyncClient(
            auth=self.auth,
            timeout=self.timeout,
            headers={"Accept": "application/json"},
        ) as client:
            while True:
                page = await self._get_value(
                    client,
                    "Catalog_Партнеры",
                    params={
                        "$format": "json",
                        "$filter": " and ".join(filters),
                        "$select": (
                            "Ref_Key,Code,Description,НаименованиеПолное,"
                            "ОсновнойМенеджер_Key,Клиент,"
                            "ИБР_ДатаСоздания,ИБР_ДатаРедактирования"
                        ),
                        "$orderby": "Code,Ref_Key",
                        "$top": str(effective_page_size),
                        "$skip": str(skip),
                    },
                )
                if not page:
                    break
                partners.extend(page)
                skip += len(page)
                if len(page) < effective_page_size:
                    effective_page_size = len(page)
        return partners

    async def _resolve_agent_ref(
        self,
        client: httpx.AsyncClient,
        agent_description: str,
    ) -> str:
        escaped = agent_description.replace("'", "''")
        values = await self._get_value(
            client,
            self.EXCHANGE_PLAN_ENTITY,
            params={
                "$format": "json",
                "$filter": f"Description eq '{escaped}' and DeletionMark eq false",
                "$select": "Ref_Key,Description,Code,ТипАгента",
                "$top": "2",
            },
        )
        if not values:
            raise OneCEntityNotFoundError(f"1C agent {agent_description!r} was not found")
        if len(values) > 1:
            raise OneCPlanReviewRequired(
                f"more than one 1C exchange-plan node matched {agent_description!r}"
            )
        return str(values[0]["Ref_Key"])

    async def _find_active_plan(
        self,
        client: httpx.AsyncClient,
        agent_ref: str,
        business_date: date,
    ) -> AgentPlan:
        values = await self._get_value(
            client,
            self.PLAN_ENTITY,
            params={
                "$format": "json",
                "$filter": (
                    f"Агент_Key eq guid'{agent_ref}' and "
                    "DeletionMark eq false and Posted eq true"
                ),
                "$select": (
                    "Ref_Key,DataVersion,Number,Date,Агент_Key,Posted,"
                    "Циклический,ДатаОкончанияЦиклическогоПлана"
                ),
                "$orderby": "Date desc",
                "$top": "50",
            },
        )

        active: list[AgentPlan] = []
        existing: list[AgentPlan] = []
        for item in values:
            plan_date = _parse_1c_datetime(item.get("Date"))
            if plan_date is None:
                continue
            cyclic = bool(item.get("Циклический"))
            cyclic_end = _parse_1c_datetime(item.get("ДатаОкончанияЦиклическогоПлана"))

            plan = AgentPlan(
                ref_key=str(item["Ref_Key"]),
                number=item.get("Number"),
                date=plan_date,
                data_version=item.get("DataVersion"),
                posted=bool(item.get("Posted")),
                cyclic=cyclic,
                cyclic_end_date=cyclic_end,
            )
            existing.append(plan)

            if plan_date.date() > business_date:
                continue
            if cyclic:
                if cyclic_end is None or business_date > cyclic_end.date():
                    continue
            elif plan_date.date() != business_date:
                continue

            active.append(plan)

        active.sort(key=lambda plan: plan.date, reverse=True)
        if active:
            if len(active) > 1 and active[0].date == active[1].date:
                raise OneCPlanReviewRequired(
                    f"multiple active agent plans have the same effective date for {agent_ref}"
                )
            return active[0]

        # Business rule: never auto-create a new agent plan from WhatsApp.
        # If the date window says there is no active plan, reuse the latest
        # existing posted, non-deleted plan for the requesting agent.
        existing.sort(key=lambda plan: plan.date, reverse=True)
        if existing:
            if len(existing) > 1 and existing[0].date == existing[1].date:
                raise OneCPlanReviewRequired(
                    f"multiple existing agent plans have the same latest date for {agent_ref}"
                )
            return existing[0]

        raise OneCEntityNotFoundError(
            f"no posted апПланАгента document exists for agent {agent_ref}"
        )


    async def _get_partner_plan_row(
        self,
        client: httpx.AsyncClient,
        plan_ref: str,
        partner_ref: str,
    ) -> dict[str, Any] | None:
        values = await self._get_value(
            client,
            self.PLAN_ROWS_ENTITY,
            params={
                "$format": "json",
                "$filter": (
                    f"Ref_Key eq guid'{plan_ref}' and "
                    f"Партнер_Key eq guid'{partner_ref}'"
                ),
                "$select": (
                    "LineNumber,Партнер_Key,Контрагент_Key,Адрес,"
                    "Д1,Д2,Д3,Д4,Д5,Д6,Д7"
                ),
                "$top": "2",
            },
        )
        if not values:
            return None
        if len(values) > 1:
            raise OneCPlanReviewRequired(
                f"partner {partner_ref} appears more than once in visit plan {plan_ref}"
            )
        return values[0]

    async def _get_plan_rows(
        self,
        client: httpx.AsyncClient,
        plan_ref: str,
        *,
        max_concurrency: int = FULL_ROW_CONCURRENCY,
    ) -> list[dict[str, Any]]:
        """Load the complete visit-plan snapshot.

        1. One paginated query for the light row keys (LineNumber, partner).
        2. Every full row by key, with bounded concurrency.

        The global read semaphore keeps simultaneous requests from different
        agents from overloading the 1C publication.
        """
        async with _global_plan_read_semaphore():
            started = time.perf_counter()
            _timing_log("full_rows_concurrent_start", plan=plan_ref, concurrency=max_concurrency)

            light_rows = await self._get_all_plan_row_keys(client, plan_ref)
            semaphore = asyncio.Semaphore(max(1, max_concurrency))

            async def fetch_row(item: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                line_number = _int_value(item.get("LineNumber"))
                if line_number <= 0:
                    raise OneCPlanReviewRequired(
                        f"invalid visit-plan LineNumber {item.get('LineNumber')!r}"
                    )
                async with semaphore:
                    response = await client.get(
                        f"{self._plan_row_url(plan_ref, line_number)}?$format=json"
                    )
                    await self._raise_for_1c_error(response)
                    data = response.json()
                if not isinstance(data, dict):
                    raise OneCError(f"unexpected OData row shape for line {line_number}")
                return line_number, data

            fetched = await asyncio.gather(*(fetch_row(item) for item in light_rows))
            rows = [row for _, row in sorted(fetched, key=lambda item: item[0])]

            _timing_log(
                "full_rows_concurrent_done",
                plan=plan_ref,
                rows=len(rows),
                concurrency=max_concurrency,
                seconds=round(time.perf_counter() - started, 3),
            )
            return rows



    async def _get_all_plan_row_keys(
        self,
        client: httpx.AsyncClient,
        plan_ref: str,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        skip = 0
        while True:
            page = await self._get_value(
                client,
                self.PLAN_ROWS_ENTITY,
                params={
                    "$format": "json",
                    "$filter": f"Ref_Key eq guid'{plan_ref}'",
                    "$select": "LineNumber,Партнер_Key",
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

    def _apply_instruction_to_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        partner_ref: str,
        instruction: VisitInstruction,
        plan_ref: str | None = None,
        plan_date: datetime | date | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        row = next(
            (
                item
                for item in rows
                if str(item.get("Партнер_Key", "")).lower() == partner_ref.lower()
            ),
            None,
        )

        created = False
        if row is None:
            if instruction.operation in {VisitOperation.REMOVE, VisitOperation.REMOVE_ALL}:
                return False, {
                    "created": False,
                    "line_number": None,
                    "before_days": [],
                    "after_days": [],
                    "missing_from_plan": True,
                }
            row = self._new_plan_row(
                rows,
                partner_ref,
                instruction.target_days,
                plan_ref=plan_ref,
                plan_date=plan_date,
            )
            rows.append(row)
            created = True
            return True, {
                "created": True,
                "line_number": row["LineNumber"],
                "before_days": [],
                "after_days": list(instruction.target_days),
            }

        before_days = _active_weekdays(row, plan_date)

        # Safety preconditions for destructive operations. Without these, a
        # wrong client/day could be reported as a successful idempotent write:
        # REMOVE on an already-false day changes nothing, and MOVE could merely
        # add the target day if the named source day was never active.
        if instruction.operation == VisitOperation.REMOVE:
            requested = set(instruction.target_days)
            if not (requested & set(before_days)):
                return False, {
                    "created": False,
                    "line_number": row.get("LineNumber"),
                    "before_days": before_days,
                    "after_days": before_days,
                    "precondition_error": (
                        "requested remove day is not active for this client"
                    ),
                }

        if (
            instruction.operation == VisitOperation.MOVE
            and instruction.source_day is not None
            and instruction.source_day not in before_days
        ):
            return False, {
                "created": False,
                "line_number": row.get("LineNumber"),
                "before_days": before_days,
                "after_days": before_days,
                "precondition_error": (
                    f"requested move source day D{instruction.source_day} "
                    "is not active for this client"
                ),
            }

        changed = self._mutate_existing_row(
            row, before_days, instruction, plan_date=plan_date
        )
        after_days = _active_weekdays(row, plan_date)
        return changed, {
            "created": created,
            "line_number": row.get("LineNumber"),
            "before_days": before_days,
            "after_days": after_days,
        }

    def _mutate_existing_row(
        self,
        row: dict[str, Any],
        before_days: list[int],
        instruction: VisitInstruction,
        *,
        plan_date: datetime | date | None = None,
    ) -> bool:
        targets = set(instruction.target_days)

        if instruction.operation == VisitOperation.ADD:
            changed = False
            for day in targets:
                field = _weekday_field(day, plan_date)
                if not bool(row.get(field)):
                    row[field] = True
                    changed = True
            return changed

        if instruction.operation == VisitOperation.REMOVE_ALL:
            changed = False
            for slot in range(1, 32):
                field = f"Д{slot}"
                if bool(row.get(field)):
                    row[field] = False
                    changed = True
            return changed

        if instruction.operation == VisitOperation.REMOVE:
            changed = False
            for day in targets:
                field = _weekday_field(day, plan_date)
                if bool(row.get(field)):
                    row[field] = False
                    changed = True
            return changed

        if instruction.operation == VisitOperation.MOVE and instruction.source_day is not None:
            changed = False
            source_field = _weekday_field(instruction.source_day, plan_date)
            if instruction.source_day not in targets and bool(row.get(source_field)):
                row[source_field] = False
                changed = True
            for day in targets:
                field = _weekday_field(day, plan_date)
                if not bool(row.get(field)):
                    row[field] = True
                    changed = True
            return changed

        # SET, or MOVE without an explicitly named source weekday: the target
        # weekdays become the complete D1..D7 schedule for this client.
        changed = False
        for day in range(1, 8):
            field = _weekday_field(day, plan_date)
            desired = day in targets
            if bool(row.get(field)) != desired:
                row[field] = desired
                changed = True
        return changed

    @staticmethod
    def _new_plan_row(
        rows: list[dict[str, Any]],
        partner_ref: str,
        target_days: tuple[int, ...],
        *,
        plan_ref: str | None = None,
        plan_date: datetime | date | None = None,
    ) -> dict[str, Any]:
        max_line = max((_int_value(item.get("LineNumber")) for item in rows), default=0)
        target_slots = {_weekday_to_plan_slot(day, plan_date) for day in target_days}
        row: dict[str, Any] = {
            "LineNumber": str(max_line + 1),
            "Партнер_Key": partner_ref,
            "Контрагент_Key": ZERO_GUID,
            "Адрес": "",
        }
        if plan_ref:
            row["Ref_Key"] = plan_ref
        for day in range(1, 32):
            row[f"Д{day}"] = day in target_slots
            row[f"Н{day}"] = 0
        return row


    async def _patch_plan_rows_batch(
        self,
        client: httpx.AsyncClient,
        plan: AgentPlan,
        rows: list[dict[str, Any]],
        *,
        changes: tuple[OneCVisitChange, ...],
    ) -> dict[str, Any]:
        """Write the visit-plan snapshot and make 1C apply it.

          1. Re-read DataVersion; abort (retryable) if the document changed.
          2. PATCH the parent document with If-Match. On timeout, poll until the
             requested rows become visible (large plans finish server-side).
          3. If the document was posted, call Post exactly once. Posted=True
             after a PATCH does NOT mean 1C refreshed its posting side effects,
             so Post is never skipped. The document is never un-posted and a timed-out
             Post is not repeated blindly.
          4. Verify the requested rows and the Posted flag.
        """
        label = plan.number or plan.ref_key
        url = self._entity_url(self.PLAN_ENTITY, plan.ref_key)
        large_plan = len(rows) > LARGE_PLAN_ROWS
        patch_timeout = LARGE_PLAN_PATCH_TIMEOUT_SECONDS if large_plan else PARENT_PATCH_TIMEOUT_SECONDS
        post_timeout = LARGE_PLAN_POST_TIMEOUT_SECONDS if large_plan else POST_TIMEOUT_SECONDS
        timings: dict[str, Any] = {
            "pre_state": 0.0,
            "patch": 0.0,
            "patch_timeout_verify": 0.0,
            "post": 0.0,
            "final_verify": 0.0,
        }

        # 1. optimistic concurrency check
        started = time.perf_counter()
        current = await self._get_plan_state(client, plan.ref_key)
        timings["pre_state"] = round(time.perf_counter() - started, 3)
        was_posted = bool(current.get("Posted"))
        _timing_log("pre_state_done", plan=label, seconds=timings["pre_state"], posted=was_posted)
        if plan.data_version and current.get("DataVersion") != plan.data_version:
            raise OneCPlanReviewRequired(
                "1C document changed while visit-plan rows were being read; retry/review is required"
            )

        # 2. parent PATCH
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if current.get("DataVersion"):
            headers["If-Match"] = str(current["DataVersion"])
        payload = {"ПланПосещений": [_clean_row_for_write(row) for row in rows]}

        started = time.perf_counter()
        _timing_log("patch_start", plan=label, rows=len(rows), timeout=patch_timeout)
        try:
            response = await client.patch(
                f"{url}?$format=json", json=payload, headers=headers, timeout=patch_timeout
            )
            timings["patch"] = round(time.perf_counter() - started, 3)
            _timing_log(
                "patch_done", plan=label, seconds=timings["patch"], status=response.status_code
            )
            await self._raise_for_1c_error(response)
        except httpx.TimeoutException:
            _timing_log("patch_timeout", plan=label, timeout=patch_timeout)
            started = time.perf_counter()
            applied = await self._wait_until_changes_verified(
                client,
                plan_ref=plan.ref_key,
                changes=changes,
                plan_date=plan.date,
                attempts=12,
                delay_seconds=3.0,
                log_prefix="patch_timeout_verify",
            )
            timings["patch_timeout_verify"] = round(time.perf_counter() - started, 3)
            if not applied:
                raise OneCPlanReviewRequired(
                    "1C parent PATCH timed out and the final visit-plan state could not be verified"
                )

        # 3. single Post for posted documents
        if was_posted:
            started = time.perf_counter()
            _timing_log("post_start", plan=label, timeout=post_timeout)
            try:
                post_response = await client.post(
                    f"{url}/Post?PostingModeOperational=false", timeout=post_timeout
                )
            except httpx.TimeoutException as exc:
                _timing_log("post_timeout", plan=label, timeout=post_timeout)
                raise OneCPlanReviewRequired(
                    "1C Post did not return before the safety timeout; "
                    "the document was not unposted or reposted a second time"
                ) from exc
            timings["post"] = round(time.perf_counter() - started, 3)
            _timing_log(
                "post_done", plan=label, seconds=timings["post"], status=post_response.status_code
            )
            await self._raise_for_1c_error(post_response)

        # 4. final verification
        started = time.perf_counter()
        changes_ok = await self._wait_until_changes_verified(
            client,
            plan_ref=plan.ref_key,
            changes=changes,
            plan_date=plan.date,
            attempts=6,
        )
        final_state = await self._get_plan_state(client, plan.ref_key)
        timings["final_verify"] = round(time.perf_counter() - started, 3)
        _timing_log(
            "final_verify_done",
            plan=label,
            seconds=timings["final_verify"],
            changes_ok=changes_ok,
            posted=bool(final_state.get("Posted")),
        )
        if not changes_ok:
            raise OneCPlanReviewRequired(
                "1C write completed but one or more requested visit-plan states were not verified"
            )
        if was_posted and not bool(final_state.get("Posted")):
            raise OneCPlanReviewRequired(
                "1C visit-plan change was written but the document is not posted"
            )

        _timing_log("patch_batch_done", plan=label, **timings)
        return timings

    async def _wait_until_changes_verified(
        self,
        client: httpx.AsyncClient,
        *,
        plan_ref: str,
        changes: tuple[OneCVisitChange, ...],
        plan_date: datetime | date | None = None,
        attempts: int = 3,
        delay_seconds: float = 2.0,
        log_prefix: str | None = None,
    ) -> bool:
        for attempt in range(attempts):
            ok = False
            try:
                ok = await self._changes_are_currently_satisfied(
                    client,
                    plan_ref=plan_ref,
                    changes=changes,
                    plan_date=plan_date,
                )
            except (httpx.TimeoutException, httpx.RequestError):
                ok = False

            if log_prefix:
                _timing_log(
                    log_prefix,
                    plan=plan_ref,
                    attempt=attempt + 1,
                    attempts=attempts,
                    ok=ok,
                )

            if ok:
                return True
            if attempt + 1 < attempts:
                await asyncio.sleep(delay_seconds)
        return False

    async def _changes_are_currently_satisfied(
        self,
        client: httpx.AsyncClient,
        *,
        plan_ref: str,
        changes: tuple[OneCVisitChange, ...],
        plan_date: datetime | date | None = None,
    ) -> bool:
        for change in changes:
            if not await self._instruction_is_currently_satisfied(
                client,
                plan_ref=plan_ref,
                partner_ref=change.partner_ref,
                instruction=change.instruction,
                plan_date=plan_date,
            ):
                return False
        return True

    async def _get_plan_state(
        self,
        client: httpx.AsyncClient,
        plan_ref: str,
    ) -> dict[str, Any]:
        params = {
            "$format": "json",
            "$select": "Ref_Key,Number,Posted,DataVersion",
        }
        query = urlencode(params, quote_via=quote, safe="$，()':*".replace("，", ","))
        response = await client.get(
            f"{self._entity_url(self.PLAN_ENTITY, plan_ref)}?{query}",
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
        await self._raise_for_1c_error(response)
        data = response.json()
        if not isinstance(data, dict):
            raise OneCError("unexpected OData document state response")
        return data


    async def _instruction_is_currently_satisfied(
        self,
        client: httpx.AsyncClient,
        *,
        plan_ref: str,
        partner_ref: str,
        instruction: VisitInstruction,
        plan_date: datetime | date | None = None,
    ) -> bool:
        row = await self._get_partner_plan_row(client, plan_ref, partner_ref)
        if row is None:
            return instruction.operation == VisitOperation.REMOVE_ALL

        if instruction.operation == VisitOperation.REMOVE_ALL:
            return not any(bool(row.get(f"Д{slot}")) for slot in range(1, 32))

        targets = set(instruction.target_days)
        active = set(_active_weekdays(row, plan_date))

        if instruction.operation == VisitOperation.ADD:
            return targets <= active

        if instruction.operation == VisitOperation.REMOVE:
            return not bool(targets & active)

        if instruction.operation == VisitOperation.MOVE and instruction.source_day is not None:
            source_ok = (
                instruction.source_day in targets
                or instruction.source_day not in active
            )
            targets_ok = targets <= active
            return source_ok and targets_ok

        # SET, or MOVE without an explicit source day, means the target days
        # are the complete D1..D7 schedule.
        return active == targets

    async def _get_value(
        self,
        client: httpx.AsyncClient,
        entity: str,
        *,
        params: dict[str, str],
    ) -> list[dict[str, Any]]:
        # 1C OData is sensitive to query-string encoding. In particular, the
        # default httpx encoding can encode spaces as '+', while this 1C
        # publication behaves correctly with percent-encoded spaces (%20).
        query = urlencode(params, quote_via=quote, safe="$,()':*")
        response = await client.get(f"{self.base_url}/{entity}?{query}")
        await self._raise_for_1c_error(response)
        data = response.json()
        value = data.get("value", [])
        if not isinstance(value, list):
            raise OneCError(f"unexpected OData response shape for {entity}")
        return value

    def _entity_url(self, entity: str, ref_key: str) -> str:
        return f"{self.base_url}/{entity}(guid'{ref_key}')"

    def _plan_row_url(self, plan_ref: str, line_number: int) -> str:
        key = f"Ref_Key=guid'{plan_ref}',LineNumber={line_number}"
        return f"{self.base_url}/{self.PLAN_ROWS_ENTITY}({key})"

    @staticmethod
    async def _raise_for_1c_error(response: httpx.Response) -> None:
        if response.is_success:
            return
        detail = response.text[:2000]
        if response.status_code == 412:
            raise OneCPlanReviewRequired(
                "1C document changed after it was read; retry/review is required"
            )
        raise OneCError(
            f"1C OData request failed with HTTP {response.status_code}: {detail}"
        )



def _parse_1c_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _plan_anchor_weekday(plan_date: datetime | date | None) -> int:
    """Return the logical weekday (Mon=1..Sun=7) represented by 1C field Д1.

    In апПланАгента the D fields are positions inside the plan's 7-day cycle,
    not fixed Monday..Sunday columns. For example, a plan dated Sunday has
    Д1=Sunday, Д2=Monday, ..., Д5=Thursday.
    """
    if plan_date is None:
        # Backward-compatible fallback for unit tests/legacy callers that do not
        # have plan metadata. Real OData writes always pass AgentPlan.date.
        return 1
    anchor_date = plan_date.date() if isinstance(plan_date, datetime) else plan_date
    return anchor_date.weekday() + 1


def _weekday_to_plan_slot(
    weekday: int,
    plan_date: datetime | date | None,
) -> int:
    if weekday < 1 or weekday > 7:
        raise ValueError(f"weekday must be in 1..7, got {weekday}")
    anchor = _plan_anchor_weekday(plan_date)
    return ((weekday - anchor) % 7) + 1


def _plan_slot_to_weekday(
    slot: int,
    plan_date: datetime | date | None,
) -> int:
    if slot < 1 or slot > 7:
        raise ValueError(f"plan slot must be in 1..7, got {slot}")
    anchor = _plan_anchor_weekday(plan_date)
    return ((anchor + slot - 2) % 7) + 1


def _weekday_field(weekday: int, plan_date: datetime | date | None) -> str:
    return f"Д{_weekday_to_plan_slot(weekday, plan_date)}"


def _active_weekdays(
    row: dict[str, Any],
    plan_date: datetime | date | None = None,
) -> list[int]:
    weekdays = [
        _plan_slot_to_weekday(slot, plan_date)
        for slot in range(1, 8)
        if bool(row.get(f"Д{slot}"))
    ]
    return sorted(weekdays)


def _clean_row_for_write(row: dict[str, Any]) -> dict[str, Any]:
    # Match the payload shape that was verified against the live 1C database:
    # keep every actual row field (including Ref_Key and stream companion
    # Type/Base64Data fields) and remove only response metadata.
    return {
        key: value
        for key, value in row.items()
        if not key.startswith("odata.")
        and "@" not in key
        and not key.startswith("__")
    }


def _int_value(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        if not text:
            return 0
        # Some 1C OData publications serialize integral LineNumber values as
        # decimal-looking strings such as "12.000000". Accept only values that
        # are mathematically integral; do not silently truncate fractions.
        number = float(text)
        if not number.is_integer():
            raise ValueError(f"non-integral integer value: {value!r}")
        return int(number)
