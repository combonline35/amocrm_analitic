from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


CONDITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "field": {"type": "string"},
        "op": {
            "type": "string",
            "enum": [
                "eq", "neq", "like", "in", "not_in", "gt", "gte", "lt", "lte",
                "between", "date_between", "this_month", "previous_month",
                "this_week", "previous_week", "last_days", "empty", "not_empty",
            ],
        },
        "value": {
            "anyOf": [
                {"type": "string"},
                {"type": "number"},
                {"type": "boolean"},
                {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}]}},
                {"type": "null"},
            ],
        },
        "value_type": {"type": ["string", "null"]},
    },
    "required": ["field", "op", "value", "value_type"],
}

FORMULA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "op": {
            "type": "string",
            "enum": ["count", "sum", "avg", "min", "max", "add", "subtract", "multiply", "divide", "table", "number", "const", "value"],
        },
        "from": {"type": ["string", "null"]},
        "source_id": {"type": ["integer", "null"]},
        "field": {"type": ["string", "null"]},
        "group_by": {
            "anyOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
                {"type": "null"},
            ],
        },
        "limit": {"type": ["integer", "null"]},
        "where": {"type": ["array", "null"], "items": CONDITION_SCHEMA},
        "filters": {"type": ["array", "null"], "items": CONDITION_SCHEMA},
        "value": {"type": ["number", "string", "boolean", "null"]},
        "left": {"anyOf": [{"$ref": "#/$defs/formula"}, {"type": "null"}]},
        "right": {"anyOf": [{"$ref": "#/$defs/formula"}, {"type": "null"}]},
        "columns": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "formula": {"anyOf": [{"$ref": "#/$defs/formula"}, {"type": "null"}]},
                },
                "required": ["title", "formula"],
            },
        },
    },
    "required": ["op", "from", "source_id", "field", "group_by", "limit", "where", "filters", "value", "left", "right", "columns"],
}

FORMULA_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "$defs": {
        "formula": FORMULA_SCHEMA,
    },
    "properties": {
        "title": {"type": "string"},
        "view": {"type": "string", "enum": ["number", "table", "bar", "line", "list"]},
        "size": {"type": "string", "enum": ["small", "medium", "wide"]},
        "formula": {"$ref": "#/$defs/formula"},
        "explanation": {"type": "string"},
        "confidence": {"type": "number"},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "view", "size", "formula", "explanation", "confidence", "questions"],
}

TEMPORAL_OPS = {"this_month", "previous_month", "this_week", "previous_week", "last_days", "date_between"}
TEMPORAL_FIELD_TYPES = {"date", "datetime", "month"}


SYSTEM_PROMPT = """
Critical rule: for status_id never use gt/gte/lt/lte as numeric comparison. If the user says "stage X and later", "from X onward", or "X и дальше", use op="in" with explicit status IDs from the ordered statuses list of the selected source/pipeline, starting with X and then every following stage.
Critical rule: if the user names a custom date/month field, use that exact date/month field for the period filter. Do not replace it with created_at unless the user explicitly says "creation date" or "created".
Critical rule: for complex KPI tables, keep the JSON compact: one table formula with columns, no prose inside formula fields, no invented custom field ids.
Critical rule: for percent columns in a table, use op="divide" with full aggregate formulas in both left and right. Never put null, strings, column names, or references in left/right.
Critical rule: in funnel/KPI tables, the base cohort filters apply to every column. If a period, "assigned", "filled", source, or row group is defined for the table, repeat these filters in each count/sum formula; stage columns only add stricter status filters.
Exception: if a column has its own explicit period field, do not inherit another date/month condition from the base cohort. Example: "Договора по Дата договора" must use "Дата договора" as the only period filter and must not also filter by "Дата и время замера" or a measurement date field.
Critical business rule: for measurement-conversion tables by "Замерщик" / "замерщики", the base column "Назначено" means the scheduled measurement datetime field ("Дата и время замера"), not the status-entered date field "ДПвС-ЗАМЕР НАЗНАЧЕН". Use "Т_замер состоялся" for "Состоялось"; use "Дата договора" for contract counts when the user asks for contracts in the same period.
Ты собираешь безопасные формулы для аналитического дашборда amoCRM.
Верни только JSON по заданной схеме.

Правила:
- Не пиши SQL.
- Используй только сущности, поля и source_id из переданного словаря.
- Если передан default_source, считай внутри него и ставь его id в source_id, кроме случаев когда пользователь явно просит весь хаб или все источники.
- Если пользователь называет этап, выбирай status_id только из default_source.statuses или из statuses нужного source_id. Не используй похожие этапы из других источников.
- Формула должна быть объектом нашего DSL.
- Базовые операции: count, sum, avg, min, max.
- Арифметика: add, subtract, multiply, divide с left/right.
- У add/subtract/multiply/divide левая и правая часть всегда должны быть объектами формулы. Для процента "Договора / Состоялось" продублируй две count-формулы с одинаковым group_by, а не ссылайся на названия колонок.
- Таблица: возвращай columns массивом: [{"title":"Название","formula": <formula>}]. Сервер превратит его в объект.
- Для KPI-таблицы по воронке не считай последующие колонки по всей истории. Колонки "Состоялось", "Договора", "Успешно" должны сохранять базовый период и базовые условия из колонки "Назначено", плюс свой этап.
- Агрегация: {"op":"count","from":"leads","source_id":1,"where":[...]}.
- Поля фильтра: field, op, value. Допустимые op: eq, neq, like, in, not_in, gt, gte, lt, lte, between, date_between, this_month, previous_month, this_week, previous_week, last_days, empty, not_empty.
- Если пользователь говорит "поле указано", "заполнено", "есть замерщик" — используй op="not_empty" и value=null. Если "не указано" — op="empty".
- Для даты можно использовать this_month/previous_month/this_week/previous_week/last_days/date_between.
- Для месяца можно использовать this_month/previous_month/date_between/eq.
- Не придумывай cf_month_* сам. Используй только month-поля, которые есть в словаре. Если есть похожее текстовое и date/month поле, для периода выбирай date/month поле.
- Если нужно разбить результат, используй group_by.
- Если пользователь просит разбивку сразу по нескольким полям, используй group_by как массив полей, например ["cf_298209","cf_127785"]. Не теряй вторую группировку.
- Если пользователь просит таблицу, мини-таблицу, список, топ или "по ..." какому-то полю, возвращай view="table" или view="list" и используй group_by.
- Разрезы: "по ответственным", "по менеджерам" -> group_by="responsible_user_id"; "по замерщикам" -> поле "Замерщик" из словаря; "по воронкам" -> "pipeline_id"; "по этапам" -> "status_id"; "по месяцам создания" -> "created_month"; "по рекламным площадкам/источникам заявок" -> подходящее custom field из словаря.
- Если пользователь просит "созданные сделки в этом месяце по ответственным", формула должна быть count from leads, where created_at this_month, group_by responsible_user_id.
- Если пользователь просит топ-N, ставь limit=N.
- Если ТЗ неоднозначное, всё равно собери лучший черновик и добавь вопросы в questions.
- Ответ должен быть на русском.
""".strip()


