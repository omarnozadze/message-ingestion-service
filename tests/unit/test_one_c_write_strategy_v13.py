from __future__ import annotations

import inspect

from app.integrations.one_c.odata import (
    OneCODataClient,
    OneCPlanReviewRequired,
    _is_retryable_plan_conflict,
)


def test_write_strategy_patches_then_posts_and_never_unposts() -> None:
    source = inspect.getsource(OneCODataClient._patch_plan_rows_batch)

    assert source.index("client.patch(") < source.index("/Post?PostingModeOperational=false")
    assert "/Unpost" not in source


def test_apply_is_serialized_per_agent_and_retries_once() -> None:
    source = inspect.getsource(OneCODataClient.apply_visit_instructions)

    assert "async with _agent_plan_lock(agent_ref)" in source
    assert "for attempt in range(2)" in source
    assert "_is_retryable_plan_conflict(exc)" in source


def test_only_data_version_conflicts_are_auto_retried() -> None:
    assert _is_retryable_plan_conflict(
        OneCPlanReviewRequired(
            "1C document changed while visit-plan rows were being read; retry/review is required"
        )
    )
    assert _is_retryable_plan_conflict(
        OneCPlanReviewRequired(
            "1C document changed after it was read; retry/review is required"
        )
    )
    assert not _is_retryable_plan_conflict(
        OneCPlanReviewRequired("client to remove is not present in active agent plan")
    )
