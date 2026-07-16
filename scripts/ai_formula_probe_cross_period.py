r"""Временный проб: конверсия с ДВУМЯ разными периодами (диагностика).

Числитель — по дате замера («ДПвС-ЗАМЕР НАЗНАЧЕН» в текущем месяце, БЕЗ
фильтра по дате создания), знаменатель — по дате создания. Выясняем, где
рвётся: модель путает периоды, наши ремонты навязывают период, валидация
или evaluate.

Прогоняет 3 промпта (2 из ТЗ + шаблон конструктора колонок) через
build_formula_draft с default_source «New Продажи» (id=1) и users, затем
engine.evaluate на мини-хабе. Для каждого печатает: происхождение ответа,
JSON формулы, ошибку (если упало) и результат.

Запуск: .\.venv\Scripts\python.exe scripts\ai_formula_probe_cross_period.py
"""
from __future__ import annotations

import json
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from amocrm_service.config import _load_env_file
import amocrm_service.ai_formula as af
from amocrm_service.db import connect, init_db
from amocrm_service.formula_engine import FormulaDictionaryService, FormulaEngine
from amocrm_service.repository import Repository

_load_env_file(Path(".env"))

CF_MEASURE_FLAG = 297941   # Т_замер назначен (select 1/0)
CF_MEASURE_DATE = 297942   # ДПвС-ЗАМЕР НАЗНАЧЕН (datetime)
CF_TARGET = 297943         # Целевой (select 1/0)
CF_MANAGER = 297945        # Менеджер (text)

PROMPT_1 = (
    'процент: (количество сделок где Т_замер назначен = 1 и поле "ДПвС-ЗАМЕР НАЗНАЧЕН" '
    'в текущем месяце, без фильтра по дате создания) делить на '
    '(количество сделок созданных в текущем месяце где Целевой = 1)'
)
PROMPT_2 = (
    'divide: left = количество сделок где Т_замер назначен = 1 и поле "ДПвС-ЗАМЕР НАЗНАЧЕН" '
    'в текущем месяце без фильтра по дате создания, '
    'right = количество сделок созданных в текущем месяце где Целевой = 1'
)
PROMPT_3 = (
    f"{PROMPT_1}. Верни ОДНУ агрегатную формулу (count/sum/avg/divide) "
    "с группировкой по полю Менеджер (cf_297945), без таблицы (op не table)."
)

USERS = [
    {"id": 10, "name": "Сырцов Иван"},
    {"id": 11, "name": "Конкина Ольга"},
]


def build_repo() -> Repository:
    db_path = Path(tempfile.mkdtemp(prefix="probe_cross_period_")) / "hub.sqlite3"
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

    for field_id, field_name, field_type in [
        (CF_MEASURE_FLAG, "Т_замер назначен", "select"),
        (CF_MEASURE_DATE, "ДПвС-ЗАМЕР НАЗНАЧЕН", "date_time"),
        (CF_TARGET, "Целевой", "select"),
        (CF_MANAGER, "Менеджер", "text"),
    ]:
        payload = {"id": field_id, "name": field_name, "type": field_type}
        if field_type == "select":
            payload["enums"] = [{"id": 1, "value": "1"}, {"id": 2, "value": "0"}]
        insert_raw("lead_custom_fields", str(field_id), payload, field_name)

    now = datetime.now(timezone.utc)
    this_month = int(now.replace(day=5, hour=12, minute=0, second=0, microsecond=0).timestamp())
    prev_month = int((now.replace(day=1) - timedelta(days=10)).timestamp())

    def cf(field_id, value):
        return {"field_id": field_id, "values": [{"value": value}]}

    leads = [
        # создана в этом месяце, целевая, замер назначен в этом месяце
        {"id": 1, "created_at": this_month, "pipeline_id": 777, "status_id": 10,
         "custom_fields_values": [cf(CF_TARGET, "1"), cf(CF_MEASURE_FLAG, "1"), cf(CF_MEASURE_DATE, this_month), cf(CF_MANAGER, "Сырцов")]},
        # КЛЮЧЕВОЙ КЕЙС: создана в ПРОШЛОМ месяце, замер назначен в ЭТОМ
        {"id": 2, "created_at": prev_month, "pipeline_id": 777, "status_id": 10,
         "custom_fields_values": [cf(CF_TARGET, "1"), cf(CF_MEASURE_FLAG, "1"), cf(CF_MEASURE_DATE, this_month), cf(CF_MANAGER, "Сырцов")]},
        # создана в этом месяце, целевая, без замера
        {"id": 3, "created_at": this_month, "pipeline_id": 777, "status_id": 10,
         "custom_fields_values": [cf(CF_TARGET, "1"), cf(CF_MANAGER, "Конкина")]},
        # создана в этом месяце, НЕ целевая
        {"id": 4, "created_at": this_month, "pipeline_id": 777, "status_id": 10,
         "custom_fields_values": [cf(CF_TARGET, "0"), cf(CF_MANAGER, "Конкина")]},
    ]
    for lead in leads:
        insert_raw("leads", str(lead["id"]), lead)
    repo.conn.commit()
    repo.rebuild_hub_indexes(["leads"])
    return repo


