from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DEFAULT_AUTO_SYNC_GROUPS = [
    {
        "name": "hot",
        "label": "Горячие данные",
        "interval_minutes": 15,
        "entities": ["leads", "tasks", "contacts"],
    },
    {
        "name": "communications",
        "label": "Коммуникации",
        "interval_minutes": 45,
        "entities": ["lead_notes", "contact_notes", "company_notes", "customer_notes"],
    },
    {
        "name": "directory",
        "label": "Справочники",
        "interval_minutes": 360,
        "entities": [
            "pipelines",
            "users",
            "lead_custom_fields",
            "contact_custom_fields",
            "company_custom_fields",
            "events",
        ],
    },
]


def default_auto_sync_settings(enabled: bool = False) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "groups": DEFAULT_AUTO_SYNC_GROUPS,
        "last_started_at": {},
        "last_job_id": {},
        "last_error": None,
    }


def normalize_auto_sync_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    result = default_auto_sync_settings(enabled=bool(source.get("enabled")))
    groups = source.get("groups")
    if isinstance(groups, list) and groups:
        result["groups"] = [
            _normalize_group(group)
            for group in groups
            if isinstance(group, dict)
        ] or result["groups"]
    for key in ("last_started_at", "last_job_id"):
        value = source.get(key)
        if isinstance(value, dict):
            result[key] = dict(value)
    result["last_error"] = source.get("last_error")
    return result


def next_due_group(config: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    if not bool(config.get("enabled")):
        return None
    now = now or datetime.now(timezone.utc)
    last_started = config.get("last_started_at") if isinstance(config.get("last_started_at"), dict) else {}
    due: list[tuple[float, dict[str, Any]]] = []
    for group in config.get("groups") or []:
        if not isinstance(group, dict) or not group.get("enabled", True):
            continue
        entities = [str(entity) for entity in group.get("entities") or [] if str(entity).strip()]
        if not entities:
            continue
        name = str(group.get("name") or "")
        interval_minutes = max(int(group.get("interval_minutes") or 15), 1)
        last = _parse_dt(last_started.get(name))
        if not last:
            due.append((float("inf"), {**group, "entities": entities}))
            continue
        age_minutes = (now - last).total_seconds() / 60
        if age_minutes >= interval_minutes:
            due.append((age_minutes - interval_minutes, {**group, "entities": entities}))
    due.sort(key=lambda item: item[0], reverse=True)
    return due[0][1] if due else None


def mark_group_started(config: dict[str, Any], group_name: str, job_id: int, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    updated = normalize_auto_sync_settings(config)
    updated.setdefault("last_started_at", {})[group_name] = now.isoformat()
    updated.setdefault("last_job_id", {})[group_name] = int(job_id)
    updated["last_error"] = None
    return updated


def mark_auto_sync_error(config: dict[str, Any], message: str) -> dict[str, Any]:
    updated = normalize_auto_sync_settings(config)
    updated["last_error"] = message
    return updated


def _normalize_group(group: dict[str, Any]) -> dict[str, Any]:
    name = str(group.get("name") or "").strip() or "sync"
    label = str(group.get("label") or name).strip()
    try:
        interval_minutes = int(group.get("interval_minutes") or 15)
    except (TypeError, ValueError):
        interval_minutes = 15
    entities = [str(entity) for entity in group.get("entities") or [] if str(entity).strip()]
    return {
        "name": name,
        "label": label,
        "interval_minutes": max(interval_minutes, 1),
        "entities": entities,
        "enabled": bool(group.get("enabled", True)),
    }


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
