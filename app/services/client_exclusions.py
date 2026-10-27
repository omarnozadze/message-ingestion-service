from __future__ import annotations

from app.core.config import settings
from app.services.matching.normalizer import contains_phrase, normalize_text


def excluded_client_name_terms() -> tuple[str, ...]:
    """Configured client-chain phrases excluded from search/matching.

    Values come from Settings so production can change the list through
    EXCLUDED_CLIENT_NAME_TERMS without a code change/deploy.
    """
    return tuple(
        normalized
        for value in settings.excluded_client_name_terms
        if (normalized := normalize_text(value))
    )


def is_excluded_client_name(value: str | None) -> bool:
    normalized = normalize_text(value or "")
    if not normalized:
        return False
    return any(
        contains_phrase(normalized, term)
        for term in excluded_client_name_terms()
    )
