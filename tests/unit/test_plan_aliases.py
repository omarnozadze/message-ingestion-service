from app.services.plan_aliases import extract_plan_address_aliases


def test_extracts_gvirela_n_number_and_address_aliases() -> None:
    assert extract_plan_address_aliases(
        "გვირილა N0203 დიღმის მასივი, კვარტალი 2, კორპუსი 13"
    ) == (
        "გვირილა 0203",
        "გვირილა n0203",
        "addr გვირილა n0203 დიღმის მასივი კვარტალი 2 კორპუსი 13",
    )


def test_extracts_retail_group_s_number_and_address_aliases() -> None:
    assert extract_plan_address_aliases(
        "რითეილ ჯგუფი  * S224 - თბილისი, ლიბანის N16"
    ) == (
        "რითეილ ჯგუფი 224",
        "რითეილ ჯგუფი s224",
        "addr რითეილ ჯგუფი s224 თბილისი ლიბანის n16",
    )


def test_address_without_store_marker_is_searchable_but_not_outlet_alias() -> None:
    assert extract_plan_address_aliases("თბილისი, რუსთაველის გამზირი 10") == (
        "addr თბილისი რუსთაველის გამზირი 10",
    )
