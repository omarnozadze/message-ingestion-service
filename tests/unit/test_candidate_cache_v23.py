from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.config import settings
from app.services.matching.candidate_cache import MatchCandidateCache
from app.services.matching.types import MatchCandidate


@pytest.mark.asyncio
async def test_candidate_cache_round_trip_and_invalidate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "client_candidate_cache_ttl_seconds", 300)
    cache = MatchCandidateCache()
    candidate = MatchCandidate(
        client_id=uuid4(),
        code="001",
        display_name="Test Client",
        normalized_name="test client",
        aliases=(),
        one_c_ref="ref-1",
    )

    assert await cache.get() is None
    await cache.set([candidate])
    assert await cache.get() == [candidate]
    await cache.invalidate()
    assert await cache.get() is None


@pytest.mark.asyncio
async def test_candidate_cache_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "client_candidate_cache_ttl_seconds", 0)
    cache = MatchCandidateCache()
    candidate = MatchCandidate(
        client_id=uuid4(),
        code=None,
        display_name="Test Client",
        normalized_name="test client",
        aliases=(),
        one_c_ref=None,
    )
    await cache.set([candidate])
    assert await cache.get() is None
