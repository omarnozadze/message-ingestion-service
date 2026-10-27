"""Plan-row loading: light keys first, then every full row by key (v38 design)."""
from __future__ import annotations

import pytest

from app.integrations.one_c.odata import OneCODataClient


class _Response:
    def __init__(self, data) -> None:
        self._data = data
        self.status_code = 200
        self.is_success = True
        self.text = ""

    def json(self):
        return self._data


class _FakeHttp:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def get(self, url: str, **_kwargs):
        self.urls.append(url)
        line = int(url.split("LineNumber=")[1].split(")")[0])
        return _Response({"LineNumber": str(line), "Партнер_Key": f"p{line}", "Д1": True})


def make_client() -> OneCODataClient:
    return OneCODataClient(
        base_url="http://1c-server/test/odata/standard.odata",
        username="u",
        password="p",
    )


@pytest.mark.asyncio
async def test_plan_rows_are_loaded_by_key_in_line_order(monkeypatch) -> None:
    client = make_client()
    http = _FakeHttp()

    async def keys(_http, _plan_ref):
        return [{"LineNumber": "3"}, {"LineNumber": "1"}, {"LineNumber": "2"}]

    monkeypatch.setattr(client, "_get_all_plan_row_keys", keys)

    rows = await client._get_plan_rows(http, "plan")  # type: ignore[arg-type]

    assert [row["LineNumber"] for row in rows] == ["1", "2", "3"]
    assert len(http.urls) == 3


@pytest.mark.asyncio
async def test_invalid_line_number_requires_review(monkeypatch) -> None:
    from app.integrations.one_c.odata import OneCPlanReviewRequired

    client = make_client()

    async def keys(_http, _plan_ref):
        return [{"LineNumber": "0"}]

    monkeypatch.setattr(client, "_get_all_plan_row_keys", keys)

    with pytest.raises(OneCPlanReviewRequired):
        await client._get_plan_rows(_FakeHttp(), "plan")  # type: ignore[arg-type]
