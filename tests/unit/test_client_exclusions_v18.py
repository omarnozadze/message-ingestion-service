from __future__ import annotations

import pytest

from app.services.client_exclusions import is_excluded_client_name


@pytest.mark.parametrize(
    "name",
    [
        "ორი ნაბიჯი 391",
        "შპს ორი ნაბიჯი * 074 - თბილისი",
        "სს ფუდმარტი * 088 - თბილისი",
        "ნიკორა ტბელ აბუსერიძის 45",
        "შპს ზღაპარი 123",
        "დეილი 158",
    ],
)
def test_excluded_chain_names(name: str) -> None:
    assert is_excluded_client_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "შპს კალია კახაბერ",
        "რითეილ ჯგუფი 319",
        "გვირილა 0229",
        "ნიკორაძე მარკეტი",
        "სხვა კლიენტი",
    ],
)
def test_other_clients_are_not_excluded(name: str) -> None:
    assert not is_excluded_client_name(name)


def test_excluded_chain_list_is_configurable(monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "excluded_client_name_terms", ("საცდელი ქსელი",))
    assert is_excluded_client_name("შპს საცდელი ქსელი 700")
    assert not is_excluded_client_name("ნიკორა 700")
