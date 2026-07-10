from __future__ import annotations

from typing import Any


DEFAULT_QUALITY_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "stale_lead_days": 3,
    "max_risks": 200,
    "filters": {
        "pipeline_ids": [],
        "status_ids": [],
        "ignored_status_ids": [142, 143],
        "responsible_user_ids": [],
    },
    "rules": {
        "overdue_tasks": True,
        "missing_next_task": True,
        "stale_leads": True,
    },
}


def quality_settings(account_settings: dict[str, Any] | None) -> dict[str, Any]:
    raw = (account_settings or {}).get("quality_control") or {}
    settings = _deep_merge(DEFAULT_QUALITY_SETTINGS, raw if isinstance(raw, dict) else {})
    settings["enabled"] = bool(settings.get("enabled", True))
    settings["stale_lead_days"] = max(_int(settings.get("stale_lead_days"), 3), 1)
    settings["max_risks"] = max(_int(settings.get("max_risks"), 200), 1)
    filters = settings.get("filters") if isinstance(settings.get("filters"), dict) else {}
    settings["filters"] = {
        "pipeline_ids": _int_list(filters.get("pipeline_ids")),
        "status_ids": _int_list(filters.get("status_ids")),
        "ignored_status_ids": _int_list(filters.get("ignored_status_ids")),
        "responsible_user_ids": _int_list(filters.get("responsible_user_ids")),
    }
    rules = settings.get("rules") if isinstance(settings.get("rules"), dict) else {}
    settings["rules"] = {
        "overdue_tasks": bool(rules.get("overdue_tasks", True)),
        "missing_next_task": bool(rules.get("missing_next_task", True)),
        "stale_leads": bool(rules.get("stale_leads", True)),
    }
    return settings


def update_quality_settings(account_settings: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    next_settings = dict(account_settings or {})
    current = quality_settings(next_settings)
    next_settings["quality_control"] = quality_settings(_deep_merge({"quality_control": current}, {"quality_control": patch}))
    return next_settings


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_list(value: Any) -> list[int]:
    if not value:
        return []
    if isinstance(value, str):
        values = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        values = value
    else:
        values = [value]
    result = []
    for item in values:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(set(result))
