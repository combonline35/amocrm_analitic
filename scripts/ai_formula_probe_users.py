"""Проб: фильтрует ли МОДЕЛЬ по имени менеджера через справочник users.

Имя берётся ПРОГРАММНО из данных аккаунта donpotolok (raw_entities
entity_type='users'), без хардкода: первый пользователь с двухсловным
именем «Фамилия Имя». Два запроса: полное имя и фамилия в падеже.

Запуск: .venv\\Scripts\\python.exe scripts\\ai_formula_probe_users.py
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


def _pick_user(users: list[dict]) -> dict | None:
    for user in users:
        name = str(user.get("name") or "").strip()
        if user.get("id") and len(name.split()) == 2 and name.replace(" ", "").isalpha():
            return user
    return next((u for u in users if u.get("id") and str(u.get("name") or "").strip()), None)


def _dative(surname: str) -> str:
    """Грубый дательный падеж для проверки: "Сырцов" -> "Сырцову"."""
    if surname.lower().endswith(("в", "н")):
        return surname + "у"
    return surname


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


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = af.ai_provider_config()
    print("provider:", cfg.get("provider") or "НЕТ", "| model:", cfg.get("model") or "-",
          "| ключ:", "есть" if cfg.get("api_key") else "НЕТ")

    repo = Repository(connect(Path(DB)))
    dictionary = FormulaDictionaryService(repo).build()
    users = repo.all_payloads("users")
    compact = af._compact_users(users)
    print(f"users в аккаунте: {len(users)} | в компактный справочник попало: {len(compact)}")
    print("справочник:", json.dumps(compact, ensure_ascii=False))

    target = _pick_user(compact)
    if not target:
        print("Пользователь с именем не найден — нечего проверять.")
        return
    user_id, name = target["id"], target["name"]
    surname = name.split()[0]
    print(f"тестовый менеджер: id={user_id} name={name!r} | фамилия={surname!r}")

    prompts = [
        f"сколько сделок за текущий месяц у менеджера {name}",
        f"сделки по {_dative(surname)} за месяц",
    ]

    if not cfg.get("api_key"):
        print("Нет API-ключа (.env) — модельный проход невозможен, пропускаю.")
        return

    # Зануляем rule-заготовки на рантайме, чтобы запрос ушёл в модель.
    for rule_name in (
        "_measurement_conversion_draft",
        "_lost_reason_ad_platform_current_month_draft",
        "_lost_reason_current_month_draft",
        "_measurement_assigned_count_draft",
        "_simple_count_draft",
    ):
        setattr(af, rule_name, lambda **kwargs: None)

    for prompt in prompts:
        print(f"\n[{prompt}]")
        try:
            draft = None
            for attempt in range(3):
                try:
                    draft = af.build_formula_draft(
                        user_prompt=prompt,
                        dictionary=dictionary,
                        sources=[],
                        default_source=None,
                        users=users,
                    )
                    break
                except af.AiFormulaError as exc:
                    if "недоступен" not in str(exc) or attempt == 2:
                        raise
        except Exception as exc:
            print(f"  ОШИБКА: {exc}")
            continue
        conds = []
        _walk_conditions(draft.get("formula"), conds)
        hits = [c for c in conds if str(c.get("field")) == "responsible_user_id"]
        id_ok = any(
            str(user_id) in [str(v) for v in (c.get("value") if isinstance(c.get("value"), list) else [c.get("value")])]
            for c in hits
        )
        print(f"  responsible_user_id фильтр: {'да' if hits else 'НЕТ'} | правильный id ({user_id}): {'да' if id_ok else 'НЕТ'}")
        for c in conds:
            print(f"  условие: {c.get('field')} {c.get('op')}={c.get('value')}")
        print(f"  формула: {json.dumps(draft.get('formula'), ensure_ascii=False)[:300]}")


if __name__ == "__main__":
    main()
