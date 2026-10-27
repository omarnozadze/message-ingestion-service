from uuid import UUID

from app.core.enums import MatchMethod
from app.services.matching.engine import MatchingEngine
from app.services.matching.types import MatchCandidate

CLIENT_A = UUID("00000000-0000-0000-0000-000000000043")
CLIENT_B = UUID("00000000-0000-0000-0000-000000000101")
CLIENT_C = UUID("00000000-0000-0000-0000-000000000202")


def candidates() -> list[MatchCandidate]:
    return [
        MatchCandidate(
            client_id=CLIENT_A,
            code="0043",
            display_name="გვირილა",
            normalized_name="გვირილა",
            aliases=("გვირილა", "gvirela"),
        ),
        MatchCandidate(
            client_id=CLIENT_B,
            code="0101",
            display_name="ნინო ბერიძე",
            normalized_name="ნინო ბერიძე",
            aliases=("ნინო", "ნინო ბ"),
        ),
        MatchCandidate(
            client_id=CLIENT_C,
            code="0202",
            display_name="გიორგი მაისურაძე",
            normalized_name="გიორგი მაისურაძე",
            aliases=("გიო მაისურაძე", "გიორგი მ"),
        ),
    ]


def test_code_match() -> None:
    result = MatchingEngine().match(
        "გვირილა 0043 დღევანდელი ჩანაწერი",
        candidates(),
    )
    assert result.matched is True
    assert result.client_id == CLIENT_A
    assert result.method == MatchMethod.CODE
    assert result.score == 100.0


def test_exact_alias_match() -> None:
    result = MatchingEngine().match("gvirela ახალი ჩანაწერი", candidates())
    assert result.matched is True
    assert result.client_id == CLIENT_A
    assert result.method == MatchMethod.ALIAS


def test_exact_full_name_match() -> None:
    result = MatchingEngine().match("ნინო ბერიძე ახალი ჩანაწერი", candidates())
    assert result.matched is True
    assert result.client_id == CLIENT_B
    assert result.method == MatchMethod.EXACT_NAME


def test_fuzzy_full_name_match() -> None:
    result = MatchingEngine().match("ნინ ბერიძ ახალი ჩანაწერი", candidates())
    assert result.matched is True
    assert result.client_id == CLIENT_B
    assert result.method == MatchMethod.FUZZY
    assert result.score is not None
    assert result.score >= 88.0


def test_fuzzy_alias_match() -> None:
    result = MatchingEngine().match("გვირილაა დღევანდელი ჩანაწერი", candidates())
    assert result.matched is True
    assert result.client_id == CLIENT_A
    assert result.method == MatchMethod.FUZZY


def test_short_fragment_does_not_auto_match() -> None:
    result = MatchingEngine().match("ნინ ახალი ჩანაწერი", candidates())
    assert result.matched is False


def test_conflicting_strong_signals_require_review() -> None:
    result = MatchingEngine().match("გვირილა 0101 დღევანდელი ჩანაწერი", candidates())
    assert result.matched is False
    assert result.ambiguous is True
    assert {item.client_id for item in result.alternatives} == {CLIENT_A, CLIENT_B}


def test_duplicate_alias_requires_review() -> None:
    duplicate_alias_candidates = [
        *candidates(),
        MatchCandidate(
            client_id=UUID("00000000-0000-0000-0000-000000000303"),
            code="0303",
            display_name="სხვა გვირილა",
            normalized_name="სხვა გვირილა",
            aliases=("gvirela",),
        ),
    ]

    result = MatchingEngine().match("gvirela ახალი ჩანაწერი", duplicate_alias_candidates)
    assert result.matched is False
    assert result.ambiguous is True


def test_no_match_goes_to_review() -> None:
    result = MatchingEngine().match("უცნობი კლიენტი", candidates())
    assert result.matched is False
    assert result.ambiguous is False


def test_short_numeric_suffix_matches_1c_code() -> None:
    short_candidates = [
        MatchCandidate(
            client_id=CLIENT_A,
            code="00-00000223",
            display_name="გვირილა",
            normalized_name="გვირილა",
            aliases=(),
        )
    ]
    result = MatchingEngine().match("#223 გვირილა ჩასვი პარასკევზე", short_candidates)
    assert result.matched is True
    assert result.client_id == CLIENT_A
    assert result.method == MatchMethod.CODE


