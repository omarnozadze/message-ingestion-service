from uuid import UUID

from app.core.enums import MatchMethod
from app.services.matching.engine import MatchingEngine
from app.services.matching.types import MatchCandidate


def _candidate(value: int, name: str) -> MatchCandidate:
    return MatchCandidate(
        client_id=UUID(f"00000000-0000-0000-0000-{value:012d}"),
        code=f"00-{value:08d}",
        display_name=name,
        normalized_name=name,
        aliases=(),
        one_c_ref=f"ref-{value}",
    )


def test_exact_prefixed_branch_beats_same_numeric_suffix() -> None:
    s278 = _candidate(1, "რითეილ ჯგუფი *S278 - ბორჯომი, წაღვერი")
    k278 = _candidate(2, "რითეილ ჯგუფი *K278 - თბილისი")
    d278 = _candidate(3, "რითეილ ჯგუფი *D278 - ბათუმი")

    result = MatchingEngine().match(
        "403 რითეილ S278 სამშაბათზე ჩასვი",
        [k278, d278, s278],
    )

    assert result.matched is True
    assert result.client_id == s278.client_id
    assert result.method == MatchMethod.EXACT_BRANCH
    assert result.score == 100.0
    assert result.evidence is not None and "exact-branch:s278" in result.evidence


def test_prefixed_branch_requires_compatible_name() -> None:
    retail = _candidate(4, "რითეილ ჯგუფი *S278 - ბორჯომი")
    other = _candidate(5, "სხვა ქსელი *S278 - თბილისი")

    result = MatchingEngine().match("რითეილ S278 ჩასვი", [other, retail])
    assert result.matched is True
    assert result.client_id == retail.client_id


def test_n_marker_is_not_treated_as_meaningful_prefix() -> None:
    plain = _candidate(6, "რითეილ ჯგუფი 278 - ბორჯომი")
    result = MatchingEngine().match("რითეილ N278 ჩასვი", [plain])
    assert result.matched is True
    assert result.method != MatchMethod.EXACT_BRANCH
