from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time, timezone
from typing import Any

from amocrm_service.repository import Repository


FIELD_SQL = {
    "lead_id": "CAST(raw_entities.entity_id AS INTEGER)",
    "name": "raw_entities.name",
    "pipeline_id": "CAST(json_extract(raw_entities.payload_json, '$.pipeline_id') AS INTEGER)",
    "status_id": "CAST(json_extract(raw_entities.payload_json, '$.status_id') AS INTEGER)",
    "responsible_user_id": "CAST(json_extract(raw_entities.payload_json, '$.responsible_user_id') AS INTEGER)",
    "price": "CAST(json_extract(raw_entities.payload_json, '$.price') AS INTEGER)",
    "created_at": "CAST(json_extract(raw_entities.payload_json, '$.created_at') AS INTEGER)",
    "updated_at": "CAST(json_extract(raw_entities.payload_json, '$.updated_at') AS INTEGER)",
    "closed_at": "CAST(json_extract(raw_entities.payload_json, '$.closed_at') AS INTEGER)",
}

GROUP_SQL = {
    **FIELD_SQL,
    "created_month": "strftime('%Y-%m', CAST(json_extract(raw_entities.payload_json, '$.created_at') AS INTEGER), 'unixepoch')",
    "updated_month": "strftime('%Y-%m', CAST(json_extract(raw_entities.payload_json, '$.updated_at') AS INTEGER), 'unixepoch')",
    "closed_month": "strftime('%Y-%m', CAST(json_extract(raw_entities.payload_json, '$.closed_at') AS INTEGER), 'unixepoch')",
}

METRIC_SQL = {
    "count": "COUNT(*)",
    "sum_price": "COALESCE(SUM(CAST(json_extract(raw_entities.payload_json, '$.price') AS INTEGER)), 0)",
    "avg_price": "ROUND(AVG(CAST(json_extract(raw_entities.payload_json, '$.price') AS INTEGER)), 2)",
    "open_count": "SUM(CASE WHEN CAST(json_extract(raw_entities.payload_json, '$.status_id') AS INTEGER) NOT IN (142, 143) THEN 1 ELSE 0 END)",
    "won_count": "SUM(CASE WHEN CAST(json_extract(raw_entities.payload_json, '$.status_id') AS INTEGER) = 142 THEN 1 ELSE 0 END)",
    "lost_count": "SUM(CASE WHEN CAST(json_extract(raw_entities.payload_json, '$.status_id') AS INTEGER) = 143 THEN 1 ELSE 0 END)",
}

OPS = {
    "eq": "=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "like": "LIKE",
}


@dataclass(frozen=True)
class AnalyticsQuery:
    entity: str
    metrics: list[str]
    group_by: list[str]
    filters: list[dict[str, Any]]
    filter_logic: str = "and"
    limit: int = 100
    order_by: str | None = None
    order_dir: str = "desc"
    source_id: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AnalyticsQuery":
        raw_source_id = payload.get("source_id")
        source_id = int(raw_source_id) if raw_source_id not in (None, "", 0, "0") else None
        return cls(
            entity=str(payload.get("entity") or "leads"),
            metrics=[str(item) for item in payload.get("metrics") or ["count"]],
            group_by=[str(item) for item in payload.get("group_by") or []],
            filters=list(payload.get("filters") or []),
            filter_logic=str(payload.get("filter_logic") or "and").lower(),
            limit=min(max(int(payload.get("limit") or 100), 1), 500),
            order_by=payload.get("order_by"),
            order_dir=str(payload.get("order_dir") or "desc").lower(),
            source_id=source_id,
        )


