from __future__ import annotations

import html
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from amocrm_service.repository import Repository


ENTITY_LABELS = {
    "leads": "Сделки",
    "contacts": "Контакты",
    "companies": "Компании",
    "tasks": "Задачи",
    "customers": "Покупатели",
    "events": "События",
    "users": "Пользователи",
}

BASE_FIELDS: dict[str, list[dict[str, Any]]] = {
    "leads": [
        {"value": "id", "label": "ID сделки", "type": "number", "path": "entity_id", "groupable": True},
        {"value": "name", "label": "Название сделки", "type": "text", "path": "name", "groupable": True},
        {"value": "pipeline_id", "label": "Воронка", "type": "number", "path": "$.pipeline_id", "groupable": True},
        {"value": "status_id", "label": "Этап", "type": "number", "path": "$.status_id", "groupable": True},
        {"value": "responsible_user_id", "label": "Ответственный", "type": "number", "path": "$.responsible_user_id", "groupable": True},
        {"value": "price", "label": "Бюджет", "type": "number", "path": "$.price", "groupable": True},
        {"value": "created_at", "label": "Дата создания", "type": "date", "path": "$.created_at", "groupable": False},
        {"value": "updated_at", "label": "Дата обновления", "type": "date", "path": "$.updated_at", "groupable": False},
        {"value": "closed_at", "label": "Дата закрытия", "type": "date", "path": "$.closed_at", "groupable": False},
        {"value": "created_month", "label": "Месяц создания", "type": "month", "path": "$.created_at", "groupable": True},
        {"value": "updated_month", "label": "Месяц обновления", "type": "month", "path": "$.updated_at", "groupable": True},
        {"value": "closed_month", "label": "Месяц закрытия", "type": "month", "path": "$.closed_at", "groupable": True},
    ],
    "contacts": [
        {"value": "id", "label": "ID контакта", "type": "number", "path": "entity_id", "groupable": True},
        {"value": "name", "label": "Имя контакта", "type": "text", "path": "name", "groupable": True},
        {"value": "responsible_user_id", "label": "Ответственный", "type": "number", "path": "$.responsible_user_id", "groupable": True},
        {"value": "created_at", "label": "Дата создания", "type": "date", "path": "$.created_at", "groupable": False},
        {"value": "updated_at", "label": "Дата обновления", "type": "date", "path": "$.updated_at", "groupable": False},
    ],
    "tasks": [
        {"value": "id", "label": "ID задачи", "type": "number", "path": "entity_id", "groupable": True},
        {"value": "name", "label": "Название задачи", "type": "text", "path": "name", "groupable": True},
        {"value": "responsible_user_id", "label": "Ответственный", "type": "number", "path": "$.responsible_user_id", "groupable": True},
        {"value": "created_at", "label": "Дата создания", "type": "date", "path": "$.created_at", "groupable": False},
        {"value": "updated_at", "label": "Дата обновления", "type": "date", "path": "$.updated_at", "groupable": False},
        {"value": "complete_till", "label": "Срок задачи", "type": "date", "path": "$.complete_till", "groupable": False},
        {"value": "is_completed", "label": "Завершена", "type": "boolean", "path": "$.is_completed", "groupable": True},
    ],
}

CUSTOM_FIELD_ENTITY_TYPES = {
    "leads": "lead_custom_fields",
    "contacts": "contact_custom_fields",
    "companies": "company_custom_fields",
    "customers": "customer_custom_fields",
}

FIELD_VALUE_TYPES_NUMERIC = {"number", "numeric", "price", "monetary"}
FIELD_TYPES_DATE = {"date", "datetime"}
OPS = {
    "eq": "=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "like": "LIKE",
}
EMPTY_OPS = {"empty", "is_empty"}
NOT_EMPTY_OPS = {"not_empty", "is_not_empty", "filled"}
ARITHMETIC_OPS = {"add", "subtract", "multiply", "divide"}
BUSINESS_TZ = ZoneInfo("Europe/Moscow")
SQL_BUSINESS_TZ_MODIFIER = "+3 hours"


@dataclass(frozen=True)
class FormulaValue:
    kind: str
    value: float | int | None = None
    rows: list[dict[str, Any]] | None = None
    meta: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "rows": self.rows or [],
            "meta": self.meta or {},
        }


