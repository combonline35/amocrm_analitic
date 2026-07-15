"""Проб: различает ли МОДЕЛЬ плавающие и фиксированные периоды.

Плавающие («текущий месяц», «прошлый месяц») должны стать пресетами
this_month/previous_month (пересчитываются сами), а названные месяц/год
(«июль 2026», «2025 год») — фиксированными date_between / eq "ГГГГ-ММ".

Словарь реальный (donpotolok) — в последнем запросе участвует select-поле.
Rule-заготовки отключаются, проверяется чистый ответ модели.

Запуск: .venv\\Scripts\\python.exe scripts\\ai_formula_probe_dates.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from amocrm_service.config import _load_env_file
from amocrm_service.db import connect
from amocrm_service.formula_engine import FormulaDictionaryService
from amocrm_service.repository import Repository
import amocrm_service.ai_formula as af

_load_env_file(Path(".env"))

DB = "data/users/default/accounts/donpotolok/hub.sqlite3"

FLOATING_OPS = {"this_month", "previous_month", "this_week", "previous_week", "last_days"}
FIXED_OPS = {"date_between", "between", "eq"}

# (запрос, ожидание: "floating" | "fixed")
CASES = [
    ("сколько сделок за текущий месяц", "floating"),
    ("сколько сделок в этом месяце", "floating"),
    ("сколько сделок за прошлый месяц", "floating"),
    ("сколько сделок за июль 2026", "fixed"),
    ("сколько сделок за 2025 год", "fixed"),
    (
        "Посчитай количество сделок созданных в текущий месяц у которых поле Целевой заполнено 1",
        "floating",
    ),
]


def _walk_conditions(node, out):
    if isinstance(node, dict):
        for key in ("where", "filters"):
            for cond in node.get(key) or []:
                if isinstance(cond, dict):
                    out.append(cond)
        for key in ("left", "right", "base"):
            if key in node:
                _walk_conditions(node[key], out)
        cols = node.get("columns")
        if isinstance(cols, dict):
            for col in cols.values():
                _walk_conditions(col, out)
        elif isinstance(cols, list):
            for col in cols:
                if isinstance(col, dict):
                    _walk_conditions(col.get("formula") or col, out)
    elif isinstance(node, list):
        for item in node:
            _walk_conditions(item, out)


def _temporal_conditions(conds):
    result = []
    for c in conds:
        op = str(c.get("op") or "")
        field = str(c.get("field") or "")
        if op in FLOATING_OPS or op in {"date_between", "between"}:
            result.append(c)
        elif op == "eq" and ("month" in field or "date" in field or "_at" in field):
            result.append(c)
    return result


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = af.ai_provider_config()
    print("provider:", cfg.get("provider") or "НЕТ", "| model:", cfg.get("model") or "-",
          "| ключ:", "есть" if cfg.get("api_key") else "НЕТ")
    if not cfg.get("api_key"):
        print("Нет API-ключа (.env) — модельный проход невозможен, выхожу.")
        return

    repo = Repository(connect(Path(DB)))
    dictionary = FormulaDictionaryService(repo).build()

    # Зануляем rule-заготовки на рантайме, чтобы запрос ушёл в модель.
    for rule_name in (
        "_measurement_conversion_draft",
        "_lost_reason_ad_platform_current_month_draft",
        "_lost_reason_current_month_draft",
        "_measurement_assigned_count_draft",
        "_simple_count_draft",
    ):
        setattr(af, rule_name, lambda **kwargs: None)

    rows = []
    for prompt, expected in CASES:
        try:
            draft = None
            for attempt in range(3):
                try:
                    draft = af.build_formula_draft(
                        user_prompt=prompt,
                        dictionary=dictionary,
                        sources=[],
                        default_source=None,
                    )
                    break
                except af.AiFormulaError as exc:
                    if "недоступен" not in str(exc) or attempt == 2:
                        raise
        except Exception as exc:
            rows.append((prompt, expected, f"ОШИБКА: {exc}", None, False))
            continue
        conds = []
        _walk_conditions(draft.get("formula"), conds)
        temporal = _temporal_conditions(conds)
        if temporal:
            got = "; ".join(
                f"{c.get('field')} {c.get('op')}" + (f"={c.get('value')}" if c.get("value") not in (None, "", []) else "")
                for c in temporal
            )
            kind = "floating" if all(str(c.get("op")) in FLOATING_OPS for c in temporal) else "fixed"
        else:
            got, kind = "нет фильтра периода", None
        has_cf = any(str(c.get("field")).startswith("cf_") for c in conds)
        ok = kind == expected
        rows.append((prompt, expected, got, kind, ok, has_cf))
        print(f"\n[{prompt}]")
        print(f"  период: {got}")
        print(f"  тип: {kind or '-'} | ожидали: {expected} | {'ВЕРНО' if ok else 'НЕВЕРНО'}")
        if "Целевой" in prompt:
            print(f"  cf-фильтр: {'да' if has_cf else 'НЕТ'}")
        print(f"  формула: {json.dumps(draft.get('formula'), ensure_ascii=False)[:300]}")

    ok_count = sum(1 for r in rows if len(r) > 4 and r[4])
    print("\n" + "=" * 78)
    print(f"ИТОГ: {ok_count}/{len(rows)} запросов получили правильный тип периода")


if __name__ == "__main__":
    main()
