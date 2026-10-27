from pathlib import Path


def test_incoming_message_has_local_parse_fast_path() -> None:
    source = Path("app/services/incoming_message_service.py").read_text(encoding="utf-8")
    assert "LOCAL_VISIT_REQUEST_PARSED_FAST_PATH" in source
    assert "AI_MATCH_SKIPPED_FAST_PATH" in source
    assert "plan_candidates = [" in source
    assert "timeout=25.0" in source


def test_whatsapp_bridge_polls_once_per_second_by_default() -> None:
    source = Path("whatsapp-bridge/src/index.js").read_text(encoding="utf-8")
    assert "STATUS_POLL_INTERVAL_MS || '1000'" in source
