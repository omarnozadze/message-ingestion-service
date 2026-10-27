import asyncio
import sys
from sqlalchemy import select

from app.db.session import AsyncSessionFactory
from app.models.client import Client
from app.models.client_alias import ClientAlias
from app.services.matching.normalizer import normalize_text


SAMPLE_CLIENTS = [
    {
        "one_c_ref": "1c-client-0043",
        "code": "0043",
        "display_name": "გვირილა",
        "aliases": ["გვირილა", "gvirela"],
    },
    {
        "one_c_ref": "1c-client-0101",
        "code": "0101",
        "display_name": "ნინო ბერიძე",
        "aliases": ["ნინო", "ნინო ბ"],
    },
    {
        "one_c_ref": "1c-client-0202",
        "code": "0202",
        "display_name": "გიორგი მაისურაძე",
        "aliases": ["გიო მაისურაძე", "გიორგი მ"],
    },
]


async def main() -> None:
    async with AsyncSessionFactory() as session:
        for item in SAMPLE_CLIENTS:
            existing = await session.scalar(select(Client).where(Client.code == item["code"]))
            if existing:
                continue
            client = Client(
                one_c_ref=item["one_c_ref"],
                code=item["code"],
                display_name=item["display_name"],
                normalized_name=normalize_text(item["display_name"]),
            )
            session.add(client)
            await session.flush()
            session.add_all(
                [
                    ClientAlias(
                        client_id=client.id,
                        alias=alias,
                        normalized_alias=normalize_text(alias),
                    )
                    for alias in item["aliases"]
                ]
            )
        await session.commit()
    print("Seed complete")


def _windows_selector_loop_factory():
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    return loop


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(
            main(),
            loop_factory=_windows_selector_loop_factory,
        )
    else:
        asyncio.run(main())
