from __future__ import annotations

import asyncio
from time import monotonic

from app.core.config import settings
from app.services.matching.types import MatchCandidate


class MatchCandidateCache:
    """Small process-local cache for active matching candidates.

    1C synchronization invalidates this cache immediately after a successful
    sync. TTL remains as a safety net for changes made by another process.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._items: tuple[MatchCandidate, ...] | None = None
        self._expires_at = 0.0

    async def get(self) -> list[MatchCandidate] | None:
        async with self._lock:
            if self._items is None or monotonic() >= self._expires_at:
                self._items = None
                self._expires_at = 0.0
                return None
            return list(self._items)

    async def set(self, items: list[MatchCandidate]) -> None:
        ttl = max(0, settings.client_candidate_cache_ttl_seconds)
        async with self._lock:
            if ttl == 0:
                self._items = None
                self._expires_at = 0.0
                return
            self._items = tuple(items)
            self._expires_at = monotonic() + ttl

    async def invalidate(self) -> None:
        async with self._lock:
            self._items = None
            self._expires_at = 0.0


match_candidate_cache = MatchCandidateCache()