def test_ambiguous_short_code_is_disambiguated_by_name() -> None:
    candidates_with_same_suffix = [
        MatchCandidate(
            client_id=CLIENT_A,
            code="00-00000223",
            display_name="გვირილა",
            normalized_name="გვირილა",
            aliases=(),
        ),
        MatchCandidate(
            client_id=CLIENT_B,
            code="00-00111223",
            display_name="ნიკორა",
            normalized_name="ნიკორა",
            aliases=(),
        ),
    ]
    result = MatchingEngine().match("#223 ნიკორა ჩასვი პარასკევზე", candidates_with_same_suffix)
    assert result.matched is True
    assert result.client_id == CLIENT_B


def test_ambiguous_short_code_without_name_requires_review() -> None:
    candidates_with_same_suffix = [
        MatchCandidate(
            client_id=CLIENT_A,
            code="00-00000223",
            display_name="გვირილა",
            normalized_name="გვირილა",
            aliases=(),
        ),
        MatchCandidate(
            client_id=CLIENT_B,
            code="00-00111223",
            display_name="ნიკორა",
            normalized_name="ნიკორა",
            aliases=(),
        ),
    ]
    result = MatchingEngine().match("#223 ჩასვი პარასკევზე", candidates_with_same_suffix)
    assert result.matched is False
    assert result.ambiguous is True


def test_plan_alias_beats_unrelated_1c_code_suffix() -> None:
    outlet = UUID("00000000-0000-0000-0000-000000000777")
    unrelated = UUID("00000000-0000-0000-0000-000000000778")
    result = MatchingEngine().match(
        "გვირილა 0203 სამშაბათზე და ხუთშაბათზე ჩასვი",
        [
            MatchCandidate(
                client_id=outlet,
                code="00-00008947",
                display_name="გვირილა",
                normalized_name="გვირილა",
                aliases=("გვირილა 0203", "გვირილა n0203"),
                one_c_ref="partner-gvirela-0203",
            ),
            MatchCandidate(
                client_id=unrelated,
                code="00-00000203",
                display_name="სხვა კლიენტი",
                normalized_name="სხვა კლიენტი",
                aliases=(),
                one_c_ref="partner-unrelated",
            ),
        ],
        preferred_one_c_refs={"partner-gvirela-0203"},
    )
    assert result.matched is True
    assert result.client_id == outlet
    assert result.method == MatchMethod.ALIAS


def test_typo_name_plus_outlet_number_matches_agent_plan_client() -> None:
    outlet = UUID("00000000-0000-0000-0000-000000000779")
    other = UUID("00000000-0000-0000-0000-000000000780")
    result = MatchingEngine().match(
        "გვრილა 203 პარასკევზე ჩასვი",
        [
            MatchCandidate(
                client_id=outlet,
                code="00-00008947",
                display_name="გვირილა",
                normalized_name="გვირილა",
                aliases=("გვირილა 0203", "გვირილა n0203"),
                one_c_ref="partner-gvirela-0203",
            ),
            MatchCandidate(
                client_id=other,
                code="00-00123456",
                display_name="სხვა 203",
                normalized_name="სხვა 203",
                aliases=("სხვა 0203",),
                one_c_ref="partner-other-0203",
            ),
        ],
        preferred_one_c_refs={"partner-gvirela-0203"},
    )
    assert result.matched is True
    assert result.client_id == outlet
    assert result.score is not None
    assert result.score >= 86.0


def test_numeric_only_outlet_can_use_unique_agent_plan_context() -> None:
    outlet = UUID("00000000-0000-0000-0000-000000000781")
    outside = UUID("00000000-0000-0000-0000-000000000782")
    result = MatchingEngine().match(
        "203 პარასკევზე ჩასვი",
        [
            MatchCandidate(
                client_id=outlet,
                code="00-00008947",
                display_name="გვირილა",
                normalized_name="გვირილა",
                aliases=("გვირილა 0203",),
                one_c_ref="partner-in-plan",
            ),
            MatchCandidate(
                client_id=outside,
                code="00-00007777",
                display_name="ნიკორა",
                normalized_name="ნიკორა",
                aliases=("ნიკორა 0203",),
                one_c_ref="partner-outside-plan",
            ),
        ],
        preferred_one_c_refs={"partner-in-plan"},
    )
    assert result.matched is True
    assert result.client_id == outlet


