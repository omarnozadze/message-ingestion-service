import pytest

from app.services.agent_resolver import AgentResolutionError, AgentResolver


def test_resolves_agent_from_sender_id_mapping() -> None:
    resolver = AgentResolver(sender_map={"id:+995599123456": "111"})
    result = resolver.resolve(
        raw_text="გვირილა 0223 ჩასვი სამშაბათი ხუთშაბათზე",
        sender_id="+995 599 123 456",
    )
    assert result.agent_code == "111"
    assert result.agent_description == "PS111"
    assert result.source == "sender_id"


def test_normalizes_georgian_phone_variants() -> None:
    resolver = AgentResolver(sender_map={"id:+995 598 13 87 88": "117"})

    for sender_id in (
        "+995555000117",
        "995 598 13 87 88",
        "555000117",
        "0555000117",
    ):
        result = resolver.resolve(
            raw_text="გვირილა 0223 ჩასვი სამშაბათზე",
            sender_id=sender_id,
        )
        assert result.agent_code == "117"
        assert result.agent_description == "PS117"


def test_resolves_agent_from_sender_name_code() -> None:
    resolver = AgentResolver()
    result = resolver.resolve(
        raw_text="#223 ნიკორა ჩასვი პარასკევზე",
        sender_name="PS112",
    )
    assert result.agent_code == "112"
    assert result.source == "sender_name"



def test_arbitrary_sender_name_containing_three_digits_is_not_treated_as_agent() -> None:
    resolver = AgentResolver(allow_message_prefix=False)
    with pytest.raises(AgentResolutionError):
        resolver.resolve(
            raw_text="#223 ნიკორა ჩასვი პარასკევზე",
            sender_name="რითეილ ჯგუფი * S224 - თბილისი, ლიბანის N16",
        )

def test_legacy_message_prefix_is_stripped_from_client_text_without_sender_metadata() -> None:
    resolver = AgentResolver()
    result = resolver.resolve(raw_text="111 გვირილა 0223 ხუთშაბათზე")
    assert result.agent_code == "111"
    assert result.client_text == "გვირილა 0223 ხუთშაბათზე"


def test_unknown_sender_cannot_impersonate_agent_via_message_prefix() -> None:
    resolver = AgentResolver(sender_map={"id:+995555000111": "111"})
    with pytest.raises(AgentResolutionError, match="unknown sender_id"):
        resolver.resolve(
            raw_text="111 გვირილა 0223 ხუთშაბათზე",
            sender_id="+995555000000",
        )



def test_fixed_sender_allows_leading_three_digit_client_code_when_not_known_agent() -> None:
    resolver = AgentResolver(
        sender_map={
            "id:+995555000111": "111",
            "id:+995555000112": "112",
        }
    )
    result = resolver.resolve(
        raw_text="223 ნიკორა ჩასვი პარასკევზე",
        sender_id="+995555000111",
    )
    assert result.agent_code == "111"
    assert result.client_text == "223 ნიკორა ჩასვი პარასკევზე"

def test_fixed_sender_conflicting_message_agent_requires_review() -> None:
    resolver = AgentResolver(
        sender_map={
            "id:+995555000111": "111",
            "id:+995555000112": "112",
        }
    )
    with pytest.raises(AgentResolutionError, match="agent_conflict"):
        resolver.resolve(
            raw_text="112 გვირილა 0223 ხუთშაბათზე",
            sender_id="+995555000111",
        )


def test_multi_agent_sender_routes_to_504_from_message_prefix() -> None:
    resolver = AgentResolver(
        multi_sender_map={"id:+995555000504": ["403", "504"]}
    )
    result = resolver.resolve(
        raw_text="504 #223 ნიკორა ჩასვი პარასკევზე",
        sender_id="+995 579 19 66 50",
    )
    assert result.agent_code == "504"
    assert result.agent_description == "PS504"
    assert result.source == "sender_id_message_prefix"
    assert result.client_text == "#223 ნიკორა ჩასვი პარასკევზე"


def test_multi_agent_sender_routes_to_403_from_message_prefix() -> None:
    resolver = AgentResolver(
        multi_sender_map={"id:+995555000504": ["403", "504"]}
    )
    result = resolver.resolve(
        raw_text="403 გვირილა 0223 ჩასვი სამშაბათზე",
        sender_id="995555000504",
    )
    assert result.agent_code == "403"
    assert result.client_text == "გვირილა 0223 ჩასვი სამშაბათზე"


def test_multi_agent_sender_requires_explicit_agent() -> None:
    resolver = AgentResolver(
        multi_sender_map={"id:+995555000504": ["403", "504"]}
    )
    with pytest.raises(AgentResolutionError, match="agent_required"):
        resolver.resolve(
            raw_text="#223 ნიკორა ჩასვი პარასკევზე",
            sender_id="+995555000504",
        )


def test_multi_agent_sender_rejects_agent_outside_allowed_set() -> None:
    resolver = AgentResolver(
        multi_sender_map={"id:+995555000504": ["403", "504"]}
    )
    with pytest.raises(AgentResolutionError, match="agent_not_allowed"):
        resolver.resolve(
            raw_text="111 #223 ნიკორა ჩასვი პარასკევზე",
            sender_id="+995555000504",
        )


def test_unknown_sender_requires_review() -> None:
    resolver = AgentResolver(allow_message_prefix=False)
    with pytest.raises(AgentResolutionError):
        resolver.resolve(raw_text="გვირილა 0223 ხუთშაბათზე", sender_name="გიორგი")