class FlexibleAnalyticsService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def run(self, query: AnalyticsQuery) -> dict[str, Any]:
        if query.entity != "leads":
            raise ValueError("Only entity='leads' is supported in the first analytics engine version")

        select_parts: list[str] = []
        group_parts: list[str] = []
        for field in query.group_by:
            field_sql = self._group_sql(field)
            select_parts.append(f"{field_sql} AS {self._alias(field)}")
            group_parts.append(field_sql)

        for metric in query.metrics:
            if metric not in METRIC_SQL:
                raise ValueError(f"Unsupported metric: {metric}")
            select_parts.append(f"{METRIC_SQL[metric]} AS {metric}")

        filter_parts: list[str] = []
        filter_params: list[Any] = []
        for item in query.filters:
            clause, clause_params = self._build_filter(item)
            filter_parts.append(clause)
            filter_params.extend(clause_params)
        logic = " OR " if query.filter_logic == "or" else " AND "
        source_join = ""
        where_sql = "raw_entities.entity_type = ?"
        params: list[Any] = []
        if query.source_id:
            source_join = """
            JOIN sync_source_entities AS source_link
              ON source_link.entity_type = raw_entities.entity_type
             AND source_link.entity_id = raw_entities.entity_id
             AND source_link.source_id = ?
            """
            params.append(query.source_id)
        params.append("leads")
        if filter_parts:
            where_sql += f" AND ({logic.join(filter_parts)})"
            params.extend(filter_params)

        sql = [
            f"SELECT {', '.join(select_parts)}",
            "FROM raw_entities",
            source_join,
            f"WHERE {where_sql}",
        ]
        if group_parts:
            sql.append(f"GROUP BY {', '.join(group_parts)}")
        sql.append(self._order_clause(query))
        sql.append("LIMIT ?")
        params.append(query.limit)

        cursor = self.repository.conn.execute("\n".join(sql), params)
        rows = [dict(row) for row in cursor.fetchall()]
        self._enrich_names(rows, query.group_by)
        return {
            "query": {
                "entity": query.entity,
                "metrics": query.metrics,
                "group_by": query.group_by,
                "filters": query.filters,
                "filter_logic": query.filter_logic,
                "limit": query.limit,
                "order_by": query.order_by,
                "order_dir": query.order_dir,
                "source_id": query.source_id,
            },
            "rows": rows,
            "row_count": len(rows),
        }

    def _build_filter(self, item: dict[str, Any]) -> tuple[str, list[Any]]:
        field = str(item.get("field") or "")
        op = str(item.get("op") or "eq")
        value = item.get("value")
        value_type = str(item.get("value_type") or "auto").lower()
        field_sql = self._field_sql(field, value_type)
        custom_field_id = self._custom_field_id(field)

        if op == "in" or op == "not_in":
            values = list(value or [])
            if not values:
                raise ValueError(f"Filter {field} {op} needs a non-empty list")
            placeholders = ",".join("?" for _ in values)
            operator = "IN" if op == "in" else "NOT IN"
            if custom_field_id is not None:
                values = [self._coerce_filter_value(item, value_type) for item in values]
            return f"{field_sql} {operator} ({placeholders})", values

        if op == "between":
            values = list(value or [])
            if len(values) != 2:
                raise ValueError(f"Filter {field} between needs two values")
            if custom_field_id is not None:
                return f"{field_sql} BETWEEN ? AND ?", [
                    self._coerce_filter_value(values[0], "number"),
                    self._coerce_filter_value(values[1], "number"),
                ]
            return f"{field_sql} BETWEEN ? AND ?", [values[0], values[1]]

        if op == "date_between":
            values = list(value or [])
            if len(values) != 2:
                raise ValueError(f"Filter {field} date_between needs two YYYY-MM-DD values")
            return f"{field_sql} BETWEEN ? AND ?", [
                self._date_to_timestamp(values[0], end_of_day=False),
                self._date_to_timestamp(values[1], end_of_day=True),
            ]

        if op in {"this_month", "previous_month", "this_week", "previous_week"}:
            start, end = self._date_preset_range(op)
            return f"{field_sql} BETWEEN ? AND ?", [start, end]

        if op == "last_days":
            days = int(value or 30)
            now = datetime.now(timezone.utc)
            start = now - timedelta(days=days)
            return f"{field_sql} BETWEEN ? AND ?", [int(start.timestamp()), int(now.timestamp())]

        if op not in OPS:
            raise ValueError(f"Unsupported filter op: {op}")
        if custom_field_id is not None:
            value = self._coerce_filter_value(value, value_type)
        return f"{field_sql} {OPS[op]} ?", [value]

    def _field_sql(self, field: str, value_type: str = "auto") -> str:
        if field in FIELD_SQL:
            return FIELD_SQL[field]
        custom_field_id = self._custom_field_id(field)
        if custom_field_id is not None:
            if value_type == "number":
                return self._custom_field_num_sql(custom_field_id)
            if value_type in {"date", "datetime"}:
                return f"CAST({self._custom_field_value_sql(custom_field_id)} AS INTEGER)"
            return self._custom_field_value_sql(custom_field_id)
        raise ValueError(f"Unsupported filter field: {field}")

    def _group_sql(self, field: str) -> str:
        if field in GROUP_SQL:
            return GROUP_SQL[field]
        custom_month_field_id = self._custom_month_field_id(field)
        if custom_month_field_id is not None:
            return f"strftime('%Y-%m', {self._custom_field_value_sql(custom_month_field_id)}, 'unixepoch')"
        custom_field_id = self._custom_field_id(field)
        if custom_field_id is not None:
            return self._custom_field_value_sql(custom_field_id)
        raise ValueError(f"Unsupported group_by field: {field}")

    def _custom_field_id(self, field: str) -> int | None:
        if field.startswith("cf_month_"):
            return None
        if not field.startswith("cf_"):
            return None
        try:
            return int(field.removeprefix("cf_"))
        except ValueError as exc:
            raise ValueError(f"Invalid custom field token: {field}") from exc

    def _custom_month_field_id(self, field: str) -> int | None:
        if not field.startswith("cf_month_"):
            return None
        try:
            return int(field.removeprefix("cf_month_"))
        except ValueError as exc:
            raise ValueError(f"Invalid custom month field token: {field}") from exc

    def _custom_field_value_sql(self, field_id: int) -> str:
        return f"""
        (
            SELECT cfv.value_text
            FROM entity_custom_field_values AS cfv
            WHERE cfv.entity_type = 'leads'
              AND cfv.entity_id = raw_entities.entity_id
              AND cfv.field_id = {field_id}
            LIMIT 1
        )
        """

    def _custom_field_num_sql(self, field_id: int) -> str:
        return f"""
        (
            SELECT cfv.value_num
            FROM entity_custom_field_values AS cfv
            WHERE cfv.entity_type = 'leads'
              AND cfv.entity_id = raw_entities.entity_id
              AND cfv.field_id = {field_id}
            LIMIT 1
        )
        """

    def _coerce_filter_value(self, value: Any, value_type: str) -> Any:
        if value_type == "number":
            if value in (None, ""):
                return None
            number = float(value)
            return int(number) if number.is_integer() else number
        if value_type in {"date", "datetime"}:
            return self._date_to_timestamp(value, end_of_day=False)
        return str(value)

    def _alias(self, field: str) -> str:
        return "".join(char if char.isalnum() or char == "_" else "_" for char in field)

    def _date_to_timestamp(self, value: Any, end_of_day: bool) -> int:
        if isinstance(value, (int, float)):
            return int(value)
        raw = str(value).strip()
        normalized = raw.replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(normalized, fmt)
                return int(parsed.replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                pass
        date = datetime.strptime(raw, "%Y-%m-%d").date()
        clock = time.max if end_of_day else time.min
        return int(datetime.combine(date, clock, tzinfo=timezone.utc).timestamp())

    def _date_preset_range(self, op: str) -> tuple[int, int]:
        now = datetime.now(timezone.utc)
        today = now.date()
        if op == "this_month":
            start_date = today.replace(day=1)
            if start_date.month == 12:
                next_month = start_date.replace(year=start_date.year + 1, month=1)
            else:
                next_month = start_date.replace(month=start_date.month + 1)
            end_date = next_month - timedelta(days=1)
        elif op == "previous_month":
            this_month = today.replace(day=1)
            end_date = this_month - timedelta(days=1)
            start_date = end_date.replace(day=1)
        elif op == "this_week":
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=6)
        elif op == "previous_week":
            this_week = today - timedelta(days=today.weekday())
            start_date = this_week - timedelta(days=7)
            end_date = start_date + timedelta(days=6)
        else:
            raise ValueError(f"Unsupported date preset: {op}")
        start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        end = datetime.combine(end_date, time.max, tzinfo=timezone.utc)
        return int(start.timestamp()), int(end.timestamp())

    def field_values(self, field: str, limit: int = 200) -> list[Any]:
        field_sql = self._field_sql(field)
        cursor = self.repository.conn.execute(
            f"""
            SELECT {field_sql} AS value, COUNT(*) AS count
            FROM raw_entities
            WHERE entity_type = 'leads' AND {field_sql} IS NOT NULL AND {field_sql} != ''
            GROUP BY value
            ORDER BY count DESC, value ASC
            LIMIT ?
            """,
            (min(max(int(limit), 1), 500),),
        )
        return [{"value": row["value"], "count": row["count"]} for row in cursor.fetchall()]

    def _order_clause(self, query: AnalyticsQuery) -> str:
        if not query.order_by:
            if query.metrics:
                return f"ORDER BY {query.metrics[0]} DESC"
            return ""
        allowed = set(query.metrics) | set(query.group_by)
        if query.order_by not in allowed:
            raise ValueError("order_by must be one of selected metrics or group_by fields")
        direction = "ASC" if query.order_dir == "asc" else "DESC"
        return f"ORDER BY {query.order_by} {direction}"

    def _enrich_names(self, rows: list[dict[str, Any]], group_by: list[str]) -> None:
        if "pipeline_id" not in group_by and "status_id" not in group_by:
            return

        pipeline_names: dict[int, str] = {}
        status_names: dict[tuple[int, int], str] = {}
        status_names_by_id: dict[int, str] = {}
        for pipeline in self.repository.all_payloads("pipelines"):
            pipeline_id = int(pipeline.get("id") or 0)
            pipeline_names[pipeline_id] = pipeline.get("name") or f"Pipeline {pipeline_id}"
            for status in pipeline.get("_embedded", {}).get("statuses", []):
                status_id = int(status.get("id") or 0)
                status_name = status.get("name") or f"Status {status_id}"
                status_names[(pipeline_id, status_id)] = status_name
                status_names_by_id.setdefault(status_id, status_name)

        for row in rows:
            pipeline_id = int(row.get("pipeline_id") or 0)
            status_id = int(row.get("status_id") or 0)
            if "pipeline_id" in row:
                row["pipeline_name"] = pipeline_names.get(pipeline_id, f"Pipeline {pipeline_id}")
            if "status_id" in row:
                row["status_name"] = status_names.get(
                    (pipeline_id, status_id),
                    status_names_by_id.get(status_id, f"Status {status_id}"),
                )
