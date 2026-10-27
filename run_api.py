"""Canonical API launcher.

On Windows, uvicorn/``fastapi dev`` default to the Proactor event loop, but async
Psycopg requires a Selector loop. This launcher always uses a Selector loop and
reads host/port/log level from the environment (set by start-api.ps1).
"""
from __future__ import annotations

import asyncio
import os
import selectors

import uvicorn


async def main() -> None:
    config = uvicorn.Config(
        "app.main:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8011")),
        log_level=os.getenv("UVICORN_LOG_LEVEL", os.getenv("LOG_LEVEL", "info")).lower(),
        workers=1,  # in-process 1C write locks require exactly one worker
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(
        main(),
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )
