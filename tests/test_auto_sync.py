from __future__ import annotations

from datetime import datetime, timezone

from amocrm_service.auto_sync import mark_group_started, next_due_group, normalize_auto_sync_settings


def test_auto_sync_selects_due_group_and_marks_started():
    now = datetime.fromisoformat("2026-07-08T12:00:00+00:00")
    config = normalize_auto_sync_settings({
        "enabled": True,
        "groups": [
            {"name": "hot", "interval_minutes": 30, "entities": ["leads", "tasks"]},
            {"name": "slow", "interval_minutes": 720, "entities": ["pipelines"]},
        ],
        "last_started_at": {
            "hot": "2026-07-08T11:00:00+00:00",
            "slow": "2026-07-08T11:59:00+00:00",
        },
    })

    due = next_due_group(config, now)
    updated = mark_group_started(config, str(due["name"]), job_id=42, now=now)

    assert due["name"] == "hot"
    assert due["entities"] == ["leads", "tasks"]
    assert updated["last_job_id"]["hot"] == 42
    assert updated["last_started_at"]["hot"] == "2026-07-08T12:00:00+00:00"
