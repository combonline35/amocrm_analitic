"""Временный проб: как build_formula_draft разбирает период в запросах.

Два прохода:
  A) как есть — видно, что перехватили rule-заготовки, а что ушло в модель;
  B) rule-заготовки занулены (monkeypatch на рантайме, файл не меняется) —
     чистый ответ LLM, чтобы увидеть, ставит ли МОДЕЛЬ фильтр для
     "июль 2026", "2025 год", "с 1 по 15 марта".

Запуск: .\.venv\Scripts\python.exe scripts\ai_formula_probe.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from amocrm_service.config import _load_env_file
import amocrm_service.ai_formula as af

_load_env_file(Path(".env"))

PROMPTS = [
    "посчитай количество сделок созданных за июль 2026",
    "сколько сделок создано в текущем месяце",
    "сколько сделок за прошлый месяц",
    "количество сделок за 2025 год",
    "сделки с 1 по 15 марта 2026",
    "сумма сделок за июнь",
    "сколько сделок в воронке продажи",
]

# Минимальный, но реалистичный словарь: сущность leads + основные поля.
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
                {"value": "status_id", "label": "Этап", "type": "status", "groupable": True},
                {"value": "pipeline_id", "label": "Воронка", "type": "pipeline", "groupable": True},
                {"value": "responsible_user_id", "label": "Ответственный", "type": "user", "groupable": True},
                {"value": "cf_100500", "label": "Дата договора", "type": "date", "groupable": True},
            ],
        }
    ],
    "operators": {},
}
SOURCES: list[dict] = []


def _walk_conditions(node, out):
    """Собрать все where-условия из (возможно вложенной) формулы."""
    if isinstance(node, dict):
        for cond in node.get("where") or []:
            if isinstance(cond, dict):
                out.append(cond)
        for key in ("left", "right", "base"):
            if key in node:
                _walk_conditions(node[key], out)
        for col in node.get("columns") or []:
            if isinstance(col, dict):
                _walk_conditions(col.get("formula") or col, out)
    elif isinstance(node, list):
        for item in node:
            _walk_conditions(item, out)


def _summarize_period(formula) -> str:
    conds = []
    _walk_conditions(formula, conds)
    if not conds:
        return "НЕТ фильтров (считает всё)"
    parts = []
    for c in conds:
        field = c.get("field")
        op = c.get("op") or c.get("operator")
        val = c.get("value")
        parts.append(f"{field} {op}" + (f"={val}" if val not in (None, "", []) else ""))
    return "; ".join(parts)


def run_pass(label: str):
    print("=" * 78)
    print(label)
    print("=" * 78)
    for prompt in PROMPTS:
        try:
            draft = af.build_formula_draft(
                user_prompt=prompt,
                dictionary=DICTIONARY,
                sources=SOURCES,
                default_source=None,
            )
        except Exception as exc:
            print(f"\n[{prompt}]\n  ОШИБКА: {type(exc).__name__}: {exc}")
            continue
        provider = draft.get("provider")
        model = draft.get("model")
        origin = "RULE" if provider == "rules" else f"MODEL({model})"
        formula = draft.get("formula")
        print(f"\n[{prompt}]")
        print(f"  источник ответа: {origin}")
        print(f"  период/фильтр:   {_summarize_period(formula)}")
        print(f"  формула: {json.dumps(formula, ensure_ascii=False)[:400]}")


def main():
    cfg = af.ai_provider_config()
    print("provider:", cfg.get("provider") or "НЕТ", "| model:", cfg.get("model") or "-",
          "| ключ:", "есть" if cfg.get("api_key") else "НЕТ")
    if not cfg.get("api_key"):
        print("Нет API-ключа — модельный проход невозможен. Прогоняю только rule-проход.")

    run_pass("ПРОХОД A — как есть (rule-заготовки активны)")

    if cfg.get("api_key"):
        # Зануляем rule-заготовки на рантайме — файл на диске не меняется.
        for name in (
            "_measurement_conversion_draft",
            "_lost_reason_ad_platform_current_month_draft",
            "_lost_reason_current_month_draft",
            "_measurement_assigned_count_draft",
            "_simple_count_draft",
        ):
            setattr(af, name, lambda **kwargs: None)
        run_pass("ПРОХОД B — rule-заготовки ОТКЛЮЧЕНЫ (чистый ответ модели)")


if __name__ == "__main__":
    main()
