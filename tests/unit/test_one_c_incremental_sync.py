from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.core.config import settings
from app.integrations.one_c.odata import OneCODataClient
from app.services.one_c_sync import OneCSearchIndexSync, _looks_like_address


def make_client() -> OneCODataClient:
    return OneCODataClient(
        base_url="http://1c-server/test/odata/standard.odata",
        username="u",
        password="p",
    )


@pytest.mark.asyncio
async def test_list_partners_incremental_filter_uses_created_and_edited_dates(monkeypatch) -> None:
    client = make_client()
    captured: list[str] = []

    async def fake_get_value(_http_client, _entity, *, params):
        captured.append(params["$filter"])
        return []

    monkeypatch.setattr(client, "_get_value", fake_get_value)

    since = datetime(2026, 10, 7, 10, 25, tzinfo=ZoneInfo("Asia/Tbilisi"))
    result = await client.list_partners(modified_since=since)

    assert result == []
    assert len(captured) == 1
    assert "DeletionMark eq false" in captured[0]
    assert "Клиент eq true" in captured[0]
    assert "ИБР_ДатаРедактирования ge datetime'2026-10-07T10:25:00'" in captured[0]
    assert "ИБР_ДатаСоздания ge datetime'2026-10-07T10:25:00'" in captured[0]


def test_full_sync_is_required_without_persistent_state() -> None:
    service = OneCSearchIndexSync()
    now = datetime(2026, 10, 7, 10, 0, tzinfo=ZoneInfo("Asia/Tbilisi"))
    assert service._full_sync_due(None, now) is True


def test_full_sync_runs_once_per_local_day_after_configured_hour(monkeypatch) -> None:
    service = OneCSearchIndexSync()
    monkeypatch.setattr(settings, "one_c_full_sync_hour", 3)
    tz = ZoneInfo("Asia/Tbilisi")
    state = SimpleNamespace(
        cursor_at=datetime(2026, 10, 6, 12, 0, tzinfo=tz),
        last_full_at=datetime(2026, 10, 6, 3, 10, tzinfo=tz),
    )

    assert service._full_sync_due(state, datetime(2026, 10, 7, 2, 59, tzinfo=tz)) is False
    assert service._full_sync_due(state, datetime(2026, 10, 7, 3, 1, tzinfo=tz)) is True

    state.last_full_at = datetime(2026, 10, 7, 3, 1, tzinfo=tz)
    assert service._full_sync_due(state, datetime(2026, 10, 7, 12, 0, tzinfo=tz)) is False


@pytest.mark.asyncio
async def test_incremental_contact_lookup_batches_changed_partner_refs(monkeypatch) -> None:
    service = OneCSearchIndexSync()
    calls: list[str | None] = []

    async def fake_page_set(_client, *, filter_text):
        calls.append(filter_text)
        return []

    monkeypatch.setattr(service, "_load_contact_page_set", fake_page_set)
    refs = {f"00000000-0000-0000-0000-{i:012d}" for i in range(26)}

    rows = await service._load_contact_rows(object(), refs)

    assert rows == []
    assert len(calls) == 2
    assert calls[0] is not None and calls[0].count("Ref_Key eq guid'") == 25
    assert calls[1] is not None and calls[1].count("Ref_Key eq guid'") == 1


@pytest.mark.asyncio
async def test_empty_incremental_partner_set_skips_contact_query(monkeypatch) -> None:
    service = OneCSearchIndexSync()
    called = False

    async def fake_load(*_args, **_kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(service, "_load_contact_rows", fake_load)
    created = await service._sync_address_aliases(set())

    assert created == 0
    assert called is False


def test_address_detection_rejects_phone_and_accepts_geography() -> None:
    assert _looks_like_address(
        {
            "Представление": "თბილისი, კახეთის გზატკეცილი 17",
            "Страна": "საქართველო",
        }
    ) is True
    assert _looks_like_address(
        {
            "Представление": "+995555123456",
            "НомерТелефона": "+995555123456",
        }
    ) is False