class AiFormulaError(RuntimeError):
    pass


def ai_provider_config() -> dict[str, str]:
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openrouter_key:
        return {
            "provider": "openrouter",
            "api_key": openrouter_key,
            "base_url": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            "model": os.getenv("OPENROUTER_MODEL", os.getenv("AI_MODEL", "openai/gpt-4.1-mini")).strip(),
        }
    if openai_key:
        return {
            "provider": "openai",
            "api_key": openai_key,
            "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            "model": os.getenv("OPENAI_MODEL", os.getenv("AI_MODEL", "gpt-4.1-mini")).strip(),
        }
    return {"provider": "", "api_key": "", "base_url": "", "model": ""}


def _simple_count_draft(
    *,
    user_prompt: str,
    dictionary: dict[str, Any],
    default_source: dict[str, Any] | None,
) -> dict[str, Any] | None:
    tokens = _text_tokens(user_prompt)
    if not (tokens & {"сделки", "сделок", "заявки", "заявок"}):
        return None
    if not (tokens & {"посчитай", "считай", "сколько", "всего", "количество"}):
        return None

    fields = _dictionary_fields_by_entity(dictionary).get("leads", {})
    if "created_at" not in fields:
        return None

    source_id = None if _prompt_requests_whole_hub(user_prompt) else int((default_source or {}).get("id") or 0) or None
    formula: dict[str, Any] = {
        "op": "count",
        "from": "leads",
        "field": None,
        "group_by": None,
        "where": [],
    }
    if source_id:
        formula["source_id"] = source_id

    title = "Количество сделок"
    view = "number"
    explanation_parts = ["Считаю количество сделок"]

    def has_prefix(prefix: str) -> bool:
        return any(token.startswith(prefix) for token in tokens)

    wants_current_month = (
        has_prefix("текущ") and any(token.startswith("месяц") for token in tokens)
    )
    wants_previous_month = (
        has_prefix("прошл") and any(token.startswith("месяц") for token in tokens)
    )
    wants_ad_group = has_prefix("реклам") and (
        has_prefix("источник") or has_prefix("площад") or has_prefix("канал")
    )
    wants_responsible_group = (
        "ответственным" in tokens or "ответственные" in tokens or "менеджерам" in tokens
    )
    mentions_other_business_date = (
        has_prefix("договор")
        or has_prefix("замер")
        or "дата договора" in user_prompt.casefold()
    )

    mentions_created = (
        {"создания", "созданные", "созданных", "создано", "создана"} & tokens
        or "дата создания" in user_prompt.casefold()
    )
    wants_created_group = mentions_created and ("по" in tokens or "дате" in tokens or "месяцам" in tokens)
    has_explicit_field_conditions = bool(
        tokens & {"где", "поле", "заполнено", "заполнен", "значением", "равно", "сгруппируй", "группируй"}
    )
    has_stage_or_reason_conditions = (
        has_prefix("этап")
        or has_prefix("статус")
        or has_prefix("закры")
        or has_prefix("реализ")
        or has_prefix("причин")
        or has_prefix("отказ")
    )
    has_table_request = has_prefix("таблиц") or has_prefix("разбив") or has_prefix("группир")
    has_named_field_reference = bool(re.search(r"[\"'«“][^\"'»”]{3,}[\"'»”]", user_prompt)) or "cf_" in user_prompt.casefold()
    supported_simple_shape = (
        wants_ad_group
        or wants_responsible_group
        or mentions_created
        or ((wants_current_month or wants_previous_month) and not mentions_other_business_date)
    )
    if (
        has_stage_or_reason_conditions
        or (mentions_other_business_date and not mentions_created)
        or (has_explicit_field_conditions and not supported_simple_shape)
        or (has_named_field_reference and not (wants_ad_group or wants_responsible_group))
        or (has_table_request and not (wants_ad_group or wants_responsible_group or wants_created_group))
    ):
        return None

    if wants_current_month and (mentions_created or not mentions_other_business_date):
        formula["where"] = [{"field": "created_at", "op": "this_month", "value": None, "value_type": "date"}]
        title = "Сделки, созданные в текущем месяце"
        explanation_parts.append("с фильтром по дате создания в текущем месяце")
    elif wants_previous_month and (mentions_created or not mentions_other_business_date):
        formula["where"] = [{"field": "created_at", "op": "previous_month", "value": None, "value_type": "date"}]
        title = "Сделки, созданные в прошлом месяце"
        explanation_parts.append("с фильтром по дате создания в прошлом месяце")
    elif wants_created_group:
        formula["group_by"] = "created_month"
        title = "Сделки по месяцу создания"
        view = "table"
        explanation_parts.append("и группирую по месяцу создания")

    if wants_ad_group:
        ad_field = "cf_127785" if "cf_127785" in fields else _find_field(
            fields,
            [["реклам", "площад"], ["реклам", "источник"], ["источник", "заяв"], ["канал"]],
        )
        if ad_field:
            formula["group_by"] = ad_field
            title = (
                "Сделки по рекламным источникам за текущий месяц"
                if wants_current_month
                else "Сделки по рекламным источникам"
            )
            view = "table"
            explanation_parts.append("с группировкой по рекламному источнику")

    if not formula.get("where"):
        formula.pop("where", None)
    if not formula.get("group_by"):
        formula.pop("group_by", None)

    if wants_responsible_group:
        formula["group_by"] = "responsible_user_id"
        title = "Сделки по ответственным"
        view = "table"
        explanation_parts.append("с группировкой по ответственным")

    if source_id:
        explanation_parts.append(f"в выбранном источнике #{source_id}")
    else:
        explanation_parts.append("по всему хабу")

    return {
        "configured": True,
        "provider": "rules",
        "model": "simple-count-rules",
        "title": title,
        "view": view,
        "size": "medium" if view == "number" else "wide",
        "formula": formula,
        "explanation": ". ".join(explanation_parts) + ".",
        "confidence": 0.9,
        "questions": [],
    }


