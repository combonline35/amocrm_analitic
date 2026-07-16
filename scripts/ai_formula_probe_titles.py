r"""Временный проб: короткие человеческие title колонок + ширины от модели.

Запрос-«каша» (раньше модель пихала в title весь текст условия вида
«% (Т_замер назначен = 1 и ДПвС-…»): таблица по Менеджеру с 4 колонками.
Ожидание: title вида «Заявки», «Целевые», «Cv…», «Замеры» (короткие, без
условий и префиксов Т_/ДПвС-), table_settings.column_widths свёрнут в
объект {title: px} с разумными ширинами.

Запуск: .\.venv\Scripts\python.exe scripts\ai_formula_probe_titles.py
"""
from __future__ import annotations

import json
from pathlib import Path

from amocrm_service.config import _load_env_file
import amocrm_service.ai_formula as af

_load_env_file(Path(".env"))

PROMPT = (
    "Таблица с группировкой по кастомному полю Менеджер. "
    "Столбец 1 — количество сделок созданных в текущем месяце. "
    "Столбец 2 — количество где Целевой = 1. "
    "Столбец 3 — процент целевых. "
    "Столбец 4 — количество где Т_замер назначен = 1."
)

DICTIONARY = {
    "entities": [
        {
            "value": "leads",
            "label": "Сделки",
            "count": 80000,
            "fields": [
                {"value": "created_at", "label": "Дата создания", "type": "date", "groupable": True},
                {"value": "created_month", "label": "Месяц создания", "type": "month", "groupable": True},
                {"value": "price", "label": "Бюджет", "type": "number", "groupable": False},
                {"value": "responsible_user_id", "label": "Ответственный", "type": "user", "groupable": True},
                {"value": "cf_101", "label": "Менеджер", "type": "text", "groupable": True},
                {"value": "cf_102", "label": "Целевой", "type": "select", "groupable": True, "enums": ["1", "0"]},
                {"value": "cf_103", "label": "Т_замер назначен", "type": "select", "groupable": True, "enums": ["1", "0"]},
            ],
        }
    ],
    "operators": {},
}


def main():
    cfg = af.ai_provider_config()
    print("provider:", cfg.get("provider") or "НЕТ", "| model:", cfg.get("model") or "-",
          "| ключ:", "есть" if cfg.get("api_key") else "НЕТ")
    if not cfg.get("api_key"):
        print("Нет API-ключа — проб невозможен.")
        return
    try:
        draft = af.build_formula_draft(
            user_prompt=PROMPT,
            dictionary=DICTIONARY,
            sources=[],
            default_source=None,
        )
    except Exception as exc:
        print(f"ОШИБКА: {type(exc).__name__}: {exc}")
        return
    origin = "RULE" if draft.get("provider") == "rules" else f"MODEL({draft.get('model')})"
    formula = draft.get("formula") or {}
    columns = formula.get("columns") or {}
    print(f"источник ответа: {origin}")
    print(f"view: {draft.get('view')} | size: {draft.get('size')}")
    print(f"title виджета: {draft.get('title')!r}")
    print("title колонок:")
    for title in columns:
        print(f"  {title!r} (длина {len(title)})")
    print(f"table_settings: {json.dumps(draft.get('table_settings'), ensure_ascii=False)}")
    print(f"формула: {json.dumps(formula, ensure_ascii=False)[:600]}")


if __name__ == "__main__":
    main()
