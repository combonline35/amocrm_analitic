from __future__ import annotations

import pytest

from amocrm_service.ai_formula import (
    AiFormulaError,
    _clean_formula,
    _compact_dictionary,
    _inherit_table_base_conditions,
    _repair_group_fields,
    _simple_count_draft,
)


def _dict_with_field(field: dict) -> dict:
    return {
        "entities": [
            {"value": "leads", "label": "Сделки", "count": 10, "fields": [field]}
        ],
        "operators": {},
    }


def test_compact_dictionary_includes_enum_values():
    field = {
        "value": "cf_1",
        "label": "Категория",
        "type": "text",
        "groupable": True,
        "enums": ["A", "B", "C"],
    }
    compact = _compact_dictionary(_dict_with_field(field), user_prompt="разбивка по категория")
    fields = compact["entities"][0]["fields"]
    assert len(fields) == 1
    assert fields[0]["values"] == ["A", "B", "C"]


def test_compact_dictionary_limits_enum():
    enums = [str(i) for i in range(30)]
    field = {
        "value": "cf_2",
        "label": "Статус",
        "type": "text",
        "groupable": True,
        "enums": enums,
    }
    compact = _compact_dictionary(_dict_with_field(field), user_prompt="фильтр по статус")
    fields = compact["entities"][0]["fields"]
    assert len(fields[0]["values"]) == 20
    assert fields[0]["values"] == enums[:20]


def test_compact_dictionary_keeps_groupable_by_morphology():
    # «топ-5 источников заявок» должен оставить в словаре groupable-поле
    # «Источник заявки» — падежи матчатся по префиксу основы.
    field = {
        "value": "cf_298209",
        "label": "Источник заявки",
        "type": "text",
        "groupable": True,
    }
    compact = _compact_dictionary(_dict_with_field(field), user_prompt="топ-5 источников заявок")
    fields = compact["entities"][0]["fields"]
    assert any(item["value"] == "cf_298209" for item in fields)


def test_non_select_no_values():
    field = {
        "value": "cf_3",
        "label": "Комментарий",
        "type": "text",
        "groupable": True,
    }
    compact = _compact_dictionary(_dict_with_field(field), user_prompt="фильтр по комментарий")
    fields = compact["entities"][0]["fields"]
    assert len(fields) == 1
    assert "values" not in fields[0]


DICTIONARY = {
    "entities": [
        {
            "value": "leads",
            "label": "Сделки",
            "count": 80000,
            "fields": [
                {"value": "created_at", "label": "Дата создания", "type": "date", "groupable": True},
                {"value": "created_month", "label": "Месяц создания", "type": "month", "groupable": True},
            ],
        }
    ],
    "operators": {},
}


def _draft(prompt: str):
    return _simple_count_draft(user_prompt=prompt, dictionary=DICTIONARY, default_source=None)


def test_simple_count_defers_named_month():
    # Названный месяц заготовка не разбирает — отдаёт запрос модели (None).
    assert _draft("посчитай количество сделок за июль 2026") is None


def test_simple_count_defers_named_month_without_year():
    # Проходит оба гарда (есть "посчитай"+"сделки"), но названный месяц -> defer.
    assert _draft("посчитай сделки за июнь") is None


def test_simple_count_defers_year():
    assert _draft("количество сделок за 2025 год") is None


def test_simple_count_still_handles_current_month():
    # Регресс-защита: работающий путь "текущий месяц" остаётся у заготовки.
    draft = _draft("сколько сделок в текущем месяце")
    assert draft is not None
    where = draft["formula"].get("where") or []
    assert any(c.get("field") == "created_at" and c.get("op") == "this_month" for c in where)


def test_simple_count_defers_when_field_condition():
    # Явное условие по полю ("поле ... заполнено 1") заготовка не умеет —
    # уступает модели, даже если остальная форма запроса ей знакома.
    assert _draft(
        "Посчитай количество сделок созданных в текущий месяц у которых поле Целевой заполнено 1"
    ) is None


def test_simple_count_defers_field_equals():
    assert _draft("сколько сделок где Статус = 1") is None


def test_simple_count_defers_manager_mention():
    # Фильтр по конкретному менеджеру заготовка не умеет — уступает модели,
    # у которой есть справочник users.
    assert _draft("сколько сделок за текущий месяц у менеджера Сырцов") is None


def test_simple_count_defers_responsible():
    assert _draft("сделки по ответственному Иванову") is None


def test_simple_count_still_handles_responsible_group():
    # Разрез "по ответственным" — группировка, остаётся на заготовке.
    draft = _draft("сколько сделок по ответственным")
    assert draft is not None
    assert draft["formula"].get("group_by") == "responsible_user_id"


def test_simple_count_still_handles_plain():
    # Регресс-защита: без условий по полю заготовка отвечает сама.
    draft = _draft("сколько сделок в текущем месяце")
    assert draft is not None
    assert draft["provider"] == "rules"
    where = draft["formula"].get("where") or []
    assert any(c.get("field") == "created_at" and c.get("op") == "this_month" for c in where)


def test_simple_count_still_handles_previous_month():
    draft = _draft("сколько сделок за прошлый месяц")
    assert draft is not None
    where = draft["formula"].get("where") or []
    assert any(c.get("field") == "created_at" and c.get("op") == "previous_month" for c in where)


def _count_column(where: list[dict]) -> dict:
    return {"op": "count", "from": "leads", "group_by": "responsible_user_id", "where": where}


THIS_MONTH = {"field": "created_at", "op": "this_month", "value": None}
CF_FILTER = {"field": "cf_1", "op": "eq", "value": "1"}


