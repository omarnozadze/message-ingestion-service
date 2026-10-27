from __future__ import annotations

import asyncio
import sys
from collections import Counter

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionFactory
from app.integrations.one_c.odata import OneCODataClient
from app.models.client import Client
from app.models.client_alias import ClientAlias
from app.services.matching.normalizer import normalize_text
from app.services.plan_aliases import make_address_alias

CONTACT_ENTITY = "Catalog_Партнеры_КонтактнаяИнформация"
ZERO_GUID = "00000000-0000-0000-0000-000000000000"


def _nonzero_guid(value: object) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and text != ZERO_GUID)


def _looks_like_address(row: dict) -> bool:
    representation = str(row.get("Представление") or "").strip()
    if not representation:
        return False

    # Explicit phone/email/web fields always win over loose heuristics.
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

    # 1C address rows normally carry at least one structured geography field.
    if str(row.get("Страна") or "").strip():
        return True
    if str(row.get("Регион") or "").strip():
        return True
    if _nonzero_guid(row.get("Город_Key")):
        return True
    if _nonzero_guid(row.get("Улица_Key")):
        return True

    return False


async def _load_contact_rows(
    one_c: OneCODataClient,
    client: httpx.AsyncClient,
) -> list[dict]:
    rows: list[dict] = []
    skip = 0
    page_size = 500

    while True:
        page = await one_c._get_value(
            client,
            CONTACT_ENTITY,
            params={
                "$format": "json",
                "$select": (
                    "Ref_Key,LineNumber,Тип,Представление,ЗначенияПолей,"
                    "Страна,Регион,Город_Key,Улица_Key,АдресЭП,"
                    "ДоменноеИмяСервера,НомерТелефона,НомерТелефонаБезКодов"
                ),
                "$orderby": "Ref_Key,LineNumber",
                "$top": str(page_size),
                "$skip": str(skip),
            },
        )
        if not page:
            break
        rows.extend(page)
        skip += len(page)
        print(f"contact rows: {len(rows)}", flush=True)
        if len(page) < page_size:
            break

    return rows


async def main() -> None:
    if not settings.one_c_username or not settings.one_c_password:
        raise RuntimeError("ONE_C_USERNAME and ONE_C_PASSWORD must be set in .env")

    one_c = OneCODataClient(
        base_url=settings.one_c_base_url,
        username=settings.one_c_username,
        password=settings.one_c_password,
        timeout_seconds=max(settings.one_c_timeout_seconds, 60.0),
    )

    async with httpx.AsyncClient(
        auth=one_c.auth,
        timeout=one_c.timeout,
        headers={"Accept": "application/json"},
    ) as http_client:
        rows = await _load_contact_rows(one_c, http_client)

    type_counts = Counter(str(row.get("Тип") or "<empty>") for row in rows)
    address_rows = [row for row in rows if _looks_like_address(row)]

    created = 0
    already_present = 0
    missing_client = 0

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
            str(client.one_c_ref).strip().lower(): client
            for client in clients
            if client.one_c_ref
        }

        existing_pairs = set(
            (
                str(client_id),
                normalized_alias,
            )
            for client_id, normalized_alias in (
                await session.execute(
                    select(ClientAlias.client_id, ClientAlias.normalized_alias)
                )
            ).all()
        )

        for row in address_rows:
            partner_ref = str(row.get("Ref_Key") or "").strip().lower()
            client_obj = by_ref.get(partner_ref)
            if client_obj is None:
                missing_client += 1
                continue

            representation = str(row.get("Представление") or "").strip()
            alias = make_address_alias(f"{client_obj.display_name} {representation}")
            if not alias:
                continue
            normalized_alias = normalize_text(alias)
            key = (str(client_obj.id), normalized_alias)
            if key in existing_pairs:
                already_present += 1
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

    print()
    print(f"contact rows fetched: {len(rows)}")
    print(f"address rows detected: {len(address_rows)}")
    print(f"address aliases created: {created}")
    print(f"already present: {already_present}")
    print(f"missing local client: {missing_client}")
    print("contact types:")
    for name, count in type_counts.most_common():
        print(f"  {name}: {count}")


def _windows_selector_loop_factory():
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    return loop


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=_windows_selector_loop_factory)
    else:
        asyncio.run(main())
