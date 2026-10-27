from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionFactory
from app.integrations.one_c.odata import OneCODataClient, OneCEntityNotFoundError
from app.models.client import Client
from app.models.client_alias import ClientAlias
from app.services.matching.normalizer import normalize_text
from app.services.plan_aliases import extract_plan_address_aliases


def _configured_agent_codes() -> list[str]:
    codes: set[str] = set()
    codes.update(str(value).strip() for value in settings.agent_sender_map.values())
    for values in settings.agent_multi_sender_map.values():
        codes.update(str(value).strip() for value in values)
    return sorted(code for code in codes if code)


async def _load_plan_rows(
    one_c: OneCODataClient,
    client: httpx.AsyncClient,
    *,
    plan_ref: str,
) -> list[dict]:
    rows: list[dict] = []
    skip = 0
    page_size = 500

    while True:
        page = await one_c._get_value(
            client,
            one_c.PLAN_ROWS_ENTITY,
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


async def main() -> None:
    if not settings.one_c_username or not settings.one_c_password:
        raise RuntimeError("ONE_C_USERNAME and ONE_C_PASSWORD must be set in .env")

    agent_codes = _configured_agent_codes()
    if not agent_codes:
        raise RuntimeError(
            "no agent codes configured in AGENT_SENDER_MAP / AGENT_MULTI_SENDER_MAP"
        )

    one_c = OneCODataClient(
        base_url=settings.one_c_base_url,
        username=settings.one_c_username,
        password=settings.one_c_password,
        timeout_seconds=max(settings.one_c_timeout_seconds, 60.0),
    )

    business_date = datetime.now(ZoneInfo(settings.business_timezone)).date()
    created = 0
    already_present = 0
    skipped_missing_client = 0
    rows_seen = 0

    async with httpx.AsyncClient(
        auth=one_c.auth,
        timeout=one_c.timeout,
        headers={"Accept": "application/json"},
    ) as http_client, AsyncSessionFactory() as session:
        for agent_code in agent_codes:
            agent_description = f"{settings.agent_description_prefix}{agent_code}"
            try:
                agent_ref = await one_c._resolve_agent_ref(http_client, agent_description)
                plan = await one_c._find_active_plan(http_client, agent_ref, business_date)
            except OneCEntityNotFoundError as exc:
                print(f"SKIP {agent_description}: {exc}")
                continue

            rows = await _load_plan_rows(one_c, http_client, plan_ref=plan.ref_key)
            rows_seen += len(rows)
            print(
                f"{agent_description}: plan {plan.number or plan.ref_key}, "
                f"{len(rows)} light rows"
            )

            for row in rows:
                partner_ref = str(row.get("Партнер_Key") or "").strip()
                address = row.get("Адрес")
                if not partner_ref:
                    continue

                aliases = extract_plan_address_aliases(address)
                if not aliases:
                    continue

                client_obj = await session.scalar(
                    select(Client).where(
                        Client.one_c_ref == partner_ref,
                        Client.is_active.is_(True),
                    )
                )
                if client_obj is None:
                    skipped_missing_client += 1
                    continue

                for alias in aliases:
                    normalized_alias = normalize_text(alias)
                    exists = await session.scalar(
                        select(ClientAlias.id).where(
                            ClientAlias.client_id == client_obj.id,
                            ClientAlias.normalized_alias == normalized_alias,
                        )
                    )
                    if exists is not None:
                        already_present += 1
                        continue

                    session.add(
                        ClientAlias(
                            client_id=client_obj.id,
                            alias=alias,
                            normalized_alias=normalized_alias,
                        )
                    )
                    created += 1

            await session.commit()

    print()
    print(
        "1C plan alias sync complete: "
        f"{rows_seen} rows seen, {created} aliases created, "
        f"{already_present} already present, "
        f"{skipped_missing_client} rows skipped (client not in local DB)"
    )


def _windows_selector_loop_factory():
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    return loop


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=_windows_selector_loop_factory)
    else:
        asyncio.run(main())
