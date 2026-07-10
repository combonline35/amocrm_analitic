from __future__ import annotations

from datetime import datetime, timezone

from amocrm_service.db import connect, init_db
from amocrm_service.quality import QualityService
from amocrm_service.repository import Repository


def _ts(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def test_quality_summary_flags_core_sales_hygiene_risks(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    repo = Repository(conn)
    try:
        now = datetime.fromisoformat("2026-07-08T12:00:00+00:00")
        repo.upsert_entities("users", [{"id": 7, "name": "Max"}])
        repo.upsert_entities("pipelines", [{
            "id": 11,
            "name": "Sales",
            "_embedded": {
                "statuses": [
                    {"id": 101, "name": "New"},
                    {"id": 142, "name": "Won"},
                    {"id": 143, "name": "Lost"},
                ],
            },
        }])
        repo.upsert_entities("leads", [
            {
                "id": 1,
                "name": "Needs call",
                "responsible_user_id": 7,
                "pipeline_id": 11,
                "status_id": 101,
                "updated_at": _ts("2026-07-08T10:00:00"),
            },
            {
                "id": 2,
                "name": "Silent lead",
                "responsible_user_id": 7,
                "pipeline_id": 11,
                "status_id": 101,
                "updated_at": _ts("2026-07-01T10:00:00"),
            },
            {
                "id": 3,
                "name": "Won lead",
                "responsible_user_id": 7,
                "pipeline_id": 11,
                "status_id": 142,
                "updated_at": _ts("2026-07-08T10:00:00"),
            },
        ])
        repo.upsert_entities("tasks", [
            {
                "id": 101,
                "text": "Call customer",
                "entity_type": "leads",
                "entity_id": 1,
                "responsible_user_id": 7,
                "is_completed": False,
                "complete_till": _ts("2026-07-07T10:00:00"),
                "updated_at": _ts("2026-07-07T09:00:00"),
            },
            {
                "id": 102,
                "text": "Closed task",
                "entity_type": "leads",
                "entity_id": 2,
                "responsible_user_id": 7,
                "is_completed": True,
                "complete_till": _ts("2026-07-02T10:00:00"),
                "updated_at": _ts("2026-07-02T11:00:00"),
            },
        ])

        summary = QualityService(repo).summary(now=now, stale_lead_days=3)

        assert summary["health_score"] == 71
        assert summary["totals"]["open_leads"] == 2
        assert summary["totals"]["risks"] == 3
        assert summary["totals"]["critical"] == 2
        assert summary["totals"]["overdue_tasks"] == 1
        assert summary["totals"]["leads_without_open_task"] == 1
        assert summary["totals"]["stale_leads"] == 1
        assert summary["by_user"][0]["responsible_user_name"] == "Max"
        assert {risk["type"] for risk in summary["risks"]} == {
            "overdue_task",
            "lead_without_open_task",
            "stale_lead",
        }
        assert all(risk["lead_name"] != "Won lead" for risk in summary["risks"])
    finally:
        conn.close()


def test_quality_summary_respects_pipeline_filter(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    repo = Repository(conn)
    try:
        now = datetime.fromisoformat("2026-07-08T12:00:00+00:00")
        repo.upsert_entities("users", [{"id": 7, "name": "Max"}])
        repo.upsert_entities("pipelines", [
            {"id": 11, "name": "Included", "_embedded": {"statuses": [{"id": 101, "name": "New"}]}},
            {"id": 22, "name": "Ignored", "_embedded": {"statuses": [{"id": 201, "name": "New"}]}},
        ])
        repo.upsert_entities("leads", [
            {
                "id": 1,
                "name": "Included lead",
                "responsible_user_id": 7,
                "pipeline_id": 11,
                "status_id": 101,
                "updated_at": _ts("2026-07-01T10:00:00"),
            },
            {
                "id": 2,
                "name": "Ignored lead",
                "responsible_user_id": 7,
                "pipeline_id": 22,
                "status_id": 201,
                "updated_at": _ts("2026-07-01T10:00:00"),
            },
        ])

        summary = QualityService(repo).summary(
            now=now,
            settings={
                "stale_lead_days": 3,
                "max_risks": 20,
                "filters": {
                    "pipeline_ids": [11],
                    "status_ids": [],
                    "ignored_status_ids": [142, 143],
                    "responsible_user_ids": [],
                },
                "rules": {
                    "overdue_tasks": True,
                    "missing_next_task": True,
                    "stale_leads": True,
                },
            },
        )

        assert summary["totals"]["open_leads"] == 1
        assert {risk["lead_name"] for risk in summary["risks"]} == {"Included lead"}
    finally:
        conn.close()
