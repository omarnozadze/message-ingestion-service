from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import selectors
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.db.session import AsyncSessionFactory
from app.integrations.one_c.odata import OneCODataClient
from app.repositories.client_repository import ClientRepository
from app.services.ai_matching import AIReviewRequired, AIServiceError, OpenAIAssistedMatching


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse and resolve one WhatsApp visit request with AI without writing to 1C."
    )
    parser.add_argument("--agent", required=True, help="Agent code, e.g. 112")
    parser.add_argument("--text", required=True, help="WhatsApp message text without agent prefix")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is empty in .env")

    ai = OpenAIAssistedMatching(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
        timeout_seconds=settings.openai_timeout_seconds,
        min_parse_confidence=settings.ai_min_parse_confidence,
        min_match_confidence=settings.ai_min_match_confidence,
        candidate_limit=settings.ai_candidate_limit,
        reasoning_effort=settings.ai_reasoning_effort,
    )

    business_date = datetime.now(ZoneInfo(settings.business_timezone)).date()
    try:
        request = await ai.parse_visit_request(
            args.text,
            agent_code=args.agent,
            business_date=business_date,
            agent_prefix=settings.agent_description_prefix,
        )
    except (AIReviewRequired, AIServiceError) as exc:
        raise SystemExit(f"AI PARSE STOPPED: {exc}") from exc

    print("\nPARSED ACTIONS")
    for index, action in enumerate(request.actions):
        print(
            f"[{index}] client={action.client_text!r} "
            f"operation={action.instruction.operation.value} "
            f"source_day={action.instruction.source_day} "
            f"target_days={action.instruction.target_days}"
        )

    preferred: set[str] = set()
    if settings.one_c_mode == "odata" and settings.one_c_username and settings.one_c_password:
        one_c = OneCODataClient(
            base_url=settings.one_c_base_url,
            username=settings.one_c_username,
            password=settings.one_c_password,
            timeout_seconds=settings.one_c_timeout_seconds,
        )
        try:
            preferred = await one_c.get_active_plan_partner_refs(
                agent_description=f"{settings.agent_description_prefix}{args.agent}",
                business_date=business_date,
            )
            print(f"\nACTIVE PLAN CONTEXT: {len(preferred)} partners")
        except Exception as exc:
            print(f"\nACTIVE PLAN CONTEXT unavailable: {type(exc).__name__}: {exc}")

    async with AsyncSessionFactory() as session:
        candidates = await ClientRepository().list_active_match_candidates(session)
        try:
            resolutions = await ai.resolve_clients(
                request.actions,
                candidates,
                preferred_one_c_refs=preferred,
            )
        except (AIReviewRequired, AIServiceError) as exc:
            raise SystemExit(f"AI MATCH STOPPED: {exc}") from exc

        by_id = {candidate.client_id: candidate for candidate in candidates}
        print("\nRESOLVED CLIENTS (READ-ONLY)")
        for resolution in resolutions:
            match = resolution.match
            candidate = by_id[match.client_id]
            print(
                f"[{resolution.action_index}] {candidate.display_name} | "
                f"ref={candidate.one_c_ref} | confidence={match.score:.1f} | "
                f"{resolution.reason}"
            )

    print("\nNO 1C WRITE WAS PERFORMED.")


if __name__ == "__main__":
    asyncio.run(
        main(),
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )
