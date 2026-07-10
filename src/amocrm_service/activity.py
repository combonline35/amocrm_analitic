from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from amocrm_service.repository import Repository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _timestamp(payload: dict[str, Any], fallback: Any = None) -> datetime | None:
    for key in ("updated_at", "created_at", "received_at", "synced_at"):
        parsed = _parse_dt(payload.get(key))
        if parsed:
            return parsed
    return _parse_dt(fallback)


def _compact_action(value: Any, default: str) -> str:
    text = str(value or default).strip()
    return text.replace("_", " ").replace("-", " ")


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


def _fmt_time(value: datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%H:%M")


class ActivityService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def dashboard(
        self,
        days: int = 7,
        limit: int = 80,
        target_date: str | None = None,
        slot_minutes: int = 15,
        use_mart: bool = True,
    ) -> dict[str, Any]:
        day_start, _day_end, day_label = _day_bounds(target_date)
        since = min(_utc_now() - timedelta(days=max(days, 1)), day_start)
        users = self._users_by_id()
        events = self._event_activities(users, since)
        webhooks = self._webhook_activities(users, since)
        tasks = self._task_activities(users, since)
        notes = self._note_activities(users, since)
        items = [*events, *webhooks, *tasks, *notes]
        items.sort(key=lambda item: item["happened_at"], reverse=True)

        by_user: Counter[str] = Counter()
        by_user_score: Counter[str] = Counter()
        by_action: Counter[str] = Counter()
        hourly: Counter[str] = Counter()
        entity_counter: Counter[str] = Counter()
        for item in items:
            by_user[item["user_name"]] += 1
            by_user_score[item["user_name"]] += int(item["weight"])
            by_action[item["action"]] += 1
            entity_counter[item["entity_type"]] += 1
            dt = _parse_dt(item["happened_at"])
            if dt:
                hourly[dt.strftime("%Y-%m-%d %H:00")] += 1

        today = _utc_now().date()
        task_plan = self._task_plan_by_user(day_start, users)
        pulse = self.repository.activity_pulse_mart(day_label, slot_minutes) if use_mart else None
        pulse_source = "mart" if pulse else "raw"
        if not pulse:
            pulse = self._pulse_for_day(items, task_plan, day_label, slot_minutes)
        return {
            "window_days": days,
            "target_date": day_label,
            "pulse_source": pulse_source,
            "totals": {
                "events": len(events),
                "webhooks": len(webhooks),
                "tasks": len(tasks),
                "notes": len(notes),
                "activities": len(items),
                "activity_score": sum(int(item["weight"]) for item in items),
                "today": sum(1 for item in items if (_parse_dt(item["happened_at"]) or _utc_now()).date() == today),
                "active_users": len([name for name, count in by_user.items() if name != "System" and count > 0]),
            },
            "by_user": self._top_rows(by_user, "user_name", scores=by_user_score),
            "by_action": self._top_rows(by_action, "action"),
            "by_entity_type": self._top_rows(entity_counter, "entity_type"),
            "hourly": [{"hour": key, "count": hourly[key]} for key in sorted(hourly)],
            "pulse": pulse,
            "timeline": items[:limit],
        }

    def rebuild_marts_for_day(
        self,
        target_date: str | None = None,
        slot_minutes: int = 15,
    ) -> dict[str, Any]:
        day_start, _day_end, day_label = _day_bounds(target_date)
        users = self._users_by_id()
        events = self._event_activities(users, day_start)
        webhooks = self._webhook_activities(users, day_start)
        tasks = self._task_activities(users, day_start)
        notes = self._note_activities(users, day_start)
        items = [*events, *webhooks, *tasks, *notes]
        task_plan = self._task_plan_by_user(day_start, users)
        pulse = self._pulse_for_day(items, task_plan, day_label, slot_minutes)
        saved = self.repository.replace_activity_marts(pulse)
        return {
            "date": day_label,
            "slot_minutes": int(pulse["slot_minutes"]),
            "saved": saved,
            "totals": pulse["totals"],
        }

    def _users_by_id(self) -> dict[str, str]:
        users: dict[str, str] = {}
        for item in self.repository.all_payloads("users"):
            user_id = item.get("id")
            if user_id is None:
                continue
            users[str(user_id)] = str(item.get("name") or item.get("email") or f"User {user_id}")
        return users

    def _event_activities(self, users: dict[str, str], since: datetime) -> list[dict[str, Any]]:
        rows = self.repository.raw_entities_since("events", since, limit=100000)
        activities = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            happened_at = _timestamp(payload, row["synced_at"])
            if not happened_at or happened_at < since:
                continue
            user_id = payload.get("created_by") or payload.get("updated_by") or payload.get("user_id")
            action = _compact_action(payload.get("type") or payload.get("event_type"), "event")
            entity_type = str(payload.get("entity_type") or payload.get("entity") or "crm")
            entity_id = payload.get("entity_id") or payload.get("entity") or row["entity_id"]
            activities.append(self._activity(
                source="amo_event",
                happened_at=happened_at,
                user_id=user_id,
                user_name=users.get(str(user_id), f"User {user_id}" if user_id else "System"),
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                title=self._event_title(action, entity_type, entity_id),
                payload=payload,
            ))
        return activities

    def _webhook_activities(self, users: dict[str, str], since: datetime) -> list[dict[str, Any]]:
        activities = []
        for row in self.repository.webhook_events_since(since):
            happened_at = _parse_dt(row["received_at"])
            if not happened_at or happened_at < since:
                continue
            payload = json.loads(row["payload_json"] or "{}")
            user_id = self._payload_first(payload, ("modified_user_id", "created_user_id", "responsible_user_id"))
            action = _compact_action(row["event_type"], "webhook")
            entity_type = str(row["entity_type"] or "crm")
            entity_id = row["entity_id"]
            activities.append(self._activity(
                source="webhook",
                happened_at=happened_at,
                user_id=user_id,
                user_name=users.get(str(user_id), f"User {user_id}" if user_id else "System"),
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                title=self._event_title(action, entity_type, entity_id),
                payload=payload,
            ))
        return activities

    def _task_activities(self, users: dict[str, str], since: datetime) -> list[dict[str, Any]]:
        rows = self.repository.raw_entities_since("tasks", since, limit=100000)
        activities = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            happened_at = _timestamp(payload, row["synced_at"])
            if not happened_at or happened_at < since:
                continue
            user_id = payload.get("responsible_user_id") or payload.get("created_by")
            status = "task completed" if payload.get("is_completed") else "task updated"
            activities.append(self._activity(
                source="task",
                happened_at=happened_at,
                user_id=user_id,
                user_name=users.get(str(user_id), f"User {user_id}" if user_id else "System"),
                action=status,
                entity_type=str(payload.get("entity_type") or "task"),
                entity_id=payload.get("entity_id") or row["entity_id"],
                title=str(payload.get("text") or payload.get("name") or status),
                payload=payload,
            ))
        return activities

    def _note_activities(self, users: dict[str, str], since: datetime) -> list[dict[str, Any]]:
        note_types = ("lead_notes", "contact_notes", "company_notes", "customer_notes")
        activities = []
        for note_type in note_types:
            rows = self.repository.raw_entities_since(note_type, since, limit=100000)
            for row in rows:
                payload = json.loads(row["payload_json"] or "{}")
                happened_at = _timestamp(payload, row["synced_at"])
                if not happened_at or happened_at < since:
                    continue
                user_id = payload.get("created_by") or payload.get("updated_by")
                entity_type = note_type.removesuffix("_notes")
                activities.append(self._activity(
                    source="note",
                    happened_at=happened_at,
                    user_id=user_id,
                    user_name=users.get(str(user_id), f"User {user_id}" if user_id else "System"),
                    action=_compact_action(payload.get("note_type"), "note added"),
                    entity_type=entity_type,
                    entity_id=payload.get("entity_id") or row["entity_id"],
                    title=self._note_title(payload),
                    payload=payload,
                ))
        return activities

    def _activity(
        self,
        *,
        source: str,
        happened_at: datetime,
        user_id: Any,
        user_name: str,
        action: str,
        entity_type: str,
        entity_id: Any,
        title: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        category, weight = self._classify_activity(source, action, entity_type, title, payload)
        return {
            "source": source,
            "happened_at": happened_at.isoformat(),
            "user_id": None if user_id in (None, "") else str(user_id),
            "user_name": user_name,
            "action": action,
            "category": category,
            "weight": weight,
            "entity_type": entity_type,
            "entity_id": None if entity_id in (None, "") else str(entity_id),
            "title": title,
            "raw_type": payload.get("type") or payload.get("event_type") or payload.get("note_type"),
        }

    def _classify_activity(
        self,
        source: str,
        action: str,
        entity_type: str,
        title: str,
        payload: dict[str, Any],
    ) -> tuple[str, int]:
        raw_type = str(payload.get("type") or payload.get("event_type") or payload.get("note_type") or "")
        raw_lower = raw_type.lower()
        action_lower = action.lower()
        text = f"{source} {action} {entity_type} {title} {raw_type}".lower()
        if "call" in text or "звон" in text or "phone" in text:
            if "miss" in text or "пропущ" in text:
                return "calls_missed", 1
            if raw_lower.startswith("incoming_call") or action_lower.startswith("incoming call") or "call_in" in raw_lower or "вход" in text:
                return "calls_in", 4
            if raw_lower.startswith("outgoing_call") or action_lower.startswith("outgoing call") or "call_out" in raw_lower or "исход" in text:
                return "calls_out", 5
            return "calls_out", 5
        if source == "webhook":
            return "webhooks", 0
        if "task completed" in text or "complete task" in text or "is completed" in text:
            return "tasks_completed", 5
        if source == "task":
            return "tasks_touched", 2
        if "lead" in entity_type.lower() and ("add" in text or "create" in text or "created" in text):
            return "leads_created", 4
        if "status" in text or "stage" in text or "pipeline" in text:
            return "stage_changes", 3
        if source == "note":
            return "notes", 3
        if "field" in text or "update" in text or "change" in text:
            return "field_changes", 1
        return "other", 1

    def _task_plan_by_user(self, day_start: datetime, users: dict[str, str]) -> dict[str, dict[str, Any]]:
        day_end = day_start + timedelta(days=1)
        plan: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "tasks_due": 0,
            "tasks_completed": 0,
            "tasks_overdue": 0,
        })
        for task in self.repository.all_payloads("tasks"):
            user_id = task.get("responsible_user_id") or task.get("created_by")
            user_key = str(user_id) if user_id not in (None, "") else "system"
            due_at = _parse_dt(task.get("complete_till"))
            updated_at = _timestamp(task)
            completed = bool(task.get("is_completed"))
            if due_at and day_start <= due_at < day_end:
                row = plan[user_key]
                row["user_id"] = None if user_key == "system" else user_key
                row["user_name"] = users.get(user_key, f"User {user_key}" if user_key != "system" else "System")
                row["tasks_due"] += 1
                if completed:
                    row["tasks_completed"] += 1
                else:
                    row["tasks_overdue"] += 1
            elif completed and updated_at and day_start <= updated_at < day_end:
                row = plan[user_key]
                row["user_id"] = None if user_key == "system" else user_key
                row["user_name"] = users.get(user_key, f"User {user_key}" if user_key != "system" else "System")
                row["tasks_completed"] += 1
        return dict(plan)

    def _pulse_for_day(
        self,
        items: list[dict[str, Any]],
        task_plan: dict[str, dict[str, Any]],
        day_label: str,
        slot_minutes: int,
    ) -> dict[str, Any]:
        slot_minutes = min(max(int(slot_minutes or 15), 5), 60)
        day_start, day_end, _label = _day_bounds(day_label)
        slots_count = int(24 * 60 / slot_minutes)
        slot_labels = [
            (day_start + timedelta(minutes=index * slot_minutes)).strftime("%H:%M")
            for index in range(slots_count)
        ]
        by_user: dict[str, dict[str, Any]] = {}
        day_items = []
        for item in items:
            dt = _parse_dt(item["happened_at"])
            if not dt or dt < day_start or dt >= day_end:
                continue
            if int(item["weight"]) <= 0:
                continue
            user_key = item.get("user_id") or f"name:{item['user_name']}"
            row = by_user.setdefault(user_key, self._empty_user_pulse(item, slots_count))
            slot_index = min(slots_count - 1, int((dt - day_start).total_seconds() // 60 // slot_minutes))
            row["slots"][slot_index]["count"] += 1
            row["slots"][slot_index]["score"] += int(item["weight"])
            row["activity_count"] += 1
            row["activity_score"] += int(item["weight"])
            row["categories"][item["category"]] += 1
            row["timestamps"].append(dt)
            day_items.append(item)

        for user_key, plan in task_plan.items():
            row = by_user.setdefault(user_key, {
                "user_id": plan.get("user_id"),
                "user_name": plan.get("user_name") or "System",
                "activity_count": 0,
                "activity_score": 0,
                "slots": [{"count": 0, "score": 0} for _ in range(slots_count)],
                "categories": Counter(),
                "timestamps": [],
            })
            row.update(plan)

        users = []
        for row in by_user.values():
            timestamps = sorted(row.pop("timestamps"))
            categories: Counter[str] = row.pop("categories")
            idle_periods = self._idle_periods(timestamps)
            active_slots = sum(1 for slot in row["slots"] if slot["count"])
            max_score = max([slot["score"] for slot in row["slots"]] or [0])
            for slot in row["slots"]:
                slot["level"] = self._slot_level(slot["score"], max_score)
            row.update({
                "active_minutes": active_slots * slot_minutes,
                "first_activity": _fmt_time(timestamps[0] if timestamps else None),
                "last_activity": _fmt_time(timestamps[-1] if timestamps else None),
                "idle_minutes": sum(period["minutes"] for period in idle_periods),
                "idle_periods_count": len(idle_periods),
                "idle_periods": idle_periods[:6],
                "tasks_due": int(row.get("tasks_due") or 0),
                "tasks_completed": int(row.get("tasks_completed") or categories.get("tasks_completed", 0)),
                "tasks_overdue": int(row.get("tasks_overdue") or 0),
                "calls_out": categories.get("calls_out", 0),
                "calls_in": categories.get("calls_in", 0),
                "calls_missed": categories.get("calls_missed", 0),
                "notes": categories.get("notes", 0),
                "leads_created": categories.get("leads_created", 0),
                "stage_changes": categories.get("stage_changes", 0),
            })
            users.append(row)
        users.sort(key=lambda item: (int(item["activity_score"]), int(item["activity_count"])), reverse=True)
        return {
            "date": day_label,
            "slot_minutes": slot_minutes,
            "slot_labels": slot_labels,
            "totals": {
                "activity_count": len(day_items),
                "activity_score": sum(int(item["weight"]) for item in day_items),
                "active_users": len([row for row in users if row["activity_count"] > 0 and row["user_name"] != "System"]),
                "idle_periods": sum(row["idle_periods_count"] for row in users),
                "tasks_due": sum(row["tasks_due"] for row in users),
                "tasks_completed": sum(row["tasks_completed"] for row in users),
                "tasks_overdue": sum(row["tasks_overdue"] for row in users),
                "calls_out": sum(row["calls_out"] for row in users),
                "calls_in": sum(row["calls_in"] for row in users),
                "calls_missed": sum(row["calls_missed"] for row in users),
                "calls_total": sum(row["calls_out"] + row["calls_in"] + row["calls_missed"] for row in users),
            },
            "users": users,
        }

    def _empty_user_pulse(self, item: dict[str, Any], slots_count: int) -> dict[str, Any]:
        return {
            "user_id": item.get("user_id"),
            "user_name": item["user_name"],
            "activity_count": 0,
            "activity_score": 0,
            "slots": [{"count": 0, "score": 0} for _ in range(slots_count)],
            "categories": Counter(),
            "timestamps": [],
        }

    def _idle_periods(self, timestamps: list[datetime], threshold_minutes: int = 30) -> list[dict[str, Any]]:
        periods = []
        for previous, current in zip(timestamps, timestamps[1:]):
            gap = int((current - previous).total_seconds() // 60)
            if gap >= threshold_minutes:
                periods.append({
                    "from": previous.strftime("%H:%M"),
                    "to": current.strftime("%H:%M"),
                    "minutes": gap,
                })
        return periods

    def _slot_level(self, score: int, max_score: int) -> int:
        if score <= 0:
            return 0
        if max_score <= 0:
            return 1
        ratio = score / max_score
        if ratio >= 0.75:
            return 4
        if ratio >= 0.45:
            return 3
        if ratio >= 0.2:
            return 2
        return 1

    def _event_title(self, action: str, entity_type: str, entity_id: Any) -> str:
        suffix = f" #{entity_id}" if entity_id not in (None, "") else ""
        return f"{action.title()} in {entity_type}{suffix}"

    def _note_title(self, payload: dict[str, Any]) -> str:
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        text = str(params.get("text") or payload.get("text") or "Note added")
        return text[:160]

    def _payload_first(self, payload: dict[str, Any], needles: tuple[str, ...]) -> Any:
        for key, value in payload.items():
            if any(needle in key for needle in needles) and value not in (None, ""):
                return value
        return None

    def _top_rows(
        self,
        counter: Counter[str],
        key: str,
        limit: int = 12,
        scores: Counter[str] | None = None,
    ) -> list[dict[str, Any]]:
        rows = [{key: name, "count": count} for name, count in counter.most_common(limit)]
        if scores:
            for row in rows:
                row["score"] = scores.get(str(row[key]), 0)
        return rows
