from __future__ import annotations

from datetime import datetime, timezone

from amocrm_service.db import connect, init_db
from amocrm_service.kpi import KpiService
from amocrm_service.repository import Repository


def _ts(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def test_kpi_daily_mart_groups_leads_by_user_pipeline_status(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    repo = Repository(conn)
    try:
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
                "name": "Open lead",
                "responsible_user_id": 7,
                "pipeline_id": 11,
                "status_id": 101,
                "price": 1000,
                "created_at": _ts("2026-06-27T09:00:00"),
                "updated_at": _ts("2026-06-27T10:00:00"),
            },
            {
                "id": 2,
                "name": "Won lead",
                "responsible_user_id": 7,
                "pipeline_id": 11,
                "status_id": 142,
                "price": 2000,
                "created_at": _ts("2026-06-26T09:00:00"),
                "updated_at": _ts("2026-06-27T12:00:00"),
                "closed_at": _ts("2026-06-27T12:30:00"),
            },
            {
                "id": 3,
                "name": "Old open lead",
                "responsible_user_id": 7,
                "pipeline_id": 11,
                "status_id": 101,
                "price": 3000,
                "created_at": _ts("2026-06-20T09:00:00"),
                "updated_at": _ts("2026-06-20T10:00:00"),
            },
        ])

        rebuilt = KpiService(repo).rebuild_daily("2026-06-27")
        daily = KpiService(repo).daily("2026-06-27")

        assert rebuilt["saved"]["rows"] == 2
        assert daily["source"] == "mart"
        assert daily["totals"]["created_count"] == 1
        assert daily["totals"]["updated_count"] == 2
        assert daily["totals"]["closed_count"] == 1
        assert daily["totals"]["won_count"] == 1
        assert daily["totals"]["open_count"] == 2
        assert daily["totals"]["created_price"] == 1000
        assert daily["totals"]["closed_price"] == 2000
        assert daily["totals"]["open_price"] == 4000
        assert {row["status_name"] for row in daily["rows"]} == {"New", "Won"}
    finally:
        conn.close()
