from __future__ import annotations

from datetime import datetime, timezone

from amocrm_service.auto_sync import (
    DEFAULT_AUTO_SYNC_GROUPS,
    default_auto_sync_settings,
    mark_group_started,
    next_due_group,
    normalize_auto_sync_settings,
)


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


def test_disabled_auto_sync_returns_no_due_group():
    now = datetime.fromisoformat("2026-07-08T12:00:00+00:00")
    config = default_auto_sync_settings(enabled=False)

    assert next_due_group(config, now) is None


def test_enabled_never_run_group_is_due():
    now = datetime.fromisoformat("2026-07-08T12:00:00+00:00")
    config = normalize_auto_sync_settings({"enabled": True})

    due = next_due_group(config, now)

    assert due is not None
    assert due["name"] in {group["name"] for group in DEFAULT_AUTO_SYNC_GROUPS}
    assert due["entities"]


def test_enabled_recent_group_not_due():
    now = datetime.fromisoformat("2026-07-08T12:00:00+00:00")
    config = normalize_auto_sync_settings({
        "enabled": True,
        "groups": [
            {"name": "hot", "interval_minutes": 30, "entities": ["leads"]},
        ],
        "last_started_at": {
            "hot": "2026-07-08T11:58:00+00:00",
        },
    })

    assert next_due_group(config, now) is None


def test_normalize_enable_preserves_groups():
    config = normalize_auto_sync_settings({"enabled": True})

    assert config["enabled"] is True
    assert config["groups"], "группы должны заполниться дефолтными, а не остаться пустыми"
    assert [group["name"] for group in config["groups"]] == [
        group["name"] for group in DEFAULT_AUTO_SYNC_GROUPS
    ]
    assert all(group["entities"] for group in config["groups"])
    assert config["last_started_at"] == {}
    assert config["last_job_id"] == {}
    assert config["last_error"] is None


def _group(name: str) -> dict:
    return next(g for g in DEFAULT_AUTO_SYNC_GROUPS if g["name"] == name)


def test_hot_group_has_contacts_not_events():
    hot = _group("hot")
    directory = _group("directory")
    assert "contacts" in hot["entities"], "contacts должны быть в hot-группе"
    assert "leads" in hot["entities"]
    assert "events" not in hot["entities"], "events убраны из hot (тяжёлые, качаются реже)"
    assert "events" in directory["entities"], "events перенесены в directory (раз в 6 часов)"
