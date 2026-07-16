r"""Временный проб: ремонт выдуманных group_by («топ-5 источников заявок»).

Два блока:
  A) детерминированная проверка _repair_group_fields — «cf_источник» должен
     замениться на реальный cf_<id> из словаря, мусорное поле — дать
     AiFormulaError, а не падение движка;
  B) живой прогон: build_formula_draft («топ-5 источников заявок») на
     реальной модели -> group_by в ответе -> engine.evaluate на мини-хабе.

Запуск: .\.venv\Scripts\python.exe scripts\ai_formula_probe_group_by.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from amocrm_service.config import _load_env_file
import amocrm_service.ai_formula as af
from amocrm_service.db import connect, init_db
from amocrm_service.formula_engine import FormulaDictionaryService, FormulaEngine
from amocrm_service.repository import Repository

_load_env_file(Path(".env"))

SOURCE_FIELD_ID = 298209
SOURCE_VALUES = ["Яндекс", "Авито", "Сайт", "ВКонтакте", "2ГИС", "Рекомендация"]


def build_repo() -> Repository:
    db_path = Path(tempfile.mkdtemp(prefix="probe_group_by_")) / "hub.sqlite3"
    init_db(db_path)
    repo = Repository(connect(db_path))

    def insert_raw(entity_type: str, entity_id: str, payload: dict, name: str = "") -> None:
        repo.conn.execute(
            """
            INSERT INTO raw_entities(entity_type, entity_id, name, payload_json, updated_at, synced_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity_type, entity_id, name, json.dumps(payload, ensure_ascii=False), 0, "2026-07-16T12:00:00+00:00"),
        )

    insert_raw(
        "lead_custom_fields",
        str(SOURCE_FIELD_ID),
        {"id": SOURCE_FIELD_ID, "name": "Источник заявки", "type": "text"},
        "Источник заявки",
    )
    for index in range(30):
        source = SOURCE_VALUES[index % len(SOURCE_VALUES)] if index % 7 else SOURCE_VALUES[0]
        insert_raw("leads", str(index + 1), {
            "id": index + 1,
            "price": 1000 + index,
            "custom_fields_values": [{
                "field_id": SOURCE_FIELD_ID,
                "field_name": "Источник заявки",
                "values": [{"value": source}],
            }],
        })
    repo.conn.commit()
    repo.rebuild_hub_indexes(["leads"])
    return repo


def collect_group_bys(node, out):
    if isinstance(node, dict):
        if node.get("group_by"):
            out.append(node["group_by"])
        for child in node.values():
            collect_group_bys(child, out)
    elif isinstance(node, list):
        for item in node:
            collect_group_bys(item, out)


def main():
    repo = build_repo()
    dictionary = FormulaDictionaryService(repo).build()
    engine = FormulaEngine(repo)

    print("=" * 78)
    print("БЛОК A — детерминированный ремонт group_by")
    print("=" * 78)
    fake = {"op": "count", "from": "leads", "group_by": "cf_источник", "limit": 5}
    repaired = af._repair_group_fields(dict(fake), dictionary)
    print(f"  cf_источник -> {repaired['group_by']}")
    result = engine.evaluate(repaired)
    print(f"  evaluate: kind={result['kind']}, строк={len(result['rows'])}")
    for row in result["rows"][:5]:
        print(f"    {row['label']}: {row['value']}")
    try:
        af._repair_group_fields({"op": "count", "from": "leads", "group_by": "cf_плотность_эфира"}, dictionary)
        print("  ОШИБКА ПРОБА: мусорное поле не отклонено")
    except af.AiFormulaError as exc:
        print(f"  мусорное поле -> AiFormulaError: {exc}")

    cfg = af.ai_provider_config()
    print()
    print("=" * 78)
    print("БЛОК B — живой прогон модели: «топ-5 источников заявок»")
    print("=" * 78)
    if not cfg.get("api_key"):
        print("Нет API-ключа — живой прогон пропущен.")
        return
    try:
        draft = af.build_formula_draft(
            user_prompt="топ-5 источников заявок",
            dictionary=dictionary,
            sources=[],
            default_source=None,
        )
    except Exception as exc:
        print(f"  ОШИБКА: {type(exc).__name__}: {exc}")
        return
    origin = "RULE" if draft.get("provider") == "rules" else f"MODEL({draft.get('model')})"
    formula = draft.get("formula")
    group_bys: list = []
    collect_group_bys(formula, group_bys)
    print(f"  источник ответа: {origin}")
    print(f"  view: {draft.get('view')}")
    print(f"  group_by в формуле: {group_bys}")
    print(f"  формула: {json.dumps(formula, ensure_ascii=False)[:500]}")
    try:
        result = engine.evaluate(formula)
    except Exception as exc:
        print(f"  evaluate УПАЛ: {type(exc).__name__}: {exc}")
        return
    rows = result.get("rows") or []
    print(f"  evaluate: kind={result['kind']}, строк={len(rows)}")
    for row in rows[:5]:
        print(f"    {row.get('label')}: {row.get('value')}")


if __name__ == "__main__":
    main()
