from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from amocrm_service.repository import Repository


CLOSED_STATUS_IDS = {142, 143}
DEFAULT_STALE_LEAD_DAYS = 3
DEFAULT_MAX_RISKS = 100


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        if int(value) <= 0:
            return None
        return datetime.fromtimestamp(int(value), timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ts(value: datetime | None) -> int:
    return int(value.timestamp()) if value else 0


def _status_id(lead: dict[str, Any]) -> int:
    try:
        return int(lead.get("status_id") or 0)
    except (TypeError, ValueError):
        return 0


def _lead_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


class QualityService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def summary(
        self,
        *,
        now: datetime | None = None,
        stale_lead_days: int = DEFAULT_STALE_LEAD_DAYS,
        max_risks: int = DEFAULT_MAX_RISKS,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = settings or {}
        now = now or _utc_now()
        stale_lead_days = max(int(settings.get("stale_lead_days") or stale_lead_days or DEFAULT_STALE_LEAD_DAYS), 1)
        max_risks = max(int(settings.get("max_risks") or max_risks or DEFAULT_MAX_RISKS), 1)
        rules = settings.get("rules") if isinstance(settings.get("rules"), dict) else {}
        users = self._users_by_id()
        pipelines = self._pipelines_by_id()
        leads = self._filtered_leads(settings)
        tasks = self.repository.all_payloads("tasks")
        lead_notes = self.repository.all_payloads("lead_notes")
        open_tasks_by_lead = self._open_tasks_by_lead(tasks)
        latest_activity_by_lead = self._latest_activity_by_lead(tasks, lead_notes)

        risks: list[dict[str, Any]] = []
        if rules.get("overdue_tasks", True):
            risks.extend(self._overdue_task_risks(tasks, leads, users, pipelines, now))
        if rules.get("missing_next_task", True):
            risks.extend(self._missing_task_risks(leads, open_tasks_by_lead, users, pipelines))
        if rules.get("stale_leads", True):
            risks.extend(self._stale_lead_risks(
                leads,
                latest_activity_by_lead,
                open_tasks_by_lead,
                users,
                pipelines,
                now,
                stale_lead_days,
            ))
        risks.sort(key=lambda item: (int(item["weight"]), int(item.get("age_hours") or 0)), reverse=True)
        risks = risks[:max_risks]
        by_user = self._by_user(risks)
        by_type = Counter(str(item["type"]) for item in risks)
        by_severity = Counter(str(item["severity"]) for item in risks)
        penalty = min(100, sum(int(item["weight"]) for item in risks))

        return {
            "generated_at": now.isoformat(),
            "settings": {
                "stale_lead_days": stale_lead_days,
                "max_risks": max_risks,
                "filters": settings.get("filters") or {},
                "rules": rules,
            },
            "health_score": max(0, 100 - penalty),
            "totals": {
                "open_leads": len(leads),
                "tasks": len(tasks),
                "open_tasks": sum(1 for task in tasks if not task.get("is_completed")),
                "risks": len(risks),
                "critical": by_severity.get("critical", 0),
                "warning": by_severity.get("warning", 0),
                "info": by_severity.get("info", 0),
                "overdue_tasks": by_type.get("overdue_task", 0),
                "leads_without_open_task": by_type.get("lead_without_open_task", 0),
                "stale_leads": by_type.get("stale_lead", 0),
            },
            "by_user": by_user,
            "by_type": [{"type": key, "count": by_type[key]} for key in sorted(by_type)],
            "risks": risks,
        }

    def _users_by_id(self) -> dict[str, str]:
        users: dict[str, str] = {}
        for item in self.repository.all_payloads("users"):
            user_id = item.get("id")
            if user_id is None:
                continue
            users[str(user_id)] = str(item.get("name") or item.get("email") or f"User {user_id}")
        return users

    def _pipelines_by_id(self) -> dict[int, dict[str, Any]]:
        pipelines: dict[int, dict[str, Any]] = {}
        for item in self.repository.all_payloads("pipelines"):
            try:
                pipeline_id = int(item.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if not pipeline_id:
                continue
            statuses = {}
            for status in (item.get("_embedded") or {}).get("statuses") or []:
                try:
                    status_id = int(status.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                if status_id:
                    statuses[status_id] = str(status.get("name") or status_id)
            pipelines[pipeline_id] = {
                "name": str(item.get("name") or f"Pipeline {pipeline_id}"),
                "statuses": statuses,
            }
        return pipelines

    def _filtered_leads(self, settings: dict[str, Any]) -> list[dict[str, Any]]:
        filters = settings.get("filters") if isinstance(settings.get("filters"), dict) else {}
        pipeline_ids = {int(item) for item in filters.get("pipeline_ids") or []}
        status_ids = {int(item) for item in filters.get("status_ids") or []}
        ignored_status_ids = {int(item) for item in filters.get("ignored_status_ids") or CLOSED_STATUS_IDS}
        responsible_user_ids = {int(item) for item in filters.get("responsible_user_ids") or []}
        leads = []
        for lead in self.repository.all_payloads("leads"):
            status_id = _status_id(lead)
            if status_id in ignored_status_ids:
                continue
            if pipeline_ids and int(lead.get("pipeline_id") or 0) not in pipeline_ids:
                continue
            if status_ids and status_id not in status_ids:
                continue
            if responsible_user_ids and int(lead.get("responsible_user_id") or 0) not in responsible_user_ids:
                continue
            leads.append(lead)
        return leads

    def _open_leads(self) -> list[dict[str, Any]]:
        return [
            lead
            for lead in self.repository.all_payloads("leads")
            if _status_id(lead) not in CLOSED_STATUS_IDS
        ]

    def _open_tasks_by_lead(self, tasks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for task in tasks:
            if task.get("is_completed"):
                continue
            if str(task.get("entity_type") or "") not in {"leads", "lead"}:
                continue
            lead_id = _lead_id(task.get("entity_id"))
            if lead_id:
                result[lead_id].append(task)
        return dict(result)

    def _latest_activity_by_lead(
        self,
        tasks: list[dict[str, Any]],
        lead_notes: list[dict[str, Any]],
    ) -> dict[str, datetime]:
        latest: dict[str, datetime] = {}
        for task in tasks:
            if str(task.get("entity_type") or "") not in {"leads", "lead"}:
                continue
            self._touch(latest, task.get("entity_id"), _parse_dt(task.get("updated_at") or task.get("created_at")))
        for note in lead_notes:
            self._touch(latest, note.get("entity_id"), _parse_dt(note.get("updated_at") or note.get("created_at")))
        return latest

    def _touch(self, latest: dict[str, datetime], raw_lead_id: Any, moment: datetime | None) -> None:
        lead_id = _lead_id(raw_lead_id)
        if not lead_id or not moment:
            return
        current = latest.get(lead_id)
        if current is None or moment > current:
            latest[lead_id] = moment

    def _overdue_task_risks(
        self,
        tasks: list[dict[str, Any]],
        leads: list[dict[str, Any]],
        users: dict[str, str],
        pipelines: dict[int, dict[str, Any]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        leads_by_id = {str(lead.get("id")): lead for lead in leads if lead.get("id") is not None}
        risks = []
        for task in tasks:
            if task.get("is_completed"):
                continue
            due_at = _parse_dt(task.get("complete_till"))
            if not due_at or due_at >= now:
                continue
            lead = leads_by_id.get(str(task.get("entity_id") or ""))
            responsible_user_id = task.get("responsible_user_id") or (lead or {}).get("responsible_user_id")
            age_hours = int((now - due_at).total_seconds() // 3600)
            severity = "critical" if age_hours >= 24 else "warning"
            risks.append(self._risk(
                risk_type="overdue_task",
                severity=severity,
                weight=12 if severity == "critical" else 8,
                title="Просрочена задача",
                detail=str(task.get("text") or task.get("name") or f"Задача #{task.get('id')}"),
                lead=lead,
                users=users,
                pipelines=pipelines,
                responsible_user_id=responsible_user_id,
                task_id=task.get("id"),
                due_at=due_at,
                age_hours=age_hours,
            ))
        return risks

    def _missing_task_risks(
        self,
        leads: list[dict[str, Any]],
        open_tasks_by_lead: dict[str, list[dict[str, Any]]],
        users: dict[str, str],
        pipelines: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        risks = []
        for lead in leads:
            lead_id = _lead_id(lead.get("id"))
            if not lead_id or open_tasks_by_lead.get(lead_id):
                continue
            risks.append(self._risk(
                risk_type="lead_without_open_task",
                severity="warning",
                weight=7,
                title="Сделка без следующей задачи",
                detail="У открытой сделки нет активной задачи или следующего шага.",
                lead=lead,
                users=users,
                pipelines=pipelines,
                responsible_user_id=lead.get("responsible_user_id"),
            ))
        return risks

    def _stale_lead_risks(
        self,
        leads: list[dict[str, Any]],
        latest_activity_by_lead: dict[str, datetime],
        open_tasks_by_lead: dict[str, list[dict[str, Any]]],
        users: dict[str, str],
        pipelines: dict[int, dict[str, Any]],
        now: datetime,
        stale_lead_days: int,
    ) -> list[dict[str, Any]]:
        cutoff = now - timedelta(days=stale_lead_days)
        risks = []
        for lead in leads:
            lead_id = _lead_id(lead.get("id"))
            if not lead_id:
                continue
            last_activity = max(
                [
                    item
                    for item in (
                        _parse_dt(lead.get("updated_at") or lead.get("created_at")),
                        latest_activity_by_lead.get(lead_id),
                    )
                    if item
                ],
                default=None,
            )
            if not last_activity or last_activity >= cutoff:
                continue
            age_hours = int((now - last_activity).total_seconds() // 3600)
            severity = "critical" if not open_tasks_by_lead.get(lead_id) and age_hours >= 24 * stale_lead_days else "warning"
            risks.append(self._risk(
                risk_type="stale_lead",
                severity=severity,
                weight=10 if severity == "critical" else 6,
                title="Сделка давно без активности",
                detail=f"Последняя активность была больше {stale_lead_days} дн. назад.",
                lead=lead,
                users=users,
                pipelines=pipelines,
                responsible_user_id=lead.get("responsible_user_id"),
                last_activity_at=last_activity,
                age_hours=age_hours,
            ))
        return risks

    def _risk(
        self,
        *,
        risk_type: str,
        severity: str,
        weight: int,
        title: str,
        detail: str,
        lead: dict[str, Any] | None,
        users: dict[str, str],
        pipelines: dict[int, dict[str, Any]],
        responsible_user_id: Any = None,
        task_id: Any = None,
        due_at: datetime | None = None,
        last_activity_at: datetime | None = None,
        age_hours: int = 0,
    ) -> dict[str, Any]:
        pipeline_id = int((lead or {}).get("pipeline_id") or 0)
        status_id = int((lead or {}).get("status_id") or 0)
        pipeline = pipelines.get(pipeline_id) or {}
        return {
            "type": risk_type,
            "severity": severity,
            "weight": int(weight),
            "title": title,
            "detail": detail,
            "recommendation": self._recommendation(risk_type),
            "lead_id": None if not lead else str(lead.get("id")),
            "lead_name": None if not lead else str(lead.get("name") or f"Сделка {lead.get('id')}"),
            "task_id": None if task_id in (None, "") else str(task_id),
            "responsible_user_id": None if responsible_user_id in (None, "") else str(responsible_user_id),
            "responsible_user_name": users.get(str(responsible_user_id), f"User {responsible_user_id}" if responsible_user_id else "Без ответственного"),
            "pipeline_id": pipeline_id or None,
            "pipeline_name": pipeline.get("name") or "",
            "status_id": status_id or None,
            "status_name": (pipeline.get("statuses") or {}).get(status_id, ""),
            "lead_updated_at": _ts(_parse_dt((lead or {}).get("updated_at"))),
            "due_at": _ts(due_at),
            "last_activity_at": _ts(last_activity_at),
            "age_hours": max(int(age_hours or 0), 0),
        }

    def _recommendation(self, risk_type: str) -> str:
        if risk_type == "overdue_task":
            return "Проверить задачу, связаться с клиентом и поставить актуальный следующий шаг."
        if risk_type == "lead_without_open_task":
            return "Назначить следующую задачу с конкретным сроком и ответственным."
        if risk_type == "stale_lead":
            return "Открыть сделку, проверить последнюю коммуникацию и вернуть ее в движение."
        return "Проверить сделку и принять решение по следующему шагу."

    def _by_user(self, risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for risk in risks:
            user_id = str(risk.get("responsible_user_id") or "unassigned")
            row = grouped.setdefault(user_id, {
                "responsible_user_id": None if user_id == "unassigned" else user_id,
                "responsible_user_name": risk.get("responsible_user_name") or "Без ответственного",
                "risks": 0,
                "critical": 0,
                "warning": 0,
                "score_penalty": 0,
            })
            row["risks"] += 1
            row[str(risk["severity"])] = int(row.get(str(risk["severity"]), 0)) + 1
            row["score_penalty"] += int(risk["weight"])
        rows = list(grouped.values())
        rows.sort(key=lambda item: (int(item["critical"]), int(item["score_penalty"]), int(item["risks"])), reverse=True)
        return rows
