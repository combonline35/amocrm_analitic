from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from amocrm_service.repository import Repository


FRESHNESS_TARGETS = {
    "leads": {"label": "Сделки", "max_age_minutes": 45, "critical_age_minutes": 180},
    "tasks": {"label": "Задачи", "max_age_minutes": 45, "critical_age_minutes": 180},
    "events": {"label": "События", "max_age_minutes": 60, "critical_age_minutes": 240},
    "lead_notes": {"label": "Примечания сделок", "max_age_minutes": 120, "critical_age_minutes": 360},
    "contacts": {"label": "Контакты", "max_age_minutes": 240, "critical_age_minutes": 720},
    "companies": {"label": "Компании", "max_age_minutes": 240, "critical_age_minutes": 720},
    "pipelines": {"label": "Воронки", "max_age_minutes": 720, "critical_age_minutes": 1440},
    "users": {"label": "Пользователи", "max_age_minutes": 720, "critical_age_minutes": 1440},
    "lead_custom_fields": {"label": "Поля сделок", "max_age_minutes": 720, "critical_age_minutes": 1440},
}


class FreshnessService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def dashboard(self, account_key: str, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        overview = {
            str(row["entity_type"]): dict(row)
            for row in self.repository.hub_entity_overview()
        }
        entities = []
        for entity_type, target in FRESHNESS_TARGETS.items():
            row = overview.get(entity_type) or {}
            last_synced_at = row.get("last_synced_at")
            age_minutes = self._age_minutes(last_synced_at, now)
            status = self._status(age_minutes, target["max_age_minutes"], target["critical_age_minutes"])
            entities.append({
                "entity_type": entity_type,
                "label": target["label"],
                "items_count": int(row.get("items_count") or 0),
                "last_synced_at": last_synced_at,
                "age_minutes": age_minutes,
                "max_age_minutes": target["max_age_minutes"],
                "critical_age_minutes": target["critical_age_minutes"],
                "status": status,
            })
        status_counts: dict[str, int] = {}
        for row in entities:
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        latest_jobs = self.repository.latest_sync_jobs(account_key, 10)
        active_jobs = [job for job in latest_jobs if job.get("status") in {"pending", "running"}]
        health = "ok"
        if status_counts.get("missing") or status_counts.get("critical"):
            health = "critical"
        elif status_counts.get("stale") or active_jobs:
            health = "syncing" if active_jobs else "stale"
        return {
            "generated_at": now.isoformat(),
            "health": health,
            "status_counts": status_counts,
            "entities": entities,
            "active_jobs": active_jobs,
            "latest_jobs": latest_jobs,
            "queue": self.repository.queue_status_counts(account_key),
        }

    def _age_minutes(self, value: Any, now: datetime) -> int | None:
        parsed = self._parse_dt(value)
        if not parsed:
            return None
        return max(int((now - parsed).total_seconds() // 60), 0)

    def _status(self, age_minutes: int | None, max_age_minutes: int, critical_age_minutes: int) -> str:
        if age_minutes is None:
            return "missing"
        if age_minutes >= critical_age_minutes:
            return "critical"
        if age_minutes >= max_age_minutes:
            return "stale"
        return "fresh"

    def _parse_dt(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
