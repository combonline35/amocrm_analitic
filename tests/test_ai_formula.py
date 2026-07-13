from __future__ import annotations

from amocrm_service.ai_formula import _simple_count_draft


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


def test_simple_count_still_handles_previous_month():
    draft = _draft("сколько сделок за прошлый месяц")
    assert draft is not None
    where = draft["formula"].get("where") or []
    assert any(c.get("field") == "created_at" and c.get("op") == "previous_month" for c in where)
