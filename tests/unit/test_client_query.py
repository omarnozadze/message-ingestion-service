from app.services.matching.client_query import (
    extract_client_identity_text,
    numeric_tokens,
)


def test_extract_client_identity_from_multi_day_command() -> None:
    assert (
        extract_client_identity_text(
            "გვირილა 0203 სამშაბათზე და ხუთშაბათზე ჩასვი"
        )
        == "გვირილა 0203"
    )


def test_extract_client_identity_from_today_command() -> None:
    assert extract_client_identity_text("#452 ორი ნაბიჯი ჩამისვი დღევანდელზე") == "452 ორი ნაბიჯი"


def test_numeric_tokens_ignore_leading_zeroes() -> None:
    assert numeric_tokens("გვირილა N0203") == ("203",)
    assert numeric_tokens("გვირილა 203") == ("203",)


def test_canonical_search_text_only_drops_n_marker_and_hash_punctuation() -> None:
    from app.services.matching.client_query import canonical_search_text

    assert canonical_search_text("დეილი N0158") == "დეილი 158"
    assert canonical_search_text("დეილი #0158") == "დეილი 158"
    assert canonical_search_text("დეილი 0158") == "დეილი 158"
    assert canonical_search_text("რითეილ S0278") == "რითეილ s278"
    assert canonical_search_text("რითეილ K0278") == "რითეილ k278"
    assert canonical_search_text("რითეილ D0278") == "რითეილ d278"


def test_numeric_tokens_do_not_collapse_meaningful_branch_prefixes() -> None:
    assert numeric_tokens("რითეილ S278") == ()
    assert numeric_tokens("რითეილ K278") == ()
    assert numeric_tokens("რითეილ D278") == ()
    assert numeric_tokens("რითეილ N278") == ("278",)
    assert numeric_tokens("რითეილ #278") == ("278",)
