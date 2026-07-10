from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from amocrm_service.repository import Repository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _day_bounds(value: str | None = None) -> tuple[datetime, datetime, str]:
    if value:
        try:
            day = datetime.fromisoformat(value).date()
        except ValueError:
            day = _utc_now().date()
    else:
        day = _utc_now().date()
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1), day.isoformat()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class KpiService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def rebuild_daily(self, target_date: str | None = None) -> dict[str, Any]:
        day_start, day_end, day_label = _day_bounds(target_date)
        rows = self._build_daily_rows(day_start, day_end)
        saved = self.repository.replace_lead_kpi_daily(day_label, rows)
        return {
            "date": day_label,
            "saved": saved,
            "totals": self._totals(rows),
        }

    def daily(self, target_date: str | None = None, limit: int = 500) -> dict[str, Any]:
        _day_start, _day_end, day_label = _day_bounds(target_date)
        rows = self.repository.lead_kpi_daily(day_label, limit=limit)
        totals = self.repository.lead_kpi_daily_totals(day_label)
        return {
            "date": day_label,
            "source": "mart" if totals["rows_count"] else "empty",
            "totals": totals,
            "rows": rows,
        }

    def _build_daily_rows(self, day_start: datetime, day_end: datetime) -> list[dict[str, Any]]:
        users = self._users_by_id()
        pipeline_names, status_names = self._pipeline_maps()
        rows = []
        for source in self.repository.lead_kpi_source_rows(int(day_start.timestamp()), int(day_end.timestamp())):
            user_id = source.get("user_id")
            pipeline_id = _int(source.get("pipeline_id"))
            status_id = _int(source.get("status_id"))
            rows.append({
                "user_id": None if user_id in (None, "") else str(user_id),
                "user_name": users.get(str(user_id), f"User {user_id}" if user_id else "System"),
                "pipeline_id": pipeline_id,
                "pipeline_name": pipeline_names.get(pipeline_id, f"Pipeline {pipeline_id}" if pipeline_id else ""),
                "status_id": status_id,
                "status_name": status_names.get((pipeline_id, status_id), f"Status {status_id}" if status_id else ""),
                "created_count": _int(source.get("created_count")),
                "updated_count": _int(source.get("updated_count")),
                "closed_count": _int(source.get("closed_count")),
                "won_count": _int(source.get("won_count")),
                "lost_count": _int(source.get("lost_count")),
                "open_count": _int(source.get("open_count")),
                "created_price": _int(source.get("created_price")),
                "closed_price": _int(source.get("closed_price")),
                "open_price": _int(source.get("open_price")),
            })
        rows.sort(
            key=lambda item: (
                _int(item["created_count"]),
                _int(item["updated_count"]),
                _int(item["open_count"]),
                item["user_name"],
            ),
            reverse=True,
        )
        return rows

    def _users_by_id(self) -> dict[str, str]:
        users: dict[str, str] = {}
        for item in self.repository.all_payloads("users"):
            user_id = item.get("id")
            if user_id is None:
                continue
            users[str(user_id)] = str(item.get("name") or item.get("email") or f"User {user_id}")
        return users

    def _pipeline_maps(self) -> tuple[dict[int, str], dict[tuple[int, int], str]]:
        pipeline_names: dict[int, str] = {}
        status_names: dict[tuple[int, int], str] = {}
        for pipeline in self.repository.all_payloads("pipelines"):
            pipeline_id = _int(pipeline.get("id"))
            if not pipeline_id:
                continue
            pipeline_names[pipeline_id] = str(pipeline.get("name") or f"Pipeline {pipeline_id}")
            statuses = pipeline.get("_embedded", {}).get("statuses", [])
            for status in statuses:
                status_id = _int(status.get("id"))
                if status_id:
                    status_names[(pipeline_id, status_id)] = str(status.get("name") or f"Status {status_id}")
        return pipeline_names, status_names

    def _totals(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        metrics = (
            "created_count",
            "updated_count",
            "closed_count",
            "won_count",
            "lost_count",
            "open_count",
            "created_price",
            "closed_price",
            "open_price",
        )
        totals = {metric: sum(_int(row.get(metric)) for row in rows) for metric in metrics}
        totals["rows_count"] = len(rows)
        totals["active_users"] = len({
            str(row.get("user_key") or row.get("user_id") or row.get("user_name"))
            for row in rows
            if any(_int(row.get(metric)) for metric in ("created_count", "updated_count", "closed_count", "open_count"))
        })
        return totals