class FormulaDictionaryService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def build(self) -> dict[str, Any]:
        entities = []
        for entity_type, label in ENTITY_LABELS.items():
            count = self._entity_count(entity_type)
            if count <= 0 and entity_type not in BASE_FIELDS:
                continue
            entities.append({
                "value": entity_type,
                "label": label,
                "count": count,
                "fields": self.fields_for(entity_type),
            })
        return {
            "entities": entities,
            "operators": {
                "aggregations": ["count", "sum", "avg", "min", "max"],
                "math": ["add", "subtract", "multiply", "divide"],
                "filters": sorted(list(OPS) + ["in", "not_in", "between", "date_between", "this_month", "previous_month", "this_week", "previous_week", "last_days", *EMPTY_OPS, *NOT_EMPTY_OPS]),
            },
        }

    def fields_for(self, entity_type: str) -> list[dict[str, Any]]:
        fields = [dict(item, entity=entity_type, source="base") for item in BASE_FIELDS.get(entity_type, [])]
        fields_by_value = {field["value"]: field for field in fields}
        for field in self._custom_fields_from_metadata(entity_type):
            fields_by_value[field["value"]] = field
        for field in self._custom_fields_from_values(entity_type):
            fields_by_value.setdefault(field["value"], field).update({
                "values_count": field.get("values_count", 0),
                "numeric_count": field.get("numeric_count", 0),
            })
        return sorted(fields_by_value.values(), key=lambda item: (item.get("source") != "base", str(item["label"]).lower()))

    def _entity_count(self, entity_type: str) -> int:
        row = self.repository.conn.execute(
            "SELECT COUNT(*) AS items_count FROM raw_entities WHERE entity_type = ?",
            (entity_type,),
        ).fetchone()
        return int(row["items_count"] or 0) if row else 0

    def _custom_fields_from_metadata(self, entity_type: str) -> list[dict[str, Any]]:
        meta_type = CUSTOM_FIELD_ENTITY_TYPES.get(entity_type)
        if not meta_type:
            return []
        rows = self.repository.conn.execute(
            """
            SELECT entity_id, name, payload_json
            FROM raw_entities
            WHERE entity_type = ?
            ORDER BY name COLLATE NOCASE
            """,
            (meta_type,),
        ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            field_id = int(payload.get("id") or row["entity_id"] or 0)
            if not field_id:
                continue
            field_type = str(payload.get("type") or "unknown")
            label = html.unescape(str(payload.get("name") or row["name"] or f"Поле {field_id}"))
            result.append({
                "value": f"cf_{field_id}",
                "label": label,
                "entity": entity_type,
                "type": self._normalize_field_type(field_type),
                "amo_type": field_type,
                "source": "amo_custom_fields",
                "field_id": field_id,
                "groupable": True,
                "path": f"cf:{field_id}",
            })
            if field_type in {"date", "birthday", "date_time", "date_time_range"}:
                result.append({
                    "value": f"cf_month_{field_id}",
                    "label": f"Месяц: {label}",
                    "entity": entity_type,
                    "type": "month",
                    "amo_type": field_type,
                    "source": "amo_custom_fields",
                    "field_id": field_id,
                    "groupable": True,
                    "path": f"cf_month:{field_id}",
                })
        return result

    def _custom_fields_from_values(self, entity_type: str) -> list[dict[str, Any]]:
        rows = self.repository.conn.execute(
            """
            SELECT field_id, MAX(field_name) AS field_name
            FROM entity_custom_field_values
            WHERE entity_type = ?
            GROUP BY field_id
            ORDER BY field_name COLLATE NOCASE
            """,
            (entity_type,),
        ).fetchall()
        result = []
        for row in rows:
            field_id = int(row["field_id"] or 0)
            if not field_id:
                continue
            result.append({
                "value": f"cf_{field_id}",
                "label": html.unescape(str(row["field_name"] or f"Поле {field_id}")),
                "entity": entity_type,
                "type": "text",
                "source": "indexed_values",
                "field_id": field_id,
                "groupable": True,
                "path": f"cf:{field_id}",
            })
        return result

    def _normalize_field_type(self, field_type: str) -> str:
        if field_type in {"date", "birthday"}:
            return "date"
        if field_type in {"date_time", "date_time_range"}:
            return "datetime"
        if field_type in FIELD_VALUE_TYPES_NUMERIC:
            return "number"
        return "text"


class FormulaEngine:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.dictionary = FormulaDictionaryService(repository)

    def evaluate(self, formula: dict[str, Any]) -> dict[str, Any]:
        return self._eval_node(formula, {}).to_json()

    def diagnose(self, formula: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(formula, dict):
            return {"items": []}
        items = self._diagnose_node(formula, title="Формула")
        return {"items": items}

    def _diagnose_node(self, node: Any, *, title: str) -> list[dict[str, Any]]:
        if not isinstance(node, dict):
            return []
        op = str(node.get("op") or "").lower()
        if op in {"count", "sum", "avg", "min", "max"}:
            return [self._diagnose_aggregate(node, title=title, op=op)]
        if op == "table":
            items = []
            columns = node.get("columns") or {}
            if isinstance(columns, dict):
                for column, child in columns.items():
                    items.extend(self._diagnose_node(child, title=str(column)))
            return items
        if op in ARITHMETIC_OPS:
            return [
                *self._diagnose_node(node.get("left"), title=f"{title}: левая часть"),
                *self._diagnose_node(node.get("right"), title=f"{title}: правая часть"),
            ]
        return []

    def _diagnose_aggregate(self, node: dict[str, Any], *, title: str, op: str) -> dict[str, Any]:
        entity_type = str(node.get("from") or node.get("entity") or "leads")
        conditions = self._node_conditions(node)
        base_node = deepcopy(node)
        base_node.pop("where", None)
        base_node.pop("filters", None)
        base_node.pop("group_by", None)
        base_node.pop("limit", None)
        stages = [{
            "label": "Источник без условий",
            "value": self._safe_scalar_value(base_node),
        }]
        current_conditions: list[dict[str, Any]] = []
        fields = {item["value"]: item for item in self.dictionary.fields_for(entity_type)}
        for condition in conditions:
            current_conditions.append(deepcopy(condition))
            check_node = deepcopy(base_node)
            check_node["where"] = deepcopy(current_conditions)
            stages.append({
                "label": self._condition_label(condition, fields, source_id=self._optional_int(node.get("source_id"))),
                "value": self._safe_scalar_value(check_node),
            })
        return {
            "title": title,
            "op": op,
            "entity": entity_type,
            "source_id": node.get("source_id"),
            "stages": stages,
        }

    def _safe_scalar_value(self, node: dict[str, Any]) -> Any:
        try:
            result = self.evaluate(node)
            if result.get("kind") == "scalar":
                return result.get("value")
            if result.get("kind") in {"series", "table"}:
                return len(result.get("rows") or [])
        except Exception as exc:
            return f"Ошибка: {exc}"
        return None

    def _node_conditions(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        conditions: list[dict[str, Any]] = []
        for key in ("where", "filters"):
            raw_conditions = node.get(key) or []
            if isinstance(raw_conditions, list):
                conditions.extend([condition for condition in raw_conditions if isinstance(condition, dict)])
        return conditions

    def _condition_label(
        self,
        condition: dict[str, Any],
        fields: dict[str, dict[str, Any]],
        *,
        source_id: int | None = None,
    ) -> str:
        field = str(condition.get("field") or "")
        field_label = str(fields.get(field, {}).get("label") or field or "Поле")
        op = str(condition.get("op") or condition.get("operator") or "eq").lower()
        op_labels = {
            "eq": "равно",
            "neq": "не равно",
            "like": "содержит",
            "in": "в списке",
            "not_in": "не в списке",
            "gt": "больше",
            "gte": "больше или равно",
            "lt": "меньше",
            "lte": "меньше или равно",
            "between": "между",
            "date_between": "между датами",
            "this_month": "текущий месяц",
            "previous_month": "прошлый месяц",
            "this_week": "текущая неделя",
            "previous_week": "прошлая неделя",
            "last_days": "последние дни",
            "empty": "не заполнено",
            "is_empty": "не заполнено",
            "not_empty": "заполнено",
            "is_not_empty": "заполнено",
            "filled": "заполнено",
        }
        if op in EMPTY_OPS | NOT_EMPTY_OPS or op in {"this_month", "previous_month", "this_week", "previous_week"}:
            return f"{field_label}: {op_labels.get(op, op)}"
        value = condition.get("value")
        if field == "status_id" and op in {"gt", "gte", "lt", "lte"}:
            status_ids = self._status_range_ids(value, op, source_id=source_id)
            if status_ids:
                label_map = self._status_label_map()
                labels = [str(label_map.get(str(status_id), status_id)) for status_id in status_ids]
                visible = ", ".join(labels[:8])
                if len(labels) > 8:
                    visible += f" и еще {len(labels) - 8}"
                direction = {
                    "gte": "этот этап и дальше",
                    "gt": "после этого этапа",
                    "lte": "до этого этапа включительно",
                    "lt": "до этого этапа",
                }.get(op, op_labels.get(op, op))
                return f"{field_label}: {direction} -> {visible}"
        if field in {"status_id", "pipeline_id", "responsible_user_id"} and value not in (None, ""):
            label_map = self._group_label_map(field)
            value_label = label_map.get(str(value), value)
            return f"{field_label}: {op_labels.get(op, op)} {value_label} ({value})"
        return f"{field_label}: {op_labels.get(op, op)} {value}"

    def _eval_node(self, node: Any, scope: dict[str, FormulaValue]) -> FormulaValue:
        if isinstance(node, (int, float)):
            return FormulaValue(kind="scalar", value=node)
        if not isinstance(node, dict):
            raise ValueError("Formula node must be an object")
        op = str(node.get("op") or "").lower()
        if op in {"number", "const", "value"}:
            return FormulaValue(kind="scalar", value=self._to_number(node.get("value") or 0))
        if op == "ref":
            name = str(node.get("name") or "")
            if name not in scope:
                raise ValueError(f"Unknown formula reference: {name}")
            return scope[name]
        if op == "let":
            next_scope = dict(scope)
            variables = node.get("vars") or node.get("variables") or {}
            if not isinstance(variables, dict):
                raise ValueError("let.vars must be an object")
            for name, child in variables.items():
                next_scope[str(name)] = self._eval_node(child, next_scope)
            return self._eval_node(node.get("return") or node.get("body"), next_scope)
        if op == "table":
            return self._table(node, scope)
        if op in {"count", "sum", "avg", "min", "max"}:
            return self._aggregate(node, op)
        if op in ARITHMETIC_OPS:
            left = self._eval_node(node.get("left"), scope)
            right = self._eval_node(node.get("right"), scope)
            return self._apply_math(op, left, right)
        raise ValueError(f"Unsupported formula op: {op}")

    def _table(self, node: dict[str, Any], scope: dict[str, FormulaValue]) -> FormulaValue:
        raw_columns = node.get("columns") or {}
        if not isinstance(raw_columns, dict) or not raw_columns:
            raise ValueError("table.columns must be a non-empty object")
        columns = []
        row_index: dict[str, dict[str, Any]] = {}
        scalar_index = 0
        for name, child in raw_columns.items():
            column_name = str(name)
            columns.append(column_name)
            value = self._eval_node(child, scope)
            if value.kind == "series":
                for item in value.rows or []:
                    key = str(item.get("key") or "")
                    if not key:
                        continue
                    row = row_index.setdefault(key, {"key": key, "label": item.get("label") or key})
                    row[column_name] = item.get("value")
                    if item.get("entity_ids"):
                        row.setdefault("_drilldown", {})[column_name] = {
                            "entity_type": item.get("entity_type") or value.meta.get("entity") or "leads",
                            "entity_ids": item.get("entity_ids") or [],
                            "total": item.get("trace_total") or item.get("value") or 0,
                            "truncated": bool(item.get("trace_truncated")),
                        }
            elif value.kind == "scalar":
                key = "__total__"
                row = row_index.setdefault(key, {"key": key, "label": "Итого"})
                row[column_name] = value.value
                if value.meta and value.meta.get("entity_ids"):
                    row.setdefault("_drilldown", {})[column_name] = {
                        "entity_type": value.meta.get("entity") or "leads",
                        "entity_ids": value.meta.get("entity_ids") or [],
                        "total": value.meta.get("trace_total") or value.value or 0,
                        "truncated": bool(value.meta.get("trace_truncated")),
                    }
            elif value.kind == "table":
                for item in value.rows or []:
                    key = str(item.get("key") or f"row_{scalar_index}")
                    scalar_index += 1
                    row = row_index.setdefault(key, {"key": key, "label": item.get("label") or key})
                    row[column_name] = item
            else:
                raise ValueError(f"Unsupported table column value: {value.kind}")
        rows = list(row_index.values())
        for row in rows:
            for column in columns:
                row.setdefault(column, 0)
        return FormulaValue(kind="table", rows=rows, meta={"columns": columns})

    def _aggregate(self, node: dict[str, Any], op: str) -> FormulaValue:
        entity_type = str(node.get("from") or node.get("entity") or "leads")
        field = str(node.get("field") or "id")
        group_by = node.get("group_by")
        if isinstance(group_by, list):
            group_fields = [str(item) for item in group_by if str(item or "").strip()]
        else:
            group_fields = [str(group_by)] if group_by else []
        source_id = self._optional_int(node.get("source_id"))
        fields = {item["value"]: item for item in self.dictionary.fields_for(entity_type)}
        if field not in fields and op != "count":
            raise ValueError(f"Unknown field for {entity_type}: {field}")
        for group_field in group_fields:
            if group_field not in fields:
                raise ValueError(f"Unknown group field for {entity_type}: {group_field}")

        where_parts = ["raw_entities.entity_type = ?"]
        params: list[Any] = [entity_type]
        join_sql = ""
        if source_id and entity_type == "leads":
            # Count against LIVE raw_entities using the source's stored
            # pipeline/status conditions, instead of the frozen membership
            # snapshot in sync_source_entities. Leads that entered the funnel
            # after the last source resync are now counted too. An empty set
            # means "no constraint on that dimension" (matches source config).
            source_pipeline_ids, source_status_ids = self._source_filter_ids(source_id)
            if source_pipeline_ids:
                pipeline_sql = self._field_sql(entity_type, fields["pipeline_id"])
                placeholders = ",".join("?" for _ in source_pipeline_ids)
                where_parts.append(f"{pipeline_sql} IN ({placeholders})")
                params.extend(sorted(source_pipeline_ids))
            if source_status_ids:
                status_sql = self._field_sql(entity_type, fields["status_id"])
                placeholders = ",".join("?" for _ in source_status_ids)
                where_parts.append(f"{status_sql} IN ({placeholders})")
                params.extend(sorted(source_status_ids))
        conditions = []
        for key in ("where", "filters"):
            raw_conditions = node.get(key) or []
            if isinstance(raw_conditions, list):
                conditions.extend(raw_conditions)
        for condition in conditions:
            clause, clause_params = self._condition_sql(entity_type, fields, condition, source_id=source_id)
            where_parts.append(clause)
            params.extend(clause_params)

        value_sql = "1" if op == "count" else self._field_sql(entity_type, fields[field], numeric=op in {"sum", "avg", "min", "max"})
        aggregate_sql = {
            "count": "COUNT(*)",
            "sum": f"COALESCE(SUM({value_sql}), 0)",
            "avg": f"COALESCE(AVG({value_sql}), 0)",
            "min": f"MIN({value_sql})",
            "max": f"MAX({value_sql})",
        }[op]

        if group_fields:
            group_items = []
            for index, group_field in enumerate(group_fields):
                group_sql = self._field_sql(entity_type, fields[group_field], month=fields[group_field].get("type") == "month")
                group_items.append((group_field, group_sql, f"item_key_{index}", self._group_label_map(group_field)))
            select_sql = ", ".join(f"{group_sql} AS {alias}" for _, group_sql, alias, _ in group_items)
            group_sql = ", ".join(alias for _, _, alias, _ in group_items)
            sql = f"""
            SELECT {select_sql}, {aggregate_sql} AS item_value, GROUP_CONCAT(raw_entities.entity_id) AS entity_ids
            FROM raw_entities
            {join_sql}
            WHERE {' AND '.join(where_parts)}
            GROUP BY {group_sql}
            ORDER BY item_value DESC
            LIMIT ?
            """
            params.append(min(max(int(node.get("limit") or 500), 1), 1000))
            rows = []
            for row in self.repository.conn.execute(sql, params).fetchall():
                key_parts = []
                label_parts = []
                dimensions = []
                skip_row = False
                for group_field, _, alias, label_map in group_items:
                    raw_value = row[alias]
                    if raw_value in (None, ""):
                        skip_row = True
                        break
                    key = str(raw_value)
                    label = label_map.get(key, raw_value)
                    key_parts.append(key)
                    label_parts.append(str(label))
                    dimensions.append({
                        "field": group_field,
                        "label": fields[group_field].get("label") or group_field,
                        "key": key,
                        "value": label,
                    })
                if skip_row:
                    continue
                rows.append({
                    "key": " | ".join(key_parts),
                    "label": " · ".join(label_parts),
                    "value": row["item_value"],
                    "dimensions": dimensions,
                    "entity_type": entity_type,
                    "entity_ids": self._trace_ids(row["entity_ids"]),
                    "trace_total": int(row["item_value"] or 0) if op == "count" else len(self._trace_ids(row["entity_ids"], limit=1000000)),
                    "trace_truncated": self._trace_is_truncated(row["entity_ids"]),
                })
            return FormulaValue(
                kind="series",
                rows=rows,
                meta={"op": op, "entity": entity_type, "group_by": group_by, "group_fields": group_fields},
            )

        sql = f"""
        SELECT {aggregate_sql} AS value, GROUP_CONCAT(raw_entities.entity_id) AS entity_ids
        FROM raw_entities
        {join_sql}
        WHERE {' AND '.join(where_parts)}
        """
        row = self.repository.conn.execute(sql, params).fetchone()
        entity_ids_raw = row["entity_ids"] if row else ""
        return FormulaValue(
            kind="scalar",
            value=row["value"] if row else 0,
            meta={
                "op": op,
                "entity": entity_type,
                "entity_ids": self._trace_ids(entity_ids_raw),
                "trace_total": int(row["value"] or 0) if row and op == "count" else len(self._trace_ids(entity_ids_raw, limit=1000000)),
                "trace_truncated": self._trace_is_truncated(entity_ids_raw),
            },
        )

    def _trace_ids(self, raw_ids: Any, limit: int = 1000) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for raw_id in str(raw_ids or "").split(","):
            entity_id = raw_id.strip()
            if not entity_id or entity_id in seen:
                continue
            seen.add(entity_id)
            ids.append(entity_id)
            if len(ids) >= limit:
                break
        return ids

    def _trace_is_truncated(self, raw_ids: Any, limit: int = 1000) -> bool:
        if not raw_ids:
            return False
        return str(raw_ids).count(",") + 1 > limit

    def _group_label_map(self, group_field: str) -> dict[str, str]:
        if group_field == "responsible_user_id":
            return self._entity_label_map(
                "users",
                fallback_prefix="User",
                payload_keys=("name", "login", "email"),
            )
        if group_field == "pipeline_id":
            return self._pipeline_label_map()
        if group_field == "status_id":
            return self._status_label_map()
        return {}

    def _entity_label_map(
        self,
        entity_type: str,
        *,
        fallback_prefix: str,
        payload_keys: tuple[str, ...],
    ) -> dict[str, str]:
        labels: dict[str, str] = {}
        rows = self.repository.conn.execute(
            """
            SELECT entity_id, name, payload_json
            FROM raw_entities
            WHERE entity_type = ?
            """,
            (entity_type,),
        ).fetchall()
        for row in rows:
            entity_id = str(row["entity_id"] or "")
            if not entity_id:
                continue
            payload = json.loads(row["payload_json"] or "{}")
            label = ""
            for key in payload_keys:
                label = str(payload.get(key) or "").strip()
                if label:
                    break
            label = label or str(row["name"] or "").strip() or f"{fallback_prefix} {entity_id}"
            labels[entity_id] = label
        return labels

    def _pipeline_label_map(self) -> dict[str, str]:
        labels: dict[str, str] = {}
        for pipeline in self.repository.all_payloads("pipelines"):
            pipeline_id = str(pipeline.get("id") or "")
            if pipeline_id:
                labels[pipeline_id] = str(pipeline.get("name") or f"Pipeline {pipeline_id}")
        return labels

    def _status_label_map(self) -> dict[str, str]:
        labels: dict[str, str] = {}
        for pipeline in self.repository.all_payloads("pipelines"):
            for status in (pipeline.get("_embedded") or {}).get("statuses") or []:
                status_id = str(status.get("id") or "")
                if status_id:
                    labels.setdefault(status_id, str(status.get("name") or f"Status {status_id}"))
        return labels

    def _condition_sql(
        self,
        entity_type: str,
        fields: dict[str, dict[str, Any]],
        condition: dict[str, Any],
        source_id: int | None = None,
    ) -> tuple[str, list[Any]]:
        field = str(condition.get("field") or "")
        if field not in fields:
            resolved_field = self._resolve_field_alias(field, fields)
            if not resolved_field:
                raise ValueError(f"Unknown filter field for {entity_type}: {field}")
            field = resolved_field
        op = str(condition.get("op") or condition.get("operator") or "eq").lower()
        field_def = fields[field]
        if op in {"this_month", "previous_month", "this_week", "previous_week", "last_days", "date_between"}:
            field_type = str(field_def.get("type") or "text").strip().lower()
            if field_type not in FIELD_TYPES_DATE and field_type != "month":
                resolved_field = self._resolve_temporal_field_alias(field, fields)
                if resolved_field:
                    field = resolved_field
                    field_def = fields[field]
        value_type = self._condition_value_type(condition, field_def)
        is_month_field = value_type == "month"
        is_date_field = value_type in FIELD_TYPES_DATE
        is_number_field = value_type == "number"
        is_boolean_field = value_type == "boolean"
        field_sql = self._field_sql(
            entity_type,
            field_def,
            numeric=is_number_field or is_boolean_field or is_date_field,
            month=is_month_field,
        )
        raw_date_sql = self._raw_temporal_field_sql(entity_type, field_def)
        value = condition.get("value")
        if op in EMPTY_OPS:
            return self._empty_condition_sql(raw_date_sql if is_date_field or is_month_field else field_sql, value_type, negate=False)
        if op in NOT_EMPTY_OPS:
            return self._empty_condition_sql(raw_date_sql if is_date_field or is_month_field else field_sql, value_type, negate=True)
        if op in {"eq", "neq"} and self._is_empty_filter_value(value):
            return self._empty_condition_sql(raw_date_sql if is_date_field or is_month_field else field_sql, value_type, negate=op == "neq")

        if entity_type == "leads" and field == "status_id" and op in {"gt", "gte", "lt", "lte"}:
            status_ids = self._status_range_ids(value, op, source_id=source_id)
            if status_ids:
                placeholders = ",".join("?" for _ in status_ids)
                return f"{field_sql} IN ({placeholders})", status_ids

        if is_month_field:
            return self._month_condition_sql(field_sql, op, value)
        if is_date_field:
            return self._date_condition_sql(raw_date_sql, op, value)

        if op in {"in", "not_in"}:
            values = list(value or [])
            if not values:
                raise ValueError(f"Filter {field} {op} needs values")
            placeholders = ",".join("?" for _ in values)
            operator = "IN" if op == "in" else "NOT IN"
            return f"{field_sql} {operator} ({placeholders})", [self._coerce_filter_value(field, item, value_type, source_id=source_id) for item in values]
        if op == "between":
            values = list(value or [])
            if len(values) != 2:
                raise ValueError(f"Filter {field} between needs two values")
            return f"{field_sql} BETWEEN ? AND ?", [self._coerce_value(values[0], value_type), self._coerce_value(values[1], value_type)]
        if op == "date_between":
            values = list(value or [])
            if len(values) != 2:
                raise ValueError(f"Filter {field} date_between needs two values")
            return f"{field_sql} BETWEEN ? AND ?", [self._coerce_value(values[0], value_type), self._coerce_value(values[1], value_type)]
        if op in {"this_month", "previous_month", "this_week", "previous_week"}:
            raise ValueError(f"Filter op {op} can be used only with date or month fields")
        if op == "last_days":
            raise ValueError("Filter op last_days can be used only with date fields")
        if op not in OPS:
            raise ValueError(f"Unsupported filter op: {op}")
        if op == "like":
            return f"{field_sql} LIKE ?", [f"%{value}%"]
        return f"{field_sql} {OPS[op]} ?", [self._coerce_filter_value(field, value, value_type, source_id=source_id)]

    def _empty_condition_sql(self, field_sql: str, value_type: str, *, negate: bool) -> tuple[str, list[Any]]:
        parts = [f"{field_sql} IS NULL", f"CAST({field_sql} AS TEXT) = ''"]
        if value_type in {"date", "datetime", "month"}:
            parts.append(f"CAST({field_sql} AS REAL) = 0")
        clause = "(" + " OR ".join(parts) + ")"
        return (f"NOT {clause}" if negate else clause), []

    def _is_empty_filter_value(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            raw = value.strip().lower()
            return raw in {"", "none", "null", "пусто", "не заполнено", "нет", "не указано"}
        return False

    def _field_sql(
        self,
        entity_type: str,
        field: dict[str, Any],
        *,
        numeric: bool = False,
        month: bool = False,
    ) -> str:
        path = str(field.get("path") or "")
        if path == "entity_id":
            return "CAST(raw_entities.entity_id AS INTEGER)" if numeric else "raw_entities.entity_id"
        if path == "name":
            return "raw_entities.name"
        if path.startswith("$."):
            base = f"json_extract(raw_entities.payload_json, '{path}')"
            if month:
                return f"strftime('%Y-%m', CAST({base} AS INTEGER), 'unixepoch', '{SQL_BUSINESS_TZ_MODIFIER}')"
            return f"CAST({base} AS REAL)" if numeric else base
        if path.startswith("cf_month:"):
            field_id = int(path.removeprefix("cf_month:"))
            raw_value_sql = self._custom_field_value_sql(entity_type, field_id, numeric=True)
            if month:
                return f"strftime('%Y-%m', CAST({raw_value_sql} AS INTEGER), 'unixepoch', '{SQL_BUSINESS_TZ_MODIFIER}')"
            return raw_value_sql
        if path.startswith("cf:"):
            field_id = int(path.removeprefix("cf:"))
            return self._custom_field_value_sql(entity_type, field_id, numeric=numeric)
        raise ValueError(f"Unsupported field path: {path}")

    def _condition_value_type(self, condition: dict[str, Any], field: dict[str, Any]) -> str:
        explicit = str(condition.get("value_type") or "").strip().lower()
        if explicit and explicit != "auto":
            return explicit
        field_type = str(field.get("type") or "text").strip().lower()
        if field_type in {"numeric", "price", "monetary"}:
            return "number"
        return field_type

    def _resolve_field_alias(self, field: str, fields: dict[str, dict[str, Any]]) -> str | None:
        if field in fields:
            return field
        if field.startswith("cf_month_"):
            base_field = "cf_" + field.removeprefix("cf_month_")
            resolved = self._resolve_temporal_field_alias(base_field, fields, prefer_month=True)
            if resolved:
                return resolved
        return None

    def _resolve_temporal_field_alias(
        self,
        field: str,
        fields: dict[str, dict[str, Any]],
        *,
        prefer_month: bool = False,
    ) -> str | None:
        source = fields.get(field)
        if not source:
            return None
        source_label = self._normalize_field_label(str(source.get("label") or field))
        if not source_label:
            return None
        candidates: list[tuple[int, str]] = []
        for candidate_key, candidate in fields.items():
            candidate_type = str(candidate.get("type") or "").strip().lower()
            if candidate_type not in FIELD_TYPES_DATE and candidate_type != "month":
                continue
            candidate_label = self._normalize_field_label(str(candidate.get("label") or candidate_key))
            if not candidate_label:
                continue
            if source_label in candidate_label or candidate_label in source_label:
                score = 0
                if prefer_month and candidate_type == "month":
                    score -= 10
                if not prefer_month and candidate_type in FIELD_TYPES_DATE:
                    score -= 8
                score += abs(len(candidate_label) - len(source_label))
                candidates.append((score, candidate_key))
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item[0])[0][1]

    def _normalize_field_label(self, value: str) -> str:
        raw = html.unescape(value).casefold()
        raw = re.sub(r"месяц[:\\s]+", "", raw)
        return "".join(char for char in raw if char.isalnum())

    def _coerce_filter_value(self, field: str, value: Any, value_type: str, *, source_id: int | None = None) -> Any:
        if value_type == "number" and isinstance(value, str) and not self._looks_numeric(value):
            resolved = self._resolve_named_id(field, value, source_id=source_id)
            if resolved is not None:
                return resolved
        return self._coerce_value(value, value_type)

    def _looks_numeric(self, value: str) -> bool:
        try:
            self._to_number(value)
            return True
        except (TypeError, ValueError):
            return False

    def _resolve_named_id(self, field: str, value: str, *, source_id: int | None = None) -> int | None:
        raw = value.strip()
        if not raw:
            return None
        needle = raw.casefold()
        if field == "responsible_user_id":
            for key, label in self._group_label_map("responsible_user_id").items():
                if needle in label.casefold() or label.casefold() in needle:
                    return int(key)
        if field == "pipeline_id":
            for key, label in self._pipeline_label_map().items():
                if needle in label.casefold() or label.casefold() in needle:
                    return int(key)
        if field == "status_id":
            allowed_status_ids = self._source_status_ids(source_id) if source_id else set()
            for key, label in self._status_label_map().items():
                if allowed_status_ids and int(key) not in allowed_status_ids:
                    continue
                if needle in label.casefold() or label.casefold() in needle:
                    return int(key)
        return None

    def _source_status_ids(self, source_id: int | None) -> set[int]:
        _, status_ids = self._source_filter_ids(source_id)
        return status_ids

    def _source_filter_ids(self, source_id: int | None) -> tuple[set[int], set[int]]:
        if not source_id:
            return set(), set()
        row = self.repository.conn.execute(
            "SELECT pipeline_ids_json, status_ids_json FROM sync_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if not row:
            return set(), set()
        try:
            pipeline_ids = {int(value) for value in json.loads(row["pipeline_ids_json"] or "[]") if str(value).strip()}
            status_ids = {int(value) for value in json.loads(row["status_ids_json"] or "[]") if str(value).strip()}
            return pipeline_ids, status_ids
        except (TypeError, ValueError, json.JSONDecodeError):
            return set(), set()

    def _status_range_ids(self, value: Any, op: str, *, source_id: int | None = None) -> list[int]:
        status_id = self._status_filter_value(value, source_id=source_id)
        if status_id is None:
            return []

        for ordered_ids in self._ordered_status_groups(source_id=source_id):
            if status_id not in ordered_ids:
                continue
            index = ordered_ids.index(status_id)
            if op == "gte":
                return ordered_ids[index:]
            if op == "gt":
                return ordered_ids[index + 1:]
            if op == "lte":
                return ordered_ids[:index + 1]
            if op == "lt":
                return ordered_ids[:index]
        return []

    def _status_filter_value(self, value: Any, *, source_id: int | None = None) -> int | None:
        if value in (None, ""):
            return None
        if isinstance(value, str) and not self._looks_numeric(value):
            return self._resolve_named_id("status_id", value, source_id=source_id)
        try:
            return int(self._to_number(value))
        except (TypeError, ValueError):
            return None

    def _ordered_status_groups(self, *, source_id: int | None = None) -> list[list[int]]:
        source_pipeline_ids, source_status_ids = self._source_filter_ids(source_id)
        groups: list[list[int]] = []
        for pipeline in self.repository.all_payloads("pipelines"):
            pipeline_id = int(pipeline.get("id") or 0)
            if source_pipeline_ids and pipeline_id not in source_pipeline_ids:
                continue
            statuses = (pipeline.get("_embedded") or {}).get("statuses") or []
            ordered_ids = [
                int(status.get("id") or 0)
                for status in statuses
                if int(status.get("id") or 0)
            ]
            if source_status_ids:
                ordered_ids = [status_id for status_id in ordered_ids if status_id in source_status_ids]
            if ordered_ids:
                groups.append(ordered_ids)
        return groups

    def _raw_temporal_field_sql(self, entity_type: str, field: dict[str, Any]) -> str:
        path = str(field.get("path") or "")
        if path.startswith("cf_month:"):
            field_id = int(path.removeprefix("cf_month:"))
            return self._custom_field_value_sql(entity_type, field_id, numeric=True)
        if path.startswith("cf:"):
            field_id = int(path.removeprefix("cf:"))
            return self._custom_field_value_sql(entity_type, field_id, numeric=True)
        return self._field_sql(entity_type, field, numeric=True, month=False)

    def _month_condition_sql(self, field_sql: str, op: str, value: Any) -> tuple[str, list[Any]]:
        if op in {"this_month", "previous_month"}:
            return f"{field_sql} = ?", [self._month_preset_value(op)]
        if op in {"this_week", "previous_week", "last_days"}:
            raise ValueError(f"Filter op {op} can be used only with date fields, not month fields")
        if op in {"in", "not_in"}:
            values = [self._date_to_month(item) for item in list(value or [])]
            if not values:
                raise ValueError(f"Month filter {op} needs values")
            placeholders = ",".join("?" for _ in values)
            operator = "IN" if op == "in" else "NOT IN"
            return f"{field_sql} {operator} ({placeholders})", values
        if op in {"between", "date_between"}:
            values = list(value or [])
            if len(values) != 2:
                raise ValueError("Month range filter needs two values")
            return f"{field_sql} BETWEEN ? AND ?", [self._date_to_month(values[0]), self._date_to_month(values[1])]
        if op in {"eq", "neq", "gt", "gte", "lt", "lte"}:
            return f"{field_sql} {OPS[op]} ?", [self._date_to_month(value)]
        if op == "like":
            return f"{field_sql} LIKE ?", [f"%{value}%"]
        raise ValueError(f"Unsupported month filter op: {op}")

    def _date_condition_sql(self, field_sql: str, op: str, value: Any) -> tuple[str, list[Any]]:
        if op in {"this_month", "previous_month", "this_week", "previous_week"}:
            start, end = self._date_preset_range(op)
            return f"{field_sql} BETWEEN ? AND ?", [start, end]
        if op == "last_days":
            days = int(value or 30)
            now = datetime.now(BUSINESS_TZ)
            return f"{field_sql} BETWEEN ? AND ?", [int((now - timedelta(days=days)).timestamp()), int(now.timestamp())]
        if op == "date_between" or op == "between":
            values = list(value or [])
            if len(values) != 2:
                raise ValueError(f"Date filter {op} needs two values")
            return f"{field_sql} BETWEEN ? AND ?", [self._date_to_timestamp(values[0], False), self._date_to_timestamp(values[1], True)]
        if op in {"in", "not_in"}:
            values = list(value or [])
            if not values:
                raise ValueError(f"Date filter {op} needs values")
            day_clauses = []
            params: list[Any] = []
            for item in values:
                day_clauses.append(f"{field_sql} BETWEEN ? AND ?")
                params.extend([self._date_to_timestamp(item, False), self._date_to_timestamp(item, True)])
            glue = " OR " if op == "in" else " AND "
            clause = glue.join(f"({item})" for item in day_clauses)
            return (f"({clause})" if op == "in" else f"NOT ({' OR '.join(f'({item})' for item in day_clauses)})", params)
        if op == "eq":
            return f"{field_sql} BETWEEN ? AND ?", [self._date_to_timestamp(value, False), self._date_to_timestamp(value, True)]
        if op == "neq":
            return f"NOT ({field_sql} BETWEEN ? AND ?)", [self._date_to_timestamp(value, False), self._date_to_timestamp(value, True)]
        if op == "gt":
            return f"{field_sql} > ?", [self._date_to_timestamp(value, True)]
        if op == "gte":
            return f"{field_sql} >= ?", [self._date_to_timestamp(value, False)]
        if op == "lt":
            return f"{field_sql} < ?", [self._date_to_timestamp(value, False)]
        if op == "lte":
            return f"{field_sql} <= ?", [self._date_to_timestamp(value, True)]
        raise ValueError(f"Unsupported date filter op: {op}")

    def _custom_field_value_sql(self, entity_type: str, field_id: int, *, numeric: bool) -> str:
        column = "value_num" if numeric else "value_text"
        return f"""
        (
            SELECT cfv.{column}
            FROM entity_custom_field_values AS cfv
            WHERE cfv.entity_type = '{entity_type}'
              AND cfv.entity_id = raw_entities.entity_id
              AND cfv.field_id = {field_id}
            LIMIT 1
        )
        """

    def _apply_math(self, op: str, left: FormulaValue, right: FormulaValue) -> FormulaValue:
        if left.kind == "scalar" and right.kind == "scalar":
            return FormulaValue(kind="scalar", value=self._math(op, left.value or 0, right.value or 0), meta={"op": op})
        if left.kind == "series" or right.kind == "series":
            return self._apply_series_math(op, left, right)
        raise ValueError(f"Unsupported math operands: {left.kind}, {right.kind}")

    def _apply_series_math(self, op: str, left: FormulaValue, right: FormulaValue) -> FormulaValue:
        left_map = self._series_map(left)
        right_map = self._series_map(right)
        keys = sorted(set(left_map) | set(right_map))
        rows = []
        for key in keys:
            left_item = left_map.get(key, {"label": key, "value": left.value or 0})
            right_item = right_map.get(key, {"label": key, "value": right.value or 0})
            rows.append({
                "key": key,
                "label": left_item.get("label") or right_item.get("label") or key,
                "value": self._math(op, left_item.get("value") or 0, right_item.get("value") or 0),
                "left": left_item.get("value") or 0,
                "right": right_item.get("value") or 0,
                "entity_type": left_item.get("entity_type") or right_item.get("entity_type") or left.meta.get("entity") or right.meta.get("entity"),
                "entity_ids": self._merge_trace_ids(left_item.get("entity_ids"), right_item.get("entity_ids")),
                "trace_total": (left_item.get("trace_total") or len(left_item.get("entity_ids") or []))
                + (right_item.get("trace_total") or len(right_item.get("entity_ids") or [])),
                "trace_truncated": bool(left_item.get("trace_truncated") or right_item.get("trace_truncated")),
            })
        return FormulaValue(kind="series", rows=rows, meta={"op": op})

    def _merge_trace_ids(self, left_ids: Any, right_ids: Any, limit: int = 1000) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for raw_ids in (left_ids or [], right_ids or []):
            if not isinstance(raw_ids, list):
                continue
            for raw_id in raw_ids:
                entity_id = str(raw_id or "").strip()
                if not entity_id or entity_id in seen:
                    continue
                seen.add(entity_id)
                merged.append(entity_id)
                if len(merged) >= limit:
                    return merged
        return merged

    def _series_map(self, value: FormulaValue) -> dict[str, dict[str, Any]]:
        if value.kind == "series":
            return {str(row["key"]): row for row in value.rows or []}
        return {"__scalar__": {"key": "__scalar__", "label": "Значение", "value": value.value or 0}}

    def _math(self, op: str, left: Any, right: Any) -> float:
        left_num = self._to_number(left)
        right_num = self._to_number(right)
        if op == "add":
            return left_num + right_num
        if op == "subtract":
            return left_num - right_num
        if op == "multiply":
            return left_num * right_num
        if op == "divide":
            return 0 if right_num == 0 else left_num / right_num
        raise ValueError(f"Unsupported math op: {op}")

    def _to_number(self, value: Any) -> float:
        if value in (None, ""):
            return 0
        if isinstance(value, str):
            value = value.replace(" ", "").replace(",", ".")
        return float(value)

    def _optional_int(self, value: Any) -> int | None:
        if value in (None, "", 0, "0"):
            return None
        return int(value)

    def _coerce_value(self, value: Any, value_type: str) -> Any:
        if value_type == "number":
            return self._to_number(value)
        if value_type == "boolean":
            return self._to_boolean(value)
        if value_type == "month":
            return self._date_to_month(value)
        if value_type in {"date", "datetime"}:
            return self._date_to_timestamp(value, False)
        return value

    def _to_boolean(self, value: Any) -> int:
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, (int, float)):
            return 1 if value else 0
        raw = str(value).strip().lower()
        return 1 if raw in {"1", "true", "yes", "y", "да", "истина", "выполнена", "завершена"} else 0

    def _date_to_timestamp(self, value: Any, end_of_day: bool) -> int:
        if isinstance(value, (int, float)):
            return int(value)
        raw = str(value).strip()
        normalized = raw.replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(normalized, fmt)
                return int(parsed.replace(tzinfo=BUSINESS_TZ).timestamp())
            except ValueError:
                pass
        date = datetime.strptime(raw, "%Y-%m-%d").date()
        clock = time.max if end_of_day else time.min
        return int(datetime.combine(date, clock, tzinfo=BUSINESS_TZ).timestamp())

    def _date_to_month(self, value: Any) -> str:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(int(value), tz=BUSINESS_TZ).strftime("%Y-%m")
        raw = str(value).strip()
        if len(raw) == 7 and raw[4] == "-":
            return raw
        return datetime.fromtimestamp(self._date_to_timestamp(raw, False), tz=BUSINESS_TZ).strftime("%Y-%m")

    def _month_preset_value(self, op: str) -> str:
        start, _ = self._date_preset_range(op)
        return datetime.fromtimestamp(start, tz=BUSINESS_TZ).strftime("%Y-%m")

    def _date_preset_range(self, op: str) -> tuple[int, int]:
        now = datetime.now(BUSINESS_TZ)
        today = now.date()
        if op == "this_month":
            start_date = today.replace(day=1)
            next_month = start_date.replace(year=start_date.year + 1, month=1) if start_date.month == 12 else start_date.replace(month=start_date.month + 1)
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
        return (
            int(datetime.combine(start_date, time.min, tzinfo=BUSINESS_TZ).timestamp()),
            int(datetime.combine(end_date, time.max, tzinfo=BUSINESS_TZ).timestamp()),
        )
