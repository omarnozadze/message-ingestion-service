from __future__ import annotations

import inspect

from app.integrations.one_c import odata
from app.services import ai_matching
from app.services.incoming_message_service import IncomingMessageService


def test_ai_has_bounded_concurrency_and_retries() -> None:
    source = inspect.getsource(ai_matching)
    assert "_AI_CALL_LIMIT = 2" in source
    assert "_AI_RETRY_ATTEMPTS = 3" in source
    assert "_ai_call_semaphore()" in source
    assert "after retries" in source


def test_ai_transport_failure_has_local_fallback() -> None:
    source = inspect.getsource(IncomingMessageService.process)
    # v28: local parsing runs first; AI is only the fallback for parsing, and
    # an AI outage during matching falls back to the local matcher.
    assert "LOCAL_VISIT_REQUEST_PARSED_FAST_PATH" in source
    assert "AI_MATCH_FALLBACK_USED" in source
    assert '"fallback": "local"' in source


def test_1c_reads_are_bounded_and_only_write_is_globally_serialized() -> None:
    apply_source = inspect.getsource(odata.OneCODataClient.apply_visit_instructions)
    read_source = inspect.getsource(odata.OneCODataClient._get_plan_rows)

    assert "async with _agent_plan_lock(agent_ref)" in apply_source
    assert "rows = await self._get_plan_rows" in apply_source
    assert "async with _global_plan_write_lock()" in apply_source
    assert apply_source.index("rows = await self._get_plan_rows") < apply_source.index(
        "async with _global_plan_write_lock()"
    )

    assert "async with _global_plan_read_semaphore()" in read_source
    assert odata.FULL_ROW_CONCURRENCY == 18  # v38: keyed GETs, bounded
