from __future__ import annotations

import json

from scripts.configure_sender import update_env_text


def _env_value(text: str, key: str) -> str:
    prefix = f"{key}="
    return next(line[len(prefix) :] for line in text.splitlines() if line.startswith(prefix))


def test_moves_fixed_sender_to_multi_map_and_adds_202() -> None:
    source = (
        'AGENT_SENDER_MAP={"id:+995555000999":"111","id:+995500000000":"402"}\n'
        'AGENT_MULTI_SENDER_MAP={"id:+995555000504":["403","504"]}\n'
    )

    result = update_env_text(
        source,
        sender="+995 598 89 11 30",
        agent_codes=["111", "112", "202", "402"],
    )

    fixed = json.loads(_env_value(result, "AGENT_SENDER_MAP"))
    multi = json.loads(_env_value(result, "AGENT_MULTI_SENDER_MAP"))
    assert "id:+995555000999" not in fixed
    assert fixed["id:+995500000000"] == "402"
    assert multi["id:+995555000999"] == ["111", "112", "202", "402"]
    assert _env_value(result, "MESSAGE_PROCESSING_TIMEOUT_SECONDS") == "1200"


def test_preserves_existing_sender_codes_and_is_idempotent() -> None:
    source = (
        'AGENT_SENDER_MAP={}\n'
        'AGENT_MULTI_SENDER_MAP={"id:995555000999":["403","504"]}\n'
    )
    first = update_env_text(
        source,
        sender="0555000999",
        agent_codes=["202", "403"],
    )
    second = update_env_text(
        first,
        sender="+995555000999",
        agent_codes=["202", "403"],
    )

    multi = json.loads(_env_value(second, "AGENT_MULTI_SENDER_MAP"))
    assert multi == {"id:+995555000999": ["403", "504", "202"]}
    assert second == first


def test_updated_mapping_authorizes_agent_202() -> None:
    from app.services.agent_resolver import AgentResolver

    result = update_env_text(
        "AGENT_SENDER_MAP={}\nAGENT_MULTI_SENDER_MAP={}\n",
        sender="+995555000999",
        agent_codes=["111", "202"],
    )
    multi = json.loads(_env_value(result, "AGENT_MULTI_SENDER_MAP"))
    resolution = AgentResolver(multi_sender_map=multi).resolve(
        raw_text="202 ზუკა პარასკევს ჩასვი",
        sender_id="+995555000999",
    )

    assert resolution.agent_code == "202"
    assert resolution.agent_description == "PS202"
    assert resolution.client_text == "ზუკა პარასკევს ჩასვი"
