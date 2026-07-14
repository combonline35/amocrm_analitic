"""Проб: ставит ли МОДЕЛЬ фильтр по кастомному select-полю значением из enum.

Берёт РЕАЛЬНЫЙ словарь donpotolok, находит первое select-поле с enum
программно (без хардкода), формирует запрос "сколько сделок где <label> =
<значение из enum>" и проверяет, что модель кладёт cf_<id> eq <value>.

Запуск: .\.venv\Scripts\python.exe scripts\ai_formula_probe_select.py
"""
from __future__ import annotations

import json
from pathlib import Path

from amocrm_service.config import _load_env_file
from amocrm_service.db import connect
from amocrm_service.formula_engine import FormulaDictionaryService
from amocrm_service.repository import Repository
import amocrm_service.ai_formula as af

_load_env_file(Path(".env"))

DB = "data/users/default/accounts/donpotolok/hub.sqlite3"


def _find_select_field(dictionary):
    for entity in dictionary.get("entities") or []:
        if entity.get("value") != "leads":
            continue
        for field in entity.get("fields") or []:
            enums = field.get("enums")
            if enums:
                return field, enums
    return None, None


def _walk_conditions(node, out):
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


def main():
    cfg = af.ai_provider_config()
    print("provider:", cfg.get("provider") or "НЕТ", "| model:", cfg.get("model") or "-",
          "| ключ:", "есть" if cfg.get("api_key") else "НЕТ")

    repo = Repository(connect(Path(DB)))
    dictionary = FormulaDictionaryService(repo).build()
    field, enums = _find_select_field(dictionary)
    if not field:
        print("select-поле с enum в словаре не найдено — нечего проверять.")
        return
    cf, label, value = field["value"], field["label"], str(enums[0])
    print(f"тестовое поле: {cf} '{label}' | enum[0]={value!r} | значений в поле={len(enums)}")

    if not cfg.get("api_key"):
        print("Нет API-ключа (.env) — модельный проход невозможен, пропускаю.")
        return

    # Зануляем rule-заготовки на рантайме, чтобы запрос ушёл в модель.
    for name in (
        "_measurement_conversion_draft",
        "_lost_reason_ad_platform_current_month_draft",
        "_lost_reason_current_month_draft",
        "_measurement_assigned_count_draft",
        "_simple_count_draft",
    ):
        setattr(af, name, lambda **kwargs: None)

    prompt = f"сколько сделок где {label} = {value}"
    print(f"\nзапрос: {prompt}")
    draft = af.build_formula_draft(
        user_prompt=prompt,
        dictionary=dictionary,
        sources=[],
        default_source=None,
    )
    formula = draft.get("formula")
    provider = draft.get("provider")
    model = draft.get("model")
    print("источник ответа:", "RULE" if provider == "rules" else f"MODEL({model})")
    print("формула:", json.dumps(formula, ensure_ascii=False)[:500])

    conds = []
    _walk_conditions(formula, conds)
    hit = [c for c in conds if str(c.get("field")) == cf]
    print(f"\nСтавит фильтр по {cf}? {'ДА' if hit else 'НЕТ'}")
    for c in conds:
        print("  условие:", c.get("field"), c.get("op"), "=", c.get("value"))


if __name__ == "__main__":
    main()
