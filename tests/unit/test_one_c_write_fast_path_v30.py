"""Parent-document write workflow (v39/v41 rules).

* a posted document is always Posted again after PATCH (exactly once);
* a timed-out Post is never retried and never followed by Unpost.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from app.integrations.one_c.odata import AgentPlan, OneCODataClient, OneCPlanReviewRequired


class _Response:
    def __init__(self, *, status_code: int = 200) -> None:
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        self.text = ""


class _FakeClient:
    def __init__(self, *, post_timeout: bool = False) -> None:
        self.patch_calls: list[str] = []
        self.post_calls: list[str] = []
        self.post_timeout = post_timeout

    async def patch(self, url: str, **kwargs):
        self.patch_calls.append(url)
        return _Response()

    async def post(self, url: str, **kwargs):
        self.post_calls.append(url)
        if self.post_timeout:
            raise httpx.ReadTimeout("post timeout")
        return _Response()


def _plan() -> AgentPlan:
    return AgentPlan(
        ref_key="11111111-1111-1111-1111-111111111111",
        number="TEST",
        date=datetime(2026, 10, 15),
        data_version="v1",
        posted=True,
        cyclic=False,
        cyclic_end_date=None,
    )


def _one_c(monkeypatch) -> OneCODataClient:
    one_c = OneCODataClient(
        base_url="http://1c.test/odata/standard.odata",
        username="u",
        password="p",
    )
    monkeypatch.setattr(
        one_c,
        "_get_plan_state",
        AsyncMock(
            side_effect=[
                {"Posted": True, "DataVersion": "v1"},
                {"Posted": True, "DataVersion": "v2"},
            ]
        ),
    )
    monkeypatch.setattr(one_c, "_changes_are_currently_satisfied", AsyncMock(return_value=True))
    return one_c


@pytest.mark.asyncio
async def test_posted_document_is_patched_then_posted_exactly_once(monkeypatch) -> None:
    one_c = _one_c(monkeypatch)
    client = _FakeClient()

    timings = await one_c._patch_plan_rows_batch(client, _plan(), [], changes=(object(),))

    assert len(client.patch_calls) == 1
    assert len(client.post_calls) == 1
    assert "/Post?PostingModeOperational=false" in client.post_calls[0]
    assert "post" in timings


@pytest.mark.asyncio
async def test_post_timeout_requires_review_without_retry_or_unpost(monkeypatch) -> None:
    one_c = _one_c(monkeypatch)
    client = _FakeClient(post_timeout=True)

    with pytest.raises(OneCPlanReviewRequired):
        await one_c._patch_plan_rows_batch(client, _plan(), [], changes=(object(),))

    assert len(client.post_calls) == 1
    assert not any("Unpost" in url for url in client.post_calls)


@pytest.mark.asyncio
async def test_changed_data_version_is_rejected_before_patch(monkeypatch) -> None:
    one_c = _one_c(monkeypatch)
    one_c._get_plan_state = AsyncMock(return_value={"Posted": True, "DataVersion": "other"})
    client = _FakeClient()

    with pytest.raises(OneCPlanReviewRequired):
        await one_c._patch_plan_rows_batch(client, _plan(), [], changes=(object(),))

    assert client.patch_calls == []
