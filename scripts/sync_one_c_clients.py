import asyncio
import sys

from sqlalchemy import or_, select

from app.core.config import settings
from app.db.session import AsyncSessionFactory
from app.integrations.one_c.odata import OneCODataClient
from app.models.client import Client
from app.services.matching.normalizer import normalize_text


async def main() -> None:
    if not settings.one_c_username or not settings.one_c_password:
        raise RuntimeError("ONE_C_USERNAME and ONE_C_PASSWORD must be set in .env")

    one_c = OneCODataClient(
        base_url=settings.one_c_base_url,
        username=settings.one_c_username,
        password=settings.one_c_password,
        timeout_seconds=settings.one_c_timeout_seconds,
    )
    partners = await one_c.list_partners()

    created = 0
    updated = 0
    async with AsyncSessionFactory() as session:
        for item in partners:
            ref_key = str(item["Ref_Key"])
            code = (item.get("Code") or "").strip() or None
            display_name = (
                (item.get("Description") or "").strip()
                or (item.get("НаименованиеПолное") or "").strip()
                or code
                or ref_key
            )

            conditions = [Client.one_c_ref == ref_key]
            if code:
                conditions.append(Client.code == code)
            client = await session.scalar(select(Client).where(or_(*conditions)))

            if client is None:
                client = Client(
                    one_c_ref=ref_key,
                    code=code,
                    display_name=display_name,
                    normalized_name=normalize_text(display_name),
                    is_active=True,
                )
                session.add(client)
                created += 1
            else:
                client.one_c_ref = ref_key
                client.code = code
                client.display_name = display_name
                client.normalized_name = normalize_text(display_name)
                client.is_active = True
                updated += 1

        await session.commit()

    print(f"1C partner sync complete: {len(partners)} fetched, {created} created, {updated} updated")


def _windows_selector_loop_factory():
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    return loop


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=_windows_selector_loop_factory)
    else:
        asyncio.run(main())
