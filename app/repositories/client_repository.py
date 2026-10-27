from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.client import Client
from app.services.matching.candidate_cache import match_candidate_cache
from app.services.matching.types import MatchCandidate


class ClientRepository:
    async def get_active_by_id(
        self,
        session: AsyncSession,
        *,
        client_id: UUID,
    ) -> Client | None:
        stmt = select(Client).where(
            Client.id == client_id,
            Client.is_active.is_(True),
        )
        return await session.scalar(stmt)

    async def list_active_match_candidates(self, session: AsyncSession) -> list[MatchCandidate]:
        cached = await match_candidate_cache.get()
        if cached is not None:
            return cached

        stmt = (
            select(Client)
            .where(Client.is_active.is_(True))
            .options(selectinload(Client.aliases))
            .order_by(Client.display_name)
        )
        clients = (await session.scalars(stmt)).unique().all()
        candidates = [
            MatchCandidate(
                client_id=client.id,
                code=client.code,
                display_name=client.display_name,
                normalized_name=client.normalized_name,
                aliases=tuple(alias.normalized_alias for alias in client.aliases),
                one_c_ref=client.one_c_ref,
            )
            for client in clients
        ]
        await match_candidate_cache.set(candidates)
        return candidates