def _lost_reason_ad_platform_current_month_draft(
    *,
    user_prompt: str,
    dictionary: dict[str, Any],
    default_source: dict[str, Any] | None,
) -> dict[str, Any] | None:
    tokens = _text_tokens(user_prompt)

    def has_prefix(prefix: str) -> bool:
        return any(token.startswith(prefix) for token in tokens)

    has_count = bool(tokens & {"посчитай", "считай", "сколько", "количество"})
    has_current_month = has_prefix("текущ") and any(token.startswith("месяц") for token in tokens)
    has_created = bool({"создания", "созданные", "созданных", "создано", "создана"} & tokens) or "дата создания" in user_prompt.casefold()
    has_lost_stage = has_prefix("закры") and has_prefix("реализ")
    has_reason_group = has_prefix("причин") and has_prefix("отказ")
    has_ad_group = has_prefix("реклам") and (
        has_prefix("площад") or has_prefix("источник") or has_prefix("канал")
    )
    if not (has_count and has_current_month and has_created and has_lost_stage and has_reason_group and has_ad_group):
        return None

    fields = _dictionary_fields_by_entity(dictionary).get("leads", {})
    reason_field = _find_field(fields, [["причин", "отказ"], ["причина", "отказ"], ["отказ"]])
    ad_field = "cf_127785" if "cf_127785" in fields else _find_field(
        fields,
        [["реклам", "площад"], ["реклам", "источник"], ["источник", "заяв"], ["канал"]],
    )
    lost_status_ids = _find_status_ids(default_source, required_prefixes=["закры", "реализ"])
    if not reason_field or not ad_field or not lost_status_ids:
        return None

    source_id = None if _prompt_requests_whole_hub(user_prompt) else int((default_source or {}).get("id") or 0) or None
    formula: dict[str, Any] = {
        "op": "count",
        "from": "leads",
        "field": None,
        "group_by": [reason_field, ad_field],
        "where": [
            {"field": "created_at", "op": "this_month", "value": None, "value_type": "date"},
            {"field": "status_id", "op": "in", "value": lost_status_ids, "value_type": "number"},
        ],
        "limit": 1000,
    }
    if source_id:
        formula["source_id"] = source_id

    return {
        "configured": True,
        "provider": "rules",
        "model": "report-spec-lost-reason-ad-platform-rules",
        "title": "Закрыто и не реализовано по причинам отказа и рекламным площадкам",
        "view": "table",
        "size": "wide",
        "formula": formula,
        "report_spec": {
            "entity": "leads",
            "source_id": source_id,
            "metric": {"type": "count", "label": "Количество сделок"},
            "filters": [
                {"field": "created_at", "op": "this_month", "label": "Дата создания: текущий месяц"},
                {"field": "status_id", "op": "in", "value": lost_status_ids, "label": "Этап: закрыто и не реализовано"},
            ],
            "rows": [{"field": reason_field, "label": "Причина отказа"}],
            "columns": [{"field": ad_field, "label": "Рекламная площадка"}],
        },
        "explanation": (
            "Сначала собран понятный план отчета: берем сделки, созданные в текущем месяце, "
            "оставляем этап 'Закрыто и не реализовано', затем считаем количество сделок в двух разрезах: "
            "причина отказа и рекламная площадка."
        ),
        "confidence": 1,
        "questions": [],
    }


def _lost_reason_current_month_draft(
    *,
    user_prompt: str,
    dictionary: dict[str, Any],
    default_source: dict[str, Any] | None,
) -> dict[str, Any] | None:
    tokens = _text_tokens(user_prompt)

    def has_prefix(prefix: str) -> bool:
        return any(token.startswith(prefix) for token in tokens)

    has_count = bool(tokens & {"посчитай", "считай", "сколько", "количество"})
    has_current_month = has_prefix("текущ") and any(token.startswith("месяц") for token in tokens)
    has_created = bool({"создания", "созданные", "созданных", "создано", "создана"} & tokens) or "дата создания" in user_prompt.casefold()
    has_lost_stage = has_prefix("закры") and has_prefix("реализ")
    has_reason_group = has_prefix("причин") and has_prefix("отказ")
    if not (has_count and has_current_month and has_created and has_lost_stage and has_reason_group):
        return None

    fields = _dictionary_fields_by_entity(dictionary).get("leads", {})
    reason_field = _find_field(fields, [["причин", "отказ"], ["причина", "отказ"], ["отказ"]])
    lost_status_ids = _find_status_ids(default_source, required_prefixes=["закры", "реализ"])
    if not reason_field or not lost_status_ids:
        return None

    formula: dict[str, Any] = {
        "op": "count",
        "from": "leads",
        "field": None,
        "group_by": reason_field,
        "where": [
            {"field": "created_at", "op": "this_month", "value": None, "value_type": "date"},
            {"field": "status_id", "op": "in", "value": lost_status_ids, "value_type": "number"},
        ],
    }
    source_id = int((default_source or {}).get("id") or 0) or None
    if source_id:
        formula["source_id"] = source_id

    return {
        "configured": True,
        "provider": "rules",
        "model": "lost-reason-current-month-rules",
        "title": "Закрыто и не реализовано по причинам отказа за текущий месяц",
        "view": "table",
        "size": "wide",
        "formula": formula,
        "explanation": (
            "Считаю сделки, созданные в текущем месяце, которые находятся на этапе "
            "'Закрыто и не реализовано', и группирую результат по полю 'Причина отказа'."
        ),
        "confidence": 1,
        "questions": [],
    }


def _measurement_assigned_count_draft(
    *,
    user_prompt: str,
    dictionary: dict[str, Any],
    default_source: dict[str, Any] | None,
) -> dict[str, Any] | None:
    tokens = _text_tokens(user_prompt)
    has_measure = any(token.startswith("замер") for token in tokens)
    has_measurer = any(token.startswith("замерщ") for token in tokens)
    has_count = bool(tokens & {"посчитай", "считай", "сколько", "количество"})
    has_assigned = any(token.startswith("назнач") for token in tokens)
    if not (has_measure and has_measurer and has_count and has_assigned):
        return None

    fields = _dictionary_fields_by_entity(dictionary).get("leads", {})
    measurement_date = _find_field(fields, [["дата", "время", "замера"], ["дата", "замера"]], temporal=True)
    measurer = _find_field(fields, [["замерщик"]])
    assigned_flag = _find_field(fields, [["т_замер", "назначен"], ["замер", "назначен"]])
    if not measurement_date or not measurer or not assigned_flag:
        return None

    source_id = int((default_source or {}).get("id") or 0) or None

    def condition(field_key: str, op: str, value: Any = None) -> dict[str, Any]:
        return {
            "field": field_key,
            "op": op,
            "value": value,
            "value_type": fields.get(field_key, {}).get("type") or "auto",
        }

    where = [
        condition(assigned_flag, "eq", 1),
        condition(measurement_date, "this_month"),
        condition(measurer, "not_empty"),
    ]
    formula: dict[str, Any] = {
        "op": "count",
        "from": "leads",
        "field": None,
        "group_by": measurer,
        "where": where,
    }
    if source_id:
        formula["source_id"] = source_id

    return {
        "configured": True,
        "provider": "rules",
        "model": "measurement-assigned-count-rules",
        "title": "Назначенные замеры за текущий месяц по замерщикам",
        "view": "table",
        "size": "wide",
        "formula": formula,
        "explanation": (
            "Считаю сделки, где флаг 'Т_замер назначен' равен 1, дата замера попадает в текущий месяц, "
            "а поле 'Замерщик' заполнено. Результат группирую по полю 'Замерщик'."
        ),
        "confidence": 1,
        "questions": [],
    }


