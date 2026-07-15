from __future__ import annotations

from amocrm_service.ai_formula import _compact_dictionary, _simple_count_draft


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
