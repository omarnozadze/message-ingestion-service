from __future__ import annotations

import asyncio
import selectors

from sqlalchemy import delete, select

from app.db.session import AsyncSessionFactory
from app.models.client import Client
from app.models.client_alias import ClientAlias
from app.services.client_exclusions import excluded_client_name_terms, is_excluded_client_name


async def main() -> None:
    async with AsyncSessionFactory() as session:
        clients = (await session.scalars(select(Client))).all()
        excluded = [
            client
            for client in clients
            if is_excluded_client_name(client.display_name)
        ]

        ids = [client.id for client in excluded]
        active_before = sum(1 for client in excluded if client.is_active)

        alias_count = 0
        if ids:
            result = await session.execute(
                delete(ClientAlias).where(ClientAlias.client_id.in_(ids))
            )
            alias_count = int(result.rowcount or 0)

        for client in excluded:
            client.is_active = False

        await session.commit()

    print("EXCLUDED TERMS =", ", ".join(excluded_client_name_terms()))
    print("MATCHED CLIENT ROWS =", len(excluded))
    print("DEACTIVATED ACTIVE CLIENTS =", active_before)
    print("DELETED SEARCH ALIASES =", alias_count)
    print("1C SOURCE DATA WAS NOT MODIFIED.")


if __name__ == "__main__":
    asyncio.run(
        main(),
        loop_factory=lambda: asyncio.SelectorEventLoop(
            selectors.SelectSelector()
        ),
    )
