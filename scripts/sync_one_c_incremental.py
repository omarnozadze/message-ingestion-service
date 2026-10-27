from __future__ import annotations

import argparse
import asyncio
import sys

from app.services.one_c_sync import OneCSearchIndexSync


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="force a full reconciliation")
    args = parser.parse_args()

    report = await OneCSearchIndexSync().run_once(force_full=args.full)
    print(report)


def _windows_selector_loop_factory():
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    return loop


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=_windows_selector_loop_factory)
    else:
        asyncio.run(main())
