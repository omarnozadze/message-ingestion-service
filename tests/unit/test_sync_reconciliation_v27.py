import json
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.one_c_sync import OneCSyncReport


def test_sync_report_contains_reconciliation_fields() -> None:
    now = datetime(2026, 10, 13, 17, 0, tzinfo=ZoneInfo("Asia/Tbilisi"))
    report = OneCSyncReport(
        mode="incremental",
        partners_fetched=20,
        clients_created=2,
        clients_updated=15,
        clients_deactivated=1,
        clients_excluded=2,
        duplicate_groups=1,
        duplicate_clients=2,
        duplicate_examples=(
            {"kind": "branch_identity", "key": "რითეილ ჯგუფი|s278"},
        ),
        address_aliases_created=3,
        plan_aliases_created=4,
        plan_rows_seen=100,
        started_at=now,
        finished_at=now,
    )
    payload = asdict(report)
    assert payload["clients_excluded"] == 2
    assert payload["duplicate_groups"] == 1
    assert payload["duplicate_clients"] == 2
