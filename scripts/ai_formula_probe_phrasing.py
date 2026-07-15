"""Проб: понимает ли МОДЕЛЬ естественные формулировки фильтра по select-полю.

Батарея из 7 формулировок одного и того же условия («поле = значение»):
=, равно, заполнено X, заполнен значением X, стоит X, = единица (число словом),
падежная форма названия поля («целевым»).

Поле и значение берутся из РЕАЛЬНОГО словаря donpotolok программно, без
хардкода: предпочитается select-поле с ОДНОСЛОВНЫМ label (>=5 букв) и
цифровыми enum-значениями — такое поле нагружает и падеж, и гейт словаря.

Запуск: .venv\\Scripts\\python.exe scripts\\ai_formula_probe_phrasing.py
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

NUMBER_WORDS = {"0": "ноль", "1": "единица", "2": "два", "3": "три"}


def _find_select_field(dictionary):
    fallback = None
    for entity in dictionary.get("entities") or []:
        if entity.get("value") != "leads":
            continue
        for field in entity.get("fields") or []:
            enums = field.get("enums")
            if not enums:
                continue
            fallback = fallback or field
            label = str(field.get("label") or "").strip()
            has_digit_enum = any(str(v).isdigit() for v in enums)
            if len(label.split()) == 1 and len(label) >= 5 and label.isalpha() and has_digit_enum:
                return field
    return fallback


def _inflect(label: str) -> str:
    """Грубая падежная форма последнего слова: "Целевой" -> "Целевым"."""
    parts = label.split()
    word = parts[-1]
    lower = word.lower()
    if lower.endswith(("ой", "ый")):
        parts[-1] = word[:-2] + "ым"
    elif lower.endswith("ий"):
        parts[-1] = word[:-2] + "им"
    elif lower.endswith(("а", "я")):
        parts[-1] = word[:-1] + "ой"
    else:
        parts[-1] = word + "ом"
    return " ".join(parts)


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


def _field_in_compact_dictionary(dictionary, prompt: str, cf: str) -> bool:
    compact = af._compact_dictionary(dictionary, user_prompt=prompt)
    for entity in compact.get("entities") or []:
        for field in entity.get("fields") or []:
            if field.get("value") == cf:
                return True
    return False


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = af.ai_provider_config()
    print("provider:", cfg.get("provider") or "НЕТ", "| model:", cfg.get("model") or "-",
          "| ключ:", "есть" if cfg.get("api_key") else "НЕТ")

    repo = Repository(connect(Path(DB)))
    dictionary = FormulaDictionaryService(repo).build()
    field = _find_select_field(dictionary)
    if not field:
        print("select-поле с enum в словаре не найдено — нечего проверять.")
        return
    cf, label = field["value"], field["label"]
    value = str(field["enums"][0])
    value_word = NUMBER_WORDS.get(value, value)
    inflected = _inflect(label)
    print(f"тестовое поле: {cf} '{label}' | значение={value!r} | падеж='{inflected}'")

    prompts = [
        f"сколько сделок где {label} = {value}",
        f"сколько сделок где {label} равно {value}",
        f"сколько сделок где {label} заполнено {value}",
        f"сколько сделок где {label} заполнен значением {value}",
        f"сколько сделок где {label} стоит {value}",
        f"сколько сделок где {label} = {value_word}",
        f"сколько сделок с {inflected} равным {value}",
    ]

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

    results = []
    for prompt in prompts:
        in_dict = _field_in_compact_dictionary(dictionary, prompt, cf)
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
                    # сетевые сбои (SSL/timeout) ретраим, содержательные ошибки — нет
                    if "недоступен" not in str(exc) or attempt == 2:
                        raise
            conds = []
            _walk_conditions(draft.get("formula"), conds)
            hits = [c for c in conds if str(c.get("field")) == cf]
            eq_hit = any(
                str(c.get("op")) in {"eq", "in"} and value in [str(v) for v in (c.get("value") if isinstance(c.get("value"), list) else [c.get("value")])]
                for c in hits
            )
            detail = "; ".join(f"{c.get('field')} {c.get('op')}={c.get('value')}" for c in conds) or "нет условий"
        except Exception as exc:
            hits, eq_hit, detail = [], False, f"ОШИБКА: {exc}"
        results.append((prompt, in_dict, bool(hits), eq_hit, detail))
        print(f"\n[{prompt}]")
        print(f"  поле в словаре модели: {'да' if in_dict else 'НЕТ'}")
        print(f"  cf-фильтр: {'да' if hits else 'НЕТ'} | eq по значению: {'да' if eq_hit else 'НЕТ'}")
        print(f"  условия: {detail}")

    ok = sum(1 for r in results if r[3])
    print("\n" + "=" * 78)
    print(f"ИТОГ: {ok}/{len(results)} формулировок ставят cf-фильтр eq по значению")
    print(json.dumps(
        [{"prompt": p, "field_in_dict": d, "cf_filter": f, "eq_value": e} for p, d, f, e, _ in results],
        ensure_ascii=False, indent=1,
    ))


if __name__ == "__main__":
    main()
