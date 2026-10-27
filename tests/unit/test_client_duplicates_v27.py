from types import SimpleNamespace
from uuid import UUID

from app.services.client_duplicates import detect_duplicate_clients, duplicate_client_count


def c(n: int, name: str, *, ref: str | None = None, code: str | None = None):
    return SimpleNamespace(
        id=UUID(f"00000000-0000-0000-0000-{n:012d}"),
        one_c_ref=ref,
        code=code,
        display_name=name,
        normalized_name=name,
        is_active=True,
    )


def test_same_chain_and_branch_code_is_reported_even_with_different_location_text() -> None:
    groups = detect_duplicate_clients([
        c(1, "რითეილ ჯგუფი *S278 - ბორჯომი, წაღვერი"),
        c(2, "რითეილ ჯგუფი *S278 - სხვა მისამართი"),
        c(3, "რითეილ ჯგუფი *K278 - თბილისი"),
    ])
    branch = [g for g in groups if g.kind == "branch_identity"]
    assert len(branch) == 1
    assert branch[0].key == "რითეილ ჯგუფი|s278"
    assert duplicate_client_count(groups) == 2


def test_same_branch_code_in_different_chain_is_not_duplicate() -> None:
    groups = detect_duplicate_clients([
        c(1, "რითეილ ჯგუფი *S278 - ბორჯომი"),
        c(2, "სხვა ქსელი *S278 - თბილისი"),
    ])
    assert not [g for g in groups if g.kind == "branch_identity"]


def test_inactive_clients_are_ignored() -> None:
    one = c(1, "რითეილ ჯგუფი *S278 - ბორჯომი")
    two = c(2, "რითეილ ჯგუფი *S278 - თბილისი")
    two.is_active = False
    assert detect_duplicate_clients([one, two]) == ()
