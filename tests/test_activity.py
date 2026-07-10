from __future__ import annotations

from datetime import datetime, timezone

from amocrm_service.activity import ActivityService
from amocrm_service.db import connect, init_db
from amocrm_service.repository import Repository


def test_activity_dashboard_combines_crm_sources(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    repo = Repository(conn)
    try:
        now = int(datetime.now(timezone.utc).timestamp())

        repo.upsert_entities("users", [{"id": 7, "name": "Max"}])
        repo.upsert_entities("events", [{
            "id": 101,
            "type": "lead_status_changed",
            "entity_type": "leads",
            "entity_id": 55,
            "created_by": 7,
            "created_at": now,
        }])
        repo.upsert_entities("tasks", [{
            "id": 102,
            "text": "Call customer",
            "entity_type": "leads",
            "entity_id": 55,
            "responsible_user_id": 7,
            "is_completed": True,
            "updated_at": now,
        }])
        repo.upsert_entities("lead_notes", [{
            "id": 103,
            "entity_id": 55,
            "created_by": 7,
            "created_at": now,
            "note_type": "common",
            "params": {"text": "Customer asked for discount"},
        }])
        repo.add_webhook_event(
            "demo",
            "update_lead",
            {"leads[update][0][modified_user_id]": "7"},
            entity_type="leads",
            entity_id="55",
        )

        dashboard = ActivityService(repo).dashboard(days=1, limit=10)
        mart = ActivityService(repo).rebuild_marts_for_day(dashboard["target_date"])
        mart_dashboard = ActivityService(repo).dashboard(days=1, limit=10, target_date=dashboard["target_date"])

        assert dashboard["totals"]["activities"] == 4
        assert dashboard["totals"]["active_users"] == 1
        assert dashboard["totals"]["activity_score"] == 11
        assert dashboard["by_user"][0] == {"user_name": "Max", "count": 4, "score": 11}
        assert dashboard["pulse"]["totals"]["activity_count"] == 3
        assert dashboard["pulse"]["totals"]["activity_score"] == 11
        assert dashboard["pulse"]["users"][0]["user_name"] == "Max"
        assert dashboard["pulse"]["users"][0]["active_minutes"] == 15
        assert dashboard["pulse"]["users"][0]["tasks_completed"] == 1
        assert {item["source"] for item in dashboard["timeline"]} == {"amo_event", "task", "note", "webhook"}
        assert mart["saved"]["daily_rows"] == 1
        assert mart["saved"]["slot_rows"] == 96
        assert mart_dashboard["pulse_source"] == "mart"
        assert mart_dashboard["pulse"]["totals"]["activity_score"] == 11
        assert mart_dashboard["pulse"]["users"][0]["user_name"] == "Max"
    finally:
        conn.close()