def run_case(label, prompt, dictionary, sources, default_source, engine):
    print("=" * 78)
    print(label)
    print("=" * 78)
    print(f"промпт: {prompt}")
    try:
        draft = af.build_formula_draft(
            user_prompt=prompt,
            dictionary=dictionary,
            sources=sources,
            default_source=default_source,
            users=USERS,
        )
    except af.AiFormulaError as exc:
        print(f"\n>>> УПАЛО НА ГЕНЕРАЦИИ/ВАЛИДАЦИИ: AiFormulaError: {exc}")
        return
    except Exception:
        print("\n>>> УПАЛО НА ГЕНЕРАЦИИ (traceback):")
        traceback.print_exc()
        return
    origin = "RULE" if draft.get("provider") == "rules" else f"MODEL({draft.get('model')})"
    formula = draft.get("formula")
    print(f"источник ответа: {origin} | view: {draft.get('view')}")
    print(f"формула JSON:\n{json.dumps(formula, ensure_ascii=False, indent=2)}")
    errors = af._formula_validation_errors(formula)
    print(f"ошибки валидации: {errors if errors else 'нет'}")
    try:
        result = engine.evaluate(formula)
    except Exception:
        print("\n>>> EVALUATE УПАЛ (traceback):")
        traceback.print_exc()
        return
    print(f"evaluate: kind={result['kind']}, value={result.get('value')}, строк={len(result.get('rows') or [])}")
    for row in (result.get("rows") or [])[:6]:
        print(f"  {row.get('label')}: {row.get('value')}")
    print()


def main():
    cfg = af.ai_provider_config()
    print("provider:", cfg.get("provider") or "НЕТ", "| model:", cfg.get("model") or "-",
          "| ключ:", "есть" if cfg.get("api_key") else "НЕТ")
    if not cfg.get("api_key"):
        print("Нет API-ключа — проб невозможен.")
        return
    repo = build_repo()
    source_id = repo.create_sync_source(
        "probe",
        name="New Продажи",
        entity_types=["leads"],
        pipeline_ids=[777],
        status_ids=[],
    )
    sources = repo.list_sync_sources("probe")
    default_source = next((s for s in sources if int(s.get("id") or 0) == source_id), None)
    print(f"default_source: id={source_id}, name={default_source.get('name') if default_source else '?'}")
    dictionary = FormulaDictionaryService(repo).build()
    engine = FormulaEngine(repo)

    run_case("КЕЙС 1 — «процент: ... делить на ...»", PROMPT_1, dictionary, sources, default_source, engine)
    run_case("КЕЙС 2 — явный divide: left/right", PROMPT_2, dictionary, sources, default_source, engine)
    run_case("КЕЙС 3 — шаблон конструктора колонок (group_by Менеджер)", PROMPT_3, dictionary, sources, default_source, engine)


if __name__ == "__main__":
    main()