def test_inherit_keeps_column_specific_filter():
    # cf-фильтр специфичной колонки НЕ размножается: наследуется только
    # пересечение условий всех агрегатных колонок (здесь — только период).
    table = {
        "op": "table",
        "columns": {
            "Все сделки": _count_column([dict(THIS_MONTH)]),
            "Целевые": _count_column([dict(THIS_MONTH), dict(CF_FILTER)]),
        },
    }
    result = _inherit_table_base_conditions(table)
    all_where = result["columns"]["Все сделки"].get("where") or []
    target_where = result["columns"]["Целевые"].get("where") or []
    assert not any(str(c.get("field", "")).startswith("cf_") for c in all_where)
    assert [c.get("op") for c in all_where] == ["this_month"]
    assert any(str(c.get("field", "")).startswith("cf_") for c in target_where)


def test_inherit_propagates_common_period():
    # Пересечение пустое (у первой колонки нет условий) — ничего не наследуем:
    # колонка «все сделки» не получает чужой период.
    table = {
        "op": "table",
        "columns": {
            "Все сделки": _count_column([]),
            "За месяц": _count_column([dict(THIS_MONTH)]),
        },
    }
    result = _inherit_table_base_conditions(table)
    assert not (result["columns"]["Все сделки"].get("where") or [])
    assert [c.get("op") for c in result["columns"]["За месяц"].get("where") or []] == ["this_month"]


def test_inherit_single_column_unchanged():
    table = {
        "op": "table",
        "columns": {
            "Целевые": _count_column([dict(THIS_MONTH), dict(CF_FILTER)]),
        },
    }
    result = _inherit_table_base_conditions(table)
    assert result == table


def test_clean_formula_moves_value_to_const():
    # Привычка модели: value:100 в multiply-узле с пустым right — авторемонт
    # переносит константу в полноценный const-узел до валидации.
    raw = {
        "op": "multiply",
        "left": {"op": "divide", "left": {"op": "count", "from": "leads"}, "right": {"op": "count", "from": "leads"}},
        "right": None,
        "value": 100,
    }
    cleaned = _clean_formula(raw)
    assert cleaned["right"] == {"op": "const", "value": 100}
    assert "value" not in cleaned
    assert cleaned["left"]["op"] == "divide"


def test_clean_formula_drops_extra_value_when_sides_filled():
    raw = {
        "op": "multiply",
        "left": {"op": "count", "from": "leads"},
        "right": {"op": "const", "value": 100},
        "value": 100,
    }
    cleaned = _clean_formula(raw)
    assert cleaned["right"] == {"op": "const", "value": 100}
    assert "value" not in cleaned


DIVIDE_RATIO = {
    "op": "divide",
    "left": {"op": "count", "from": "leads", "where": [{"field": "cf_1", "op": "eq", "value": "1"}]},
    "right": {"op": "count", "from": "leads"},
}


def test_table_percent_column_returns_ratio():
    # Конвенция: колонка таблицы отдаёт долю, фронт сам умножает на 100.
    raw = {
        "op": "table",
        "columns": [
            {
                "title": "Конверсия, %",
                "formula": {"op": "multiply", "left": dict(DIVIDE_RATIO), "right": {"op": "const", "value": 100}},
            },
        ],
    }
    cleaned = _clean_formula(raw)
    column = cleaned["columns"]["Конверсия, %"]
    assert column["op"] == "divide"
    assert column["left"]["op"] == "count"


def test_scalar_percent_keeps_multiply():
    # Скалярный ответ (корень не table) — ×100 легитимен, не трогаем.
    raw = {"op": "multiply", "left": dict(DIVIDE_RATIO), "right": {"op": "const", "value": 100}}
    cleaned = _clean_formula(raw)
    assert cleaned["op"] == "multiply"
    assert cleaned["right"] == {"op": "const", "value": 100}
    assert cleaned["left"]["op"] == "divide"


GROUP_DICTIONARY = {
    "entities": [
        {
            "value": "leads",
            "label": "Сделки",
            "count": 100,
            "fields": [
                {"value": "cf_298209", "label": "Источник", "type": "text", "groupable": True},
                {"value": "responsible_user_id", "label": "Ответственный", "type": "user", "groupable": True},
                {"value": "created_month", "label": "Месяц создания", "type": "month", "groupable": True},
            ],
        }
    ],
    "operators": {},
}


def test_group_by_repair_by_label():
    formula = {"op": "count", "from": "leads", "group_by": "cf_источник"}
    repaired = _repair_group_fields(formula, GROUP_DICTIONARY)
    assert repaired["group_by"] == "cf_298209"


def test_group_by_repair_by_label_morphology():
    # Падежная форма ("источникам") тоже должна находить поле «Источник».
    formula = {"op": "count", "from": "leads", "group_by": "cf_источникам"}
    repaired = _repair_group_fields(formula, GROUP_DICTIONARY)
    assert repaired["group_by"] == "cf_298209"


def test_group_by_repair_keeps_real_fields():
    formula = {"op": "count", "from": "leads", "group_by": ["cf_298209", "created_month"]}
    repaired = _repair_group_fields(formula, GROUP_DICTIONARY)
    assert repaired["group_by"] == ["cf_298209", "created_month"]


def test_group_by_repair_inside_table_columns():
    formula = {
        "op": "table",
        "columns": {
            "Заявки": {"op": "count", "from": "leads", "group_by": "cf_источник"},
        },
    }
    repaired = _repair_group_fields(formula, GROUP_DICTIONARY)
    assert repaired["columns"]["Заявки"]["group_by"] == "cf_298209"


def test_group_by_unknown_gives_validation_error():
    formula = {"op": "count", "from": "leads", "group_by": "cf_плотность_эфира"}
    with pytest.raises(AiFormulaError) as excinfo:
        _repair_group_fields(formula, GROUP_DICTIONARY)
    assert "не найдено" in str(excinfo.value)
