"""Quick PostgreSQL connectivity check: ``uv run python -m scripts.check_database``."""
from __future__ import annotations

import asyncio
import selectors

from sqlalchemy import text

from app.db.session import AsyncSessionFactory


async def main() -> None:
    async with AsyncSessionFactory() as session:
        value = await session.scalar(text("SELECT 1"))
        print("DATABASE_OK =", value)


if __name__ == "__main__":
    asyncio.run(
        main(),
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )
