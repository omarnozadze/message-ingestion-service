from dataclasses import dataclass, field
from uuid import UUID

from app.core.enums import MatchMethod


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    client_id: UUID
    code: str | None
    display_name: str
    normalized_name: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    one_c_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RankedMatch:
    client_id: UUID
    method: MatchMethod
    score: float
    evidence: str


@dataclass(frozen=True, slots=True)
class MatchResult:
    matched: bool
    ambiguous: bool
    client_id: UUID | None = None
    method: MatchMethod | None = None
    score: float | None = None
    evidence: str | None = None
    alternatives: tuple[RankedMatch, ...] = field(default_factory=tuple)