def _measurement_conversion_draft(
    *,
    user_prompt: str,
    dictionary: dict[str, Any],
    default_source: dict[str, Any] | None,
) -> dict[str, Any] | None:
    prompt_tokens = _text_tokens(user_prompt)
    if not _looks_like_measurement_conversion_table(prompt_tokens):
        return None

    fields = _dictionary_fields_by_entity(dictionary).get("leads", {})
    measurement_date = _find_field(fields, [["дата", "время", "замера"], ["дата", "замера"]], temporal=True)
    measurer = _find_field(fields, [["замерщик"]])
    assigned_flag = _find_field(fields, [["т_замер", "назначен"], ["замер", "назначен"]])
    completed_flag = _find_field(fields, [["т_замер", "состоялся"], ["замер", "состоялся"]])
    contract_date = _find_field(fields, [["дата", "договора"], ["договор"]], temporal=True)
    contract_flag = _find_field(fields, [["т_договор", "заключен"], ["договор", "заключен"]])
    if not measurement_date or not measurer or not completed_flag or not (contract_date or contract_flag):
        return None

    source_id = int((default_source or {}).get("id") or 0) or None

    def condition(field_key: str, op: str, value: Any = None) -> dict[str, Any]:
        return {
            "field": field_key,
            "op": op,
            "value": value,
            "value_type": fields.get(field_key, {}).get("type") or "auto",
        }

    requested_measurers = _extract_requested_measurers(user_prompt)
    measurer_condition = (
        condition(measurer, "in", requested_measurers)
        if requested_measurers
        else condition(measurer, "not_empty")
    )

    base_where = [
        condition(measurement_date, "this_month"),
        measurer_condition,
    ]
    if assigned_flag:
        base_where.append(condition(assigned_flag, "eq", 1))

    def count(
        extra: list[dict[str, Any]] | None = None,
        *,
        base: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        node: dict[str, Any] = {
            "op": "count",
            "from": "leads",
            "where": [dict(item) for item in (base or base_where) + (extra or [])],
            "group_by": measurer,
        }
        if source_id:
            node["source_id"] = source_id
        return node

    assigned = count()
    completed = count([condition(completed_flag, "eq", 1)])
    if contract_date:
        contracts = count([condition(contract_date, "this_month")], base=[measurer_condition])
    else:
        contracts = count([condition(contract_flag, "eq", 1)])

    formula: dict[str, Any] = {
        "op": "table",
        "from": "leads",
        "columns": {
            "Назначено": assigned,
            "Св в сост": {"op": "divide", "left": completed, "right": assigned},
            "Состоялось": completed,
            "Договора": contracts,
            "Св общая": {"op": "divide", "left": contracts, "right": completed},
        },
    }
    if source_id:
        formula["source_id"] = source_id

    return {
        "configured": True,
        "provider": "rules",
        "model": "measurement-conversion-rules",
        "title": "Конверсия замерщиков за текущий месяц",
        "view": "table",
        "size": "wide",
        "formula": formula,
        "explanation": (
            "Собрал таблицу по полю 'Замерщик'. Базовый массив: сделки с датой и временем замера "
            "в текущем месяце, заполненным замерщиком и флагом назначенного замера. "
            "'Состоялось' считается по флагу 'Т_замер состоялся'. 'Договора' считает тот же список замерщиков, "
            "но период берет только из поля 'Дата договора' и не наследует дату замера. "
            "Проценты считаются как Состоялось / Назначено и Договора / Состоялось."
        ),
        "confidence": 1,
        "questions": [],
    }


def _extract_requested_measurers(user_prompt: str) -> list[str]:
    patterns = [
        r"показывай\s+только\s+этих\s+замерщиков\s*:\s*(.+?)(?:\n\s*(?:не\s+выводи|колонки|источник|строки|\d+\.)|$)",
        r"только\s+этих\s+замерщиков\s*:\s*(.+?)(?:\n\s*(?:не\s+выводи|колонки|источник|строки|\d+\.)|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, user_prompt, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        raw = match.group(1)
        values = []
        for item in re.split(r"[,;\n]+", raw):
            value = re.sub(r"^\s*[-•\d.)]+", "", item).strip(" .\t\r\n")
            if value and len(value) <= 80:
                values.append(value)
        if values:
            return values
    return []


def _looks_like_measurement_conversion_table(prompt_tokens: set[str]) -> bool:
    has_measurer = any(token.startswith("замерщ") for token in prompt_tokens)
    has_table = bool(prompt_tokens & {"таблица", "таблицу", "строки", "колонки", "конверсия"})
    metric_hits = prompt_tokens & {"назначено", "состоялось", "договора", "договоров", "св"}
    return has_measurer and bool(metric_hits) and (has_table or len(metric_hits) >= 2)


def _find_field(
    fields: dict[str, dict[str, Any]],
    variants: list[list[str]],
    *,
    temporal: bool = False,
) -> str | None:
    candidates: list[tuple[int, str]] = []
    for key, field in fields.items():
        field_type = str(field.get("type") or "").lower()
        if temporal and field_type not in TEMPORAL_FIELD_TYPES:
            continue
        label = str(field.get("label") or "")
        tokens = _text_tokens(f"{key} {label}")
        label_tokens = _text_tokens(label)
        for index, variant in enumerate(variants):
            if all(any(token == wanted or token.startswith(wanted) for token in tokens) for wanted in variant):
                score = 100 - index * 10
                if all(token in label_tokens for token in variant):
                    score += 30
                if len(variant) == 1 and label_tokens == set(variant):
                    score += 200
                if not temporal and field_type in {"number", "numeric", "price"}:
                    score -= 40
                if label_tokens & {"аванс", "зп", "папка", "форма", "комментарии"}:
                    score -= 60
                if str(key).startswith("cf_"):
                    score += 5
                if temporal and field_type == "datetime":
                    score += 3
                candidates.append((score, key))
                break
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _find_status_ids(source: dict[str, Any] | None, *, required_prefixes: list[str]) -> list[int]:
    result: list[int] = []
    if not source:
        return result
    for status in source.get("statuses") or []:
        status_id = int(status.get("id") or 0)
        if not status_id:
            continue
        tokens = _text_tokens(str(status.get("name") or ""))
        if all(any(token.startswith(prefix) for token in tokens) for prefix in required_prefixes):
            result.append(status_id)
    return result


def build_formula_draft(
    *,
    user_prompt: str,
    dictionary: dict[str, Any],
    sources: list[dict[str, Any]],
    default_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deterministic = _measurement_conversion_draft(
        user_prompt=user_prompt,
        dictionary=dictionary,
        default_source=default_source,
    )
    if deterministic:
        return deterministic
    deterministic = _lost_reason_ad_platform_current_month_draft(
        user_prompt=user_prompt,
        dictionary=dictionary,
        default_source=default_source,
    )
    if deterministic:
        return deterministic
    deterministic = _lost_reason_current_month_draft(
        user_prompt=user_prompt,
        dictionary=dictionary,
        default_source=default_source,
    )
    if deterministic:
        return deterministic
    deterministic = _measurement_assigned_count_draft(
        user_prompt=user_prompt,
        dictionary=dictionary,
        default_source=default_source,
    )
    if deterministic:
        return deterministic
    deterministic = _simple_count_draft(
        user_prompt=user_prompt,
        dictionary=dictionary,
        default_source=default_source,
    )
    if deterministic:
        return deterministic

    config = ai_provider_config()
    if not config["api_key"]:
        return {
            "configured": False,
            "provider": "",
            "message": "AI-ключ не настроен. Подойдет OPENAI_API_KEY или OPENROUTER_API_KEY.",
        }

    context = {
        "task": user_prompt,
        "sources": _compact_sources(sources),
        "default_source": _compact_source(default_source) if default_source else None,
        "dictionary": _compact_dictionary(dictionary, user_prompt=user_prompt),
    }
    request_body = {
        "model": config["model"],
        "temperature": 0.1,
        "max_tokens": 6000,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False, separators=(",", ":"))},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "amo_dashboard_formula_draft",
                "strict": True,
                "schema": FORMULA_DRAFT_SCHEMA,
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    if config["provider"] == "openrouter":
        headers["HTTP-Referer"] = os.getenv("AI_HTTP_REFERER", "https://www.panel-amo.linksider-ai.ru")
        headers["X-OpenRouter-Title"] = os.getenv("AI_APP_TITLE", "amoCRM dashboard formula builder")

    raw = None
    selected_model = config["model"]
    last_error: AiFormulaError | None = None
    for model in _ai_model_candidates(config):
        request_body["model"] = model
        try:
            raw = _post_json(f"{config['base_url']}/chat/completions", request_body, headers)
            selected_model = model
            break
        except AiFormulaError as exc:
            last_error = exc
            if config["provider"] == "openrouter" and _is_retryable_openrouter_policy_error(str(exc)):
                continue
            raise
    if raw is None:
        message = str(last_error or "неизвестная ошибка")
        raise AiFormulaError(
            "OpenRouter отклонил запрос политикой безопасности у всех доступных моделей. "
            "Попробуй выбрать другую модель в OPENROUTER_MODEL или пополни OPENROUTER_FALLBACK_MODELS. "
            f"Последняя ошибка: {message}"
        )
    content = _extract_chat_content(raw)
    try:
        draft = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AiFormulaError(f"AI вернул не JSON: {exc}") from exc
    if not isinstance(draft, dict):
        raise AiFormulaError("AI вернул JSON не в виде объекта")
    if isinstance(draft.get("formula"), dict):
        draft["formula"] = _clean_formula(draft["formula"])
        if default_source and not _prompt_requests_whole_hub(user_prompt):
            draft["formula"] = _apply_default_source(draft["formula"], int(default_source.get("id") or 0))
        elif not default_source and not _prompt_mentions_any_source(user_prompt, sources):
            draft["formula"] = _clear_formula_sources(draft["formula"])
        draft["formula"] = _repair_temporal_conditions(draft["formula"], dictionary, user_prompt=user_prompt)
        draft["formula"] = _inherit_table_base_conditions(draft["formula"])
        draft["formula"] = _repair_temporal_conditions(draft["formula"], dictionary, user_prompt=user_prompt)
        errors = _formula_validation_errors(draft["formula"])
        if errors:
            raise AiFormulaError("AI собрал неполную формулу: " + "; ".join(errors[:5]))
    draft["configured"] = True
    draft["provider"] = config["provider"]
    draft["model"] = selected_model
    return draft


def _prompt_requests_whole_hub(prompt: str) -> bool:
    raw = prompt.lower()
    markers = [
        "весь хаб",
        "всему хабу",
        "по всему хабу",
        "все источники",
        "всем источникам",
        "по всем источникам",
        "без источника",
    ]
    return any(marker in raw for marker in markers)


def _apply_default_source(node: Any, source_id: int) -> Any:
    if not source_id:
        return node
    if isinstance(node, list):
        return [_apply_default_source(item, source_id) for item in node]
    if not isinstance(node, dict):
        return node
    result = {key: _apply_default_source(value, source_id) for key, value in node.items()}
    op = str(result.get("op") or "")
    if op in {"count", "sum", "avg", "min", "max"} and str(result.get("from") or "") == "leads":
        if not result.get("source_id"):
            result["source_id"] = source_id
    return result


def _clear_formula_sources(node: Any) -> Any:
    if isinstance(node, list):
        return [_clear_formula_sources(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {key: _clear_formula_sources(value) for key, value in node.items() if key != "source_id"}
    return result


def _prompt_mentions_any_source(prompt: str, sources: list[dict[str, Any]]) -> bool:
    raw = _normalize_prompt_text(prompt)
    if not raw:
        return False
    for source in sources:
        names = [str(source.get("name") or "")]
        names.extend(str(item) for item in source.get("pipeline_names") or [])
        for name in names:
            normalized = _normalize_prompt_text(name)
            if normalized and (normalized in raw or raw in normalized):
                return True
    return False


def _normalize_prompt_text(value: str) -> str:
    return "".join(char for char in str(value).casefold() if char.isalnum())


def _clean_formula(node: Any) -> Any:
    if isinstance(node, list):
        return [_clean_formula(item) for item in node if item is not None]
    if not isinstance(node, dict):
        return node
    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if value is None:
            continue
        if key in {"where", "filters"} and isinstance(value, list):
            conditions = []
            for condition in value:
                if not isinstance(condition, dict):
                    continue
                compact = {
                    k: _clean_formula(v)
                    for k, v in condition.items()
                    if v is not None and not (k == "value" and v == "")
                }
                if compact.get("field") and compact.get("op"):
                    conditions.append(compact)
            if conditions:
                cleaned[key] = conditions
            continue
        if key == "columns" and isinstance(value, list):
            columns: dict[str, Any] = {}
            for item in value:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                formula = _clean_formula(item.get("formula"))
                if title and isinstance(formula, dict):
                    columns[title] = formula
            if columns:
                cleaned[key] = columns
            continue
        if key == "columns" and isinstance(value, dict):
            columns = {}
            for title, formula_value in value.items():
                formula = formula_value.get("formula") if isinstance(formula_value, dict) and "formula" in formula_value else formula_value
                formula = _clean_formula(formula)
                if title and isinstance(formula, dict):
                    columns[str(title)] = formula
            if columns:
                cleaned[key] = columns
            continue
        cleaned[key] = _clean_formula(value)
    return cleaned


def _formula_validation_errors(node: Any, path: str = "formula") -> list[str]:
    if isinstance(node, (int, float)):
        return []
    if not isinstance(node, dict):
        return [f"{path}: должен быть объект формулы"]
    op = str(node.get("op") or "").lower()
    if not op:
        return [f"{path}: не указан op"]
    errors: list[str] = []
    if op == "table":
        columns = node.get("columns")
        if not isinstance(columns, dict) or not columns:
            errors.append(f"{path}.columns: нужна непустая таблица колонок")
        else:
            for title, child in columns.items():
                errors.extend(_formula_validation_errors(child, f"{path}.columns.{title}"))
    elif op in {"add", "subtract", "multiply", "divide"}:
        left = node.get("left")
        right = node.get("right")
        if not isinstance(left, dict) and not isinstance(left, (int, float)):
            errors.append(f"{path}.left: нужна полноценная формула")
        else:
            errors.extend(_formula_validation_errors(left, f"{path}.left"))
        if not isinstance(right, dict) and not isinstance(right, (int, float)):
            errors.append(f"{path}.right: нужна полноценная формула")
        else:
            errors.extend(_formula_validation_errors(right, f"{path}.right"))
    elif op == "let":
        variables = node.get("vars") or node.get("variables") or {}
        if not isinstance(variables, dict):
            errors.append(f"{path}.vars: нужен объект переменных")
        else:
            for name, child in variables.items():
                errors.extend(_formula_validation_errors(child, f"{path}.vars.{name}"))
        errors.extend(_formula_validation_errors(node.get("return") or node.get("body"), f"{path}.return"))
    elif op in {"count", "sum", "avg", "min", "max", "number", "const", "value", "ref"}:
        pass
    else:
        errors.append(f"{path}: неизвестная операция {op}")
    return errors


def _repair_temporal_conditions(node: Any, dictionary: dict[str, Any], *, user_prompt: str = "") -> Any:
    fields_by_entity = _dictionary_fields_by_entity(dictionary)
    prompt_tokens = _text_tokens(user_prompt)

    def walk(value: Any, entity_type: str = "leads") -> Any:
        if isinstance(value, list):
            return [item for item in (walk(item, entity_type) for item in value) if item is not None]
        if not isinstance(value, dict):
            return value

        current_entity = str(value.get("from") or value.get("entity") or entity_type or "leads")
        result: dict[str, Any] = {}
        for key, child in value.items():
            if key in {"where", "filters"} and isinstance(child, list):
                repaired_conditions = []
                for condition in child:
                    repaired = _repair_temporal_condition(
                        condition,
                        fields_by_entity.get(current_entity, {}),
                        prompt_tokens,
                    )
                    if repaired is not None:
                        repaired_conditions.append(repaired)
                if repaired_conditions:
                    result[key] = repaired_conditions
                continue
            if key == "columns" and isinstance(child, dict):
                result[key] = {title: walk(formula, current_entity) for title, formula in child.items()}
                continue
            if key in {"left", "right", "return", "body"}:
                result[key] = walk(child, current_entity)
                continue
            if key in {"vars", "variables"} and isinstance(child, dict):
                result[key] = {name: walk(formula, current_entity) for name, formula in child.items()}
                continue
            result[key] = walk(child, current_entity) if isinstance(child, (dict, list)) else child
        return result

    return walk(node)


def _dictionary_fields_by_entity(dictionary: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for entity in dictionary.get("entities") or []:
        entity_value = str(entity.get("value") or "")
        if not entity_value:
            continue
        result[entity_value] = {
            str(field.get("value") or ""): field
            for field in entity.get("fields") or []
            if field.get("value")
        }
    return result


def _repair_temporal_condition(
    condition: Any,
    fields: dict[str, dict[str, Any]],
    prompt_tokens: set[str],
) -> dict[str, Any] | None:
    if not isinstance(condition, dict):
        return None
    op = str(condition.get("op") or condition.get("operator") or "").lower()
    if op not in TEMPORAL_OPS:
        return condition
    field = str(condition.get("field") or "")
    field_def = fields.get(field)
    field_type = str((field_def or {}).get("type") or "").lower()
    if field_type in TEMPORAL_FIELD_TYPES:
        return condition
    replacement = _best_temporal_field(field, fields, prompt_tokens, op=op)
    if not replacement:
        return None
    repaired = dict(condition)
    repaired["field"] = replacement
    repaired["value_type"] = fields.get(replacement, {}).get("type") or "auto"
    return repaired


def _best_temporal_field(
    source_field: str,
    fields: dict[str, dict[str, Any]],
    prompt_tokens: set[str],
    *,
    op: str,
) -> str | None:
    source = fields.get(source_field) or {}
    source_tokens = _text_tokens(f"{source_field} {source.get('label') or ''}")
    month_op = op in {"this_month", "previous_month"}
    business_candidate = _business_temporal_field(fields, prompt_tokens, source_tokens, month_op=month_op)
    if business_candidate:
        return business_candidate
    candidates: list[tuple[int, str]] = []
    for key, field in fields.items():
        field_type = str(field.get("type") or "").lower()
        if field_type not in TEMPORAL_FIELD_TYPES:
            continue
        label = str(field.get("label") or key)
        tokens = _text_tokens(f"{key} {label}")
        overlap = len(tokens & prompt_tokens)
        source_overlap = len(tokens & source_tokens)
        if not overlap and not source_overlap:
            continue
        score = overlap * 20 + source_overlap * 8
        if str(key).startswith("cf_"):
            score += 6
        if month_op and field_type == "month":
            score += 8
        if not month_op and field_type in {"date", "datetime"}:
            score += 5
        if any(token in tokens for token in {"дпес", "дпвс", "замер"}):
            score += 10
        score += _temporal_marker_score(tokens, prompt_tokens, source_tokens)
        candidates.append((score, key))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _business_temporal_field(
    fields: dict[str, dict[str, Any]],
    prompt_tokens: set[str],
    source_tokens: set[str],
    *,
    month_op: bool,
) -> str | None:
    if not month_op:
        return None
    if not _prompt_mentions_measurement_conversion(prompt_tokens):
        return None
    if _prompt_or_source_has_explicit_transition_marker(prompt_tokens, source_tokens):
        return None
    candidates: list[tuple[int, str]] = []
    for key, field in fields.items():
        field_type = str(field.get("type") or "").lower()
        if field_type not in TEMPORAL_FIELD_TYPES:
            continue
        tokens = _text_tokens(f"{key} {field.get('label') or ''}")
        score = 0
        if {"дата", "время", "замера"} <= tokens:
            score += 120
        if "замер" in tokens and "дата" in tokens:
            score += 60
        if "дпвс" in tokens or "дпес" in tokens:
            score -= 80
        if field_type == "datetime":
            score += 8
        if score > 0:
            candidates.append((score, key))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _prompt_mentions_measurement_conversion(prompt_tokens: set[str]) -> bool:
    has_measure_person = any(token.startswith("замерщ") for token in prompt_tokens)
    has_measure = any(token.startswith("замер") for token in prompt_tokens)
    has_conversion_words = bool(prompt_tokens & {"конверсия", "св", "состоялось", "договора", "назначено"})
    return has_measure_person or (has_measure and has_conversion_words)


def _prompt_or_source_has_explicit_transition_marker(prompt_tokens: set[str], source_tokens: set[str]) -> bool:
    explicit = {"дпвс", "дпес"}
    return bool((prompt_tokens | source_tokens) & explicit)


def _temporal_marker_score(tokens: set[str], prompt_tokens: set[str], source_tokens: set[str]) -> int:
    score = 0
    marker_pairs = [("дпвс", "дпес"), ("дпес", "дпвс")]
    for wanted, other in marker_pairs:
        if wanted in prompt_tokens or wanted in source_tokens:
            if wanted in tokens:
                score += 70
            if other in tokens:
                score -= 70
    if {"дата", "время", "замера"} <= prompt_tokens:
        if {"дата", "время", "замера"} <= tokens:
            score += 90
        if "дпвс" in tokens or "дпес" in tokens:
            score -= 50
    if "договор" in prompt_tokens or "договора" in prompt_tokens:
        if "дата" in tokens and any(token.startswith("договор") for token in tokens):
            score += 90
    return score


def _inherit_table_base_conditions(node: Any) -> Any:
    if isinstance(node, list):
        return [_inherit_table_base_conditions(item) for item in node]
    if not isinstance(node, dict):
        return node

    result = {key: _inherit_table_base_conditions(value) for key, value in node.items()}
    if str(result.get("op") or "").lower() != "table":
        return result

    columns = result.get("columns")
    if not isinstance(columns, dict) or len(columns) < 2:
        return result

    base = _table_base_scope(columns)
    if not base["conditions"]:
        return result

    result["columns"] = {
        title: _apply_base_scope_to_formula(formula, base)
        for title, formula in columns.items()
    }
    return result


def _table_base_scope(columns: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for title, formula in columns.items():
        for aggregate in _iter_aggregate_nodes(formula):
            conditions = [
                condition
                for condition in _formula_conditions(aggregate)
                if _is_base_condition(condition)
            ]
            if not conditions:
                continue
            title_score = 20 if any(marker in str(title).casefold() for marker in ("назнач", "заяв", "план", "всего")) else 0
            score = title_score + len(conditions)
            if aggregate.get("group_by"):
                score += 5
            candidates.append({
                "score": score,
                "from": aggregate.get("from") or aggregate.get("entity") or "leads",
                "source_id": aggregate.get("source_id"),
                "group_by": aggregate.get("group_by"),
                "conditions": conditions,
            })
    if not candidates:
        return {"conditions": []}
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates[0]


def _iter_aggregate_nodes(node: Any) -> list[dict[str, Any]]:
    if not isinstance(node, dict):
        return []
    op = str(node.get("op") or "").lower()
    if op in {"count", "sum", "avg", "min", "max"}:
        return [node]
    result: list[dict[str, Any]] = []
    if op == "table" and isinstance(node.get("columns"), dict):
        for child in node["columns"].values():
            result.extend(_iter_aggregate_nodes(child))
    for key in ("left", "right", "return", "body"):
        result.extend(_iter_aggregate_nodes(node.get(key)))
    variables = node.get("vars") or node.get("variables")
    if isinstance(variables, dict):
        for child in variables.values():
            result.extend(_iter_aggregate_nodes(child))
    return result


def _formula_conditions(node: dict[str, Any]) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    for key in ("where", "filters"):
        raw = node.get(key)
        if isinstance(raw, list):
            conditions.extend([item for item in raw if isinstance(item, dict)])
    return conditions


def _is_base_condition(condition: dict[str, Any]) -> bool:
    field = str(condition.get("field") or "")
    op = str(condition.get("op") or "").lower()
    if not field or field == "status_id":
        return False
    if op in {"this_month", "previous_month", "this_week", "previous_week", "last_days", "date_between", "between"}:
        return True
    if op in {"empty", "not_empty", "is_empty", "is_not_empty", "filled"}:
        return True
    field_lower = field.casefold()
    if field_lower.startswith("cf_") and op in {"eq", "in", "not_in", "like"}:
        return True
    if field in {"pipeline_id", "responsible_user_id"} and op in {"eq", "in"}:
        return True
    return False


def _apply_base_scope_to_formula(node: Any, base: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_apply_base_scope_to_formula(item, base) for item in node]
    if not isinstance(node, dict):
        return node

    result = {key: _apply_base_scope_to_formula(value, base) for key, value in node.items()}
    op = str(result.get("op") or "").lower()
    if op in {"count", "sum", "avg", "min", "max"}:
        if (result.get("from") or result.get("entity") or "leads") != base.get("from"):
            return result
        if base.get("source_id") and not result.get("source_id"):
            result["source_id"] = base.get("source_id")
        if base.get("group_by") and not result.get("group_by"):
            result["group_by"] = base.get("group_by")
        existing = _formula_conditions(result)
        where = list(result.get("where") or [])
        for condition in base.get("conditions") or []:
            if _is_temporal_condition(condition) and any(_is_temporal_condition(item) for item in existing):
                continue
            if not _has_equivalent_condition(existing, condition):
                where.append(condition)
                existing.append(condition)
        if where:
            result["where"] = where
    return result


def _has_equivalent_condition(conditions: list[dict[str, Any]], target: dict[str, Any]) -> bool:
    target_key = _condition_key(target)
    return any(_condition_key(condition) == target_key for condition in conditions)


def _is_temporal_condition(condition: dict[str, Any]) -> bool:
    op = str(condition.get("op") or "").lower()
    return op in TEMPORAL_OPS or op == "between"


def _condition_key(condition: dict[str, Any]) -> tuple[str, str, str]:
    field = str(condition.get("field") or "")
    op = str(condition.get("op") or "")
    value = condition.get("value")
    try:
        value_key = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        value_key = str(value)
    return field, op, value_key


def _ai_model_candidates(config: dict[str, str]) -> list[str]:
    primary = str(config.get("model") or "").strip()
    candidates = [primary] if primary else []
    env_key = "OPENROUTER_FALLBACK_MODELS" if config.get("provider") == "openrouter" else "AI_FALLBACK_MODELS"
    raw_fallbacks = os.getenv(env_key, os.getenv("AI_FALLBACK_MODELS", "")).strip()
    if raw_fallbacks:
        candidates.extend(item.strip() for item in raw_fallbacks.split(",") if item.strip())
    elif config.get("provider") == "openrouter":
        candidates.extend([
            "google/gemini-2.0-flash-001",
            "openai/gpt-4o-mini",
            "anthropic/claude-3.5-haiku",
        ])
    result: list[str] = []
    seen: set[str] = set()
    for model in candidates:
        if model and model not in seen:
            result.append(model)
            seen.add(model)
    return result


def _is_retryable_openrouter_policy_error(message: str) -> bool:
    lowered = message.casefold()
    retry_markers = [
        "access denied by security policy",
        "security policy",
        "ai api error 403",
        "model not found",
        "not a valid model",
    ]
    return any(marker in lowered for marker in retry_markers)


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=285) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise AiFormulaError(f"AI API error {exc.code}: {error_body[:1200]}") from exc
    except urllib.error.URLError as exc:
        raise AiFormulaError(f"AI API недоступен: {exc.reason}") from exc


def _extract_chat_content(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise AiFormulaError("AI API не вернул choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict):
                chunks.append(str(item.get("text") or item.get("content") or ""))
        return "".join(chunks)
    raise AiFormulaError("AI API не вернул текст ответа")


def _compact_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for source in sources[:80]:
        result.append(_compact_source(source))
    return result


def _compact_source(source: dict[str, Any] | None) -> dict[str, Any] | None:
    if not source:
        return None
    return {
        "id": int(source.get("id") or 0),
        "name": str(source.get("name") or ""),
        "leads_count": int(source.get("linked_leads_count") or source.get("linked_count") or 0),
        "pipeline_names": list(source.get("pipeline_names") or [])[:10],
        "status_ids": list(source.get("status_ids") or [])[:200],
        "statuses": [
            {
                "id": int(status.get("id") or 0),
                "name": str(status.get("name") or ""),
                "pipeline_id": int(status.get("pipeline_id") or 0),
            }
            for status in list(source.get("statuses") or [])[:300]
        ],
    }


BASE_FIELD_VALUES = {
    "id",
    "name",
    "pipeline_id",
    "status_id",
    "responsible_user_id",
    "price",
    "created_at",
    "updated_at",
    "closed_at",
    "created_month",
    "updated_month",
    "closed_month",
    "complete_till",
    "is_completed",
}


def _compact_dictionary(dictionary: dict[str, Any], *, user_prompt: str = "") -> dict[str, Any]:
    prompt_tokens = _text_tokens(user_prompt)
    entities = []
    for entity in dictionary.get("entities") or []:
        entity_value = str(entity.get("value") or "")
        fields = []
        for field in entity.get("fields") or []:
            if not _keep_dictionary_field(entity_value, field, prompt_tokens):
                continue
            fields.append({
                "value": field.get("value"),
                "label": field.get("label"),
                "type": field.get("type"),
                "groupable": bool(field.get("groupable")),
            })
        if entity_value == "leads":
            fields = _prioritize_fields(fields, prompt_tokens)[:140]
        else:
            fields = _prioritize_fields(fields, prompt_tokens)[:50]
        entities.append({
            "value": entity.get("value"),
            "label": entity.get("label"),
            "count": entity.get("count"),
            "fields": fields,
        })
    return {"entities": entities, "operators": dictionary.get("operators") or {}}


def _keep_dictionary_field(entity_value: str, field: dict[str, Any], prompt_tokens: set[str]) -> bool:
    value = str(field.get("value") or "")
    label = str(field.get("label") or "")
    if value in BASE_FIELD_VALUES:
        return True
    if value and value.casefold() in prompt_tokens:
        return True
    field_tokens = _text_tokens(f"{value} {label}")
    if field_tokens & prompt_tokens:
        return True
    if entity_value == "leads" and "замер" in prompt_tokens and any("замер" in token for token in field_tokens):
        return True
    if entity_value == "leads" and "дпвс" in prompt_tokens and any("дпвс" in token for token in field_tokens):
        return True
    if entity_value == "leads" and "дпес" in prompt_tokens and any("дп" in token for token in field_tokens):
        return True
    return False


def _prioritize_fields(fields: list[dict[str, Any]], prompt_tokens: set[str]) -> list[dict[str, Any]]:
    def score(field: dict[str, Any]) -> tuple[int, str]:
        value = str(field.get("value") or "")
        label = str(field.get("label") or "")
        tokens = _text_tokens(f"{value} {label}")
        points = 0
        if value in BASE_FIELD_VALUES:
            points -= 100
        points -= 10 * len(tokens & prompt_tokens)
        if "замер" in prompt_tokens and any("замер" in token for token in tokens):
            points -= 30
        if "дпвс" in prompt_tokens and any("дпвс" in token for token in tokens):
            points -= 30
        return points, label.casefold()

    return sorted(fields, key=score)


def _text_tokens(value: str) -> set[str]:
    raw = html_unescape(str(value)).casefold().replace("\u0451", "\u0435")
    return {token for token in re.findall(r"[a-z\u0430-\u044f0-9_]+", raw) if len(token) >= 2}


def html_unescape(value: str) -> str:
    return value.replace("&quot;", "\"").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