def test_same_outlet_number_in_same_plan_still_requires_name_or_review() -> None:
    one = UUID("00000000-0000-0000-0000-000000000783")
    two = UUID("00000000-0000-0000-0000-000000000784")
    result = MatchingEngine().match(
        "203 პარასკევზე ჩასვი",
        [
            MatchCandidate(
                client_id=one,
                code="00-00008881",
                display_name="გვირილა",
                normalized_name="გვირილა",
                aliases=("გვირილა 0203",),
                one_c_ref="partner-one",
            ),
            MatchCandidate(
                client_id=two,
                code="00-00008882",
                display_name="ნიკორა",
                normalized_name="ნიკორა",
                aliases=("ნიკორა 0203",),
                one_c_ref="partner-two",
            ),
        ],
        preferred_one_c_refs={"partner-one", "partner-two"},
    )
    assert result.matched is False
    assert result.ambiguous is True


def test_display_name_outlet_marker_matches_human_number_without_1c_code() -> None:
    deili = UUID("00000000-0000-0000-0000-000000000901")
    unrelated_code = UUID("00000000-0000-0000-0000-000000000902")
    result = MatchingEngine().match(
        "დეილი 158 ორშაბათს და სამშაბათს ჩასვი",
        [
            MatchCandidate(
                client_id=deili,
                code="00-00006931",
                display_name="დეილი N0158",
                normalized_name="დეილი n0158",
                aliases=(),
                one_c_ref="deili-158",
            ),
            MatchCandidate(
                client_id=unrelated_code,
                code="00-00000158",
                display_name="სხვა კლიენტი",
                normalized_name="სხვა კლიენტი",
                aliases=(),
                one_c_ref="other",
            ),
        ],
    )
    assert result.matched is True
    assert result.client_id == deili
    assert result.method != MatchMethod.CODE


def test_address_fragment_matches_plan_address() -> None:
    gvirela = UUID("00000000-0000-0000-0000-000000000903")
    other = UUID("00000000-0000-0000-0000-000000000904")
    result = MatchingEngine().match(
        "დიღმის მასივი კვარტალი 2 პარასკევზე ჩასვი",
        [
            MatchCandidate(
                client_id=gvirela,
                code="00-00008947",
                display_name="გვირილა",
                normalized_name="გვირილა",
                aliases=(
                    "გვირილა 0203",
                    "გვირილა n0203",
                    "addr გვირილა n0203 დიღმის მასივი კვარტალი 2 კორპუსი 13",
                ),
                one_c_ref="gvirela",
            ),
            MatchCandidate(
                client_id=other,
                code="00-00009999",
                display_name="სხვა",
                normalized_name="სხვა",
                aliases=("addr სხვა თბილისი რუსთაველის გამზირი 10",),
                one_c_ref="other",
            ),
        ],
        preferred_one_c_refs={"gvirela", "other"},
    )
    assert result.matched is True
    assert result.client_id == gvirela
    assert result.evidence is not None
    assert result.evidence.startswith("address:")


def test_address_with_building_number_does_not_become_outlet_number_match() -> None:
    one = UUID("00000000-0000-0000-0000-000000000905")
    two = UUID("00000000-0000-0000-0000-000000000906")
    result = MatchingEngine().match(
        "16 პარასკევზე ჩასვი",
        [
            MatchCandidate(
                client_id=one,
                code="00-00001111",
                display_name="ერთი",
                normalized_name="ერთი",
                aliases=("addr ერთი თბილისი ლიბანის n16",),
                one_c_ref="one",
            ),
            MatchCandidate(
                client_id=two,
                code="00-00002222",
                display_name="ორი",
                normalized_name="ორი",
                aliases=("addr ორი თბილისი ჭავჭავაძის 16",),
                one_c_ref="two",
            ),
        ],
        preferred_one_c_refs={"one", "two"},
    )
    assert result.matched is False
    assert result.ambiguous is True


def test_short_1c_suffix_never_overrides_unique_human_name() -> None:
    deili = UUID("00000000-0000-0000-0000-000000000907")
    code_suffix = UUID("00000000-0000-0000-0000-000000000908")
    result = MatchingEngine().match(
        "დეილი 158 პარასკევზე ჩასვი",
        [
            MatchCandidate(
                client_id=deili,
                code="00-00006931",
                display_name="დეილი",
                normalized_name="დეილი",
                aliases=(),
                one_c_ref="deili",
            ),
            MatchCandidate(
                client_id=code_suffix,
                code="00-00000158",
                display_name="სხვა",
                normalized_name="სხვა",
                aliases=(),
                one_c_ref="other",
            ),
        ],
    )
    assert result.matched is True
    assert result.client_id == deili
    assert result.method == MatchMethod.EXACT_NAME


def test_retail_letter_prefixed_branch_codes_remain_distinct() -> None:
    s278 = UUID("00000000-0000-0000-0000-000000001278")
    k278 = UUID("00000000-0000-0000-0000-000000002278")
    d278 = UUID("00000000-0000-0000-0000-000000003278")

    branch_candidates = [
        MatchCandidate(
            client_id=s278,
            code="00-00111278",
            display_name="რითეილ ჯგუფი *S278 - ბორჯომი, წაღვერი",
            normalized_name="რითეილ ჯგუფი s278 ბორჯომი წაღვერი",
            aliases=("რითეილ s278",),
            one_c_ref="retail-s278",
        ),
        MatchCandidate(
            client_id=k278,
            code="00-00222278",
            display_name="რითეილ ჯგუფი *K278 - თბილისი",
            normalized_name="რითეილ ჯგუფი k278 თბილისი",
            aliases=("რითეილ k278",),
            one_c_ref="retail-k278",
        ),
        MatchCandidate(
            client_id=d278,
            code="00-00333278",
            display_name="რითეილ ჯგუფი *D278 - ქუთაისი",
            normalized_name="რითეილ ჯგუფი d278 ქუთაისი",
            aliases=("რითეილ d278",),
            one_c_ref="retail-d278",
        ),
    ]

    result = MatchingEngine().match(
        "რითეილ S278 სამშაბათზე ჩასვი",
        branch_candidates,
    )

    assert result.matched is True
    assert result.client_id == s278
    assert result.ambiguous is False


def test_retail_n_and_hash_branch_codes_collapse_to_plain_number() -> None:
    outlet = UUID("00000000-0000-0000-0000-000000004278")
    branch_candidates = [
        MatchCandidate(
            client_id=outlet,
            code="00-00444278",
            display_name="რითეილ ჯგუფი N0278 - თბილისი",
            normalized_name="რითეილ ჯგუფი n0278 თბილისი",
            aliases=("რითეილ n0278", "რითეილ 278"),
            one_c_ref="retail-278",
        )
    ]

    for query in (
        "რითეილ N278 სამშაბათზე ჩასვი",
        "რითეილ #278 სამშაბათზე ჩასვი",
        "რითეილ 278 სამშაბათზე ჩასვი",
    ):
        result = MatchingEngine().match(query, branch_candidates)
        assert result.matched is True
        assert result.client_id == outlet


def test_technical_prefix_and_address_suffix_are_exact_identity() -> None:
    sogoi = UUID("00000000-0000-0000-0000-000000004101")
    ivo = UUID("00000000-0000-0000-0000-000000004102")
    pool = [
        MatchCandidate(
            client_id=sogoi,
            code="00-00004101",
            display_name="შმს 1სოგოი",
            normalized_name="შმს 1სოგოი",
            aliases=(),
        ),
        MatchCandidate(
            client_id=ivo,
            code="00-00004102",
            display_name="შმს ივო 87 * ქ.თბილისი, ვაჟა-ფშაველას 12",
            normalized_name="შმს ივო 87 ქ თბილისი ვაჟა ფშაველას 12",
            aliases=(),
        ),
    ]

    first = MatchingEngine().match("1სოგოი ორშაბათზე ჩასვი", pool)
    assert first.matched is True
    assert first.client_id == sogoi
    assert first.method == MatchMethod.EXACT_NAME

    second = MatchingEngine().match("ივო 87 ორშაბათზე ჩასვი", pool)
    assert second.matched is True
    assert second.client_id == ivo
