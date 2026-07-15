from __future__ import annotations

import json
from datetime import datetime, timezone

from amocrm_service.db import connect, init_db
from amocrm_service.formula_engine import FormulaDictionaryService, FormulaEngine
from amocrm_service.repository import Repository


def _repo(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    init_db(db_path)
    return Repository(connect(db_path))


def _insert_raw(repo: Repository, entity_type: str, entity_id: str, payload: dict, name: str = "") -> None:
    repo.conn.execute(
        """
        INSERT INTO raw_entities(entity_type, entity_id, name, payload_json, updated_at, synced_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (entity_type, entity_id, name, json.dumps(payload, ensure_ascii=False), payload.get("updated_at", 0), "2026-07-04T12:00:00+00:00"),
    )
    repo.conn.commit()


def test_formula_dictionary_includes_custom_fields(tmp_path):
    repo = _repo(tmp_path)
    _insert_raw(
        repo,
        "lead_custom_fields",
        "1001",
        {"id": 1001, "name": "Рекламная площадка", "type": "text"},
        "Рекламная площадка",
    )
    _insert_raw(repo, "leads", "1", {"id": 1, "price": 1000, "custom_fields_values": [{"field_id": 1001, "field_name": "Рекламная площадка", "values": [{"value": "Яндекс"}]}]})
    repo.rebuild_hub_indexes(["leads"])

    fields = FormulaDictionaryService(repo).fields_for("leads")

    assert any(field["value"] == "cf_1001" and field["label"] == "Рекламная площадка" for field in fields)


def test_formula_engine_counts_and_sums_filtered_leads(tmp_path):
    repo = _repo(tmp_path)
    _insert_raw(repo, "leads", "1", {"id": 1, "status_id": 10, "price": 1000})
    _insert_raw(repo, "leads", "2", {"id": 2, "status_id": 20, "price": 2500})
    _insert_raw(repo, "leads", "3", {"id": 3, "status_id": 10, "price": 1500})
    engine = FormulaEngine(repo)

    count = engine.evaluate({"op": "count", "from": "leads", "where": [{"field": "status_id", "op": "eq", "value": 10, "value_type": "number"}]})
    total = engine.evaluate({"op": "sum", "from": "leads", "field": "price", "where": [{"field": "status_id", "op": "eq", "value": 10, "value_type": "number"}]})

    assert count["kind"] == "scalar"
    assert count["value"] == 2
    assert total["value"] == 2500


def test_formula_engine_treats_status_gte_as_pipeline_order(tmp_path):
    repo = _repo(tmp_path)
    _insert_raw(
        repo,
        "pipelines",
        "777",
        {
            "id": 777,
            "name": "Pipeline",
            "_embedded": {
                "statuses": [
                    {"id": 100, "name": "First"},
                    {"id": 50, "name": "Start"},
                    {"id": 300, "name": "After"},
                ]
            },
        },
        "Pipeline",
    )
    _insert_raw(repo, "leads", "1", {"id": 1, "pipeline_id": 777, "status_id": 100})
    _insert_raw(repo, "leads", "2", {"id": 2, "pipeline_id": 777, "status_id": 50})
    _insert_raw(repo, "leads", "3", {"id": 3, "pipeline_id": 777, "status_id": 300})
    source_id = repo.create_sync_source(
        "test",
        name="Pipeline source",
        entity_types=["leads"],
        pipeline_ids=[777],
        status_ids=[],
    )
    repo.record_sync_source_entities(source_id, "leads", [{"id": 1}, {"id": 2}, {"id": 3}])
    engine = FormulaEngine(repo)

    result = engine.evaluate({
        "op": "count",
        "from": "leads",
        "source_id": source_id,
        "where": [{"field": "status_id", "op": "gte", "value": 50, "value_type": "number"}],
    })

    assert result["value"] == 2


def test_source_counts_live_not_snapshot(tmp_path):
    repo = _repo(tmp_path)
    # Three live leads sitting in pipeline 555.
    _insert_raw(repo, "leads", "1", {"id": 1, "pipeline_id": 555, "status_id": 10})
    _insert_raw(repo, "leads", "2", {"id": 2, "pipeline_id": 555, "status_id": 10})
    _insert_raw(repo, "leads", "3", {"id": 3, "pipeline_id": 555, "status_id": 10})
    source_id = repo.create_sync_source(
        "test",
        name="Live source",
        entity_types=["leads"],
        pipeline_ids=[555],
        status_ids=[],
    )
    # Frozen snapshot only knows 2 of them — the 3rd entered the funnel after
    # the last source resync, so the old INNER JOIN would miss it.
    repo.record_sync_source_entities(source_id, "leads", [{"id": 1}, {"id": 2}])
    engine = FormulaEngine(repo)

    result = engine.evaluate({
        "op": "count",
        "from": "leads",
        "source_id": source_id,
    })

    # Live count must be 3 (all leads in pipeline 555), not 2 (stale snapshot).
    assert result["value"] == 3


def test_formula_engine_filters_month_fields_with_month_presets(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    current_month_ts = int(now.replace(day=2, hour=12, minute=0, second=0, microsecond=0).timestamp())
    previous_year_ts = int(now.replace(year=now.year - 1, day=2, hour=12, minute=0, second=0, microsecond=0).timestamp())
    _insert_raw(repo, "leads", "1", {"id": 1, "created_at": current_month_ts})
    _insert_raw(repo, "leads", "2", {"id": 2, "created_at": previous_year_ts})
    engine = FormulaEngine(repo)

    by_date = engine.evaluate({"op": "count", "from": "leads", "where": [{"field": "created_at", "op": "this_month"}]})
    by_month = engine.evaluate({"op": "count", "from": "leads", "where": [{"field": "created_month", "op": "this_month"}]})

    assert by_date["value"] == 1
    assert by_month["value"] == 1


def test_formula_engine_filters_field_types_consistently(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    current_month_ts = int(now.replace(day=5, hour=12, minute=0, second=0, microsecond=0).timestamp())
    old_ts = int(now.replace(year=now.year - 1, day=5, hour=12, minute=0, second=0, microsecond=0).timestamp())
    _insert_raw(repo, "leads", "1", {"id": 1, "name": "Заявка Конкин", "price": 1500.5, "created_at": current_month_ts}, "Заявка Конкин")
    _insert_raw(repo, "leads", "2", {"id": 2, "name": "Другая заявка", "price": 500, "created_at": old_ts}, "Другая заявка")
    engine = FormulaEngine(repo)

    text_like = engine.evaluate({"op": "count", "from": "leads", "where": [{"field": "name", "op": "like", "value": "Конкин"}]})
    number_gte = engine.evaluate({"op": "count", "from": "leads", "where": [{"field": "price", "op": "gte", "value": "1 500,5"}]})
    date_eq = engine.evaluate({"op": "count", "from": "leads", "where": [{"field": "created_at", "op": "eq", "value": now.strftime("%Y-%m-05")}]})
    month_between = engine.evaluate({"op": "count", "from": "leads", "where": [{"field": "created_month", "op": "date_between", "value": [now.strftime("%Y-%m"), now.strftime("%Y-%m")]}]})

    assert text_like["value"] == 1
    assert number_gte["value"] == 1
    assert date_eq["value"] == 1
    assert month_between["value"] == 1


def test_formula_engine_filters_boolean_and_custom_month_fields(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    current_month_ts = int(now.replace(day=7, hour=12, minute=0, second=0, microsecond=0).timestamp())
    old_ts = int(now.replace(year=now.year - 1, day=7, hour=12, minute=0, second=0, microsecond=0).timestamp())
    _insert_raw(repo, "lead_custom_fields", "2002", {"id": 2002, "name": "Дата договора", "type": "date"}, "Дата договора")
    _insert_raw(
        repo,
        "leads",
        "1",
        {"id": 1, "custom_fields_values": [{"field_id": 2002, "field_name": "Дата договора", "values": [{"value": current_month_ts}]}]},
    )
    _insert_raw(
        repo,
        "leads",
        "2",
        {"id": 2, "custom_fields_values": [{"field_id": 2002, "field_name": "Дата договора", "values": [{"value": old_ts}]}]},
    )
    _insert_raw(repo, "tasks", "1", {"id": 1, "is_completed": True})
    _insert_raw(repo, "tasks", "2", {"id": 2, "is_completed": False})
    repo.rebuild_hub_indexes(["leads"])
    engine = FormulaEngine(repo)

    custom_month = engine.evaluate({"op": "count", "from": "leads", "where": [{"field": "cf_month_2002", "op": "this_month"}]})
    task_done = engine.evaluate({"op": "count", "from": "tasks", "where": [{"field": "is_completed", "op": "eq", "value": "да"}]})

    assert custom_month["value"] == 1
    assert task_done["value"] == 1


def test_formula_engine_divides_series_by_key(tmp_path):
    repo = _repo(tmp_path)
    _insert_raw(repo, "leads", "1", {"id": 1, "responsible_user_id": 11, "status_id": 10})
    _insert_raw(repo, "leads", "2", {"id": 2, "responsible_user_id": 11, "status_id": 20})
    _insert_raw(repo, "leads", "3", {"id": 3, "responsible_user_id": 12, "status_id": 10})
    engine = FormulaEngine(repo)

    result = engine.evaluate({
        "op": "divide",
        "left": {
            "op": "count",
            "from": "leads",
            "group_by": "responsible_user_id",
            "where": [{"field": "status_id", "op": "eq", "value": 20, "value_type": "number"}],
        },
        "right": {
            "op": "count",
            "from": "leads",
            "group_by": "responsible_user_id",
        },
    })

    rows = {row["key"]: row["value"] for row in result["rows"]}
    assert result["kind"] == "series"
    assert rows["11"] == 0.5
    assert rows["12"] == 0


def test_formula_engine_builds_table_from_formula_columns(tmp_path):
    repo = _repo(tmp_path)
    _insert_raw(repo, "leads", "1", {"id": 1, "responsible_user_id": 11, "status_id": 10})
    _insert_raw(repo, "leads", "2", {"id": 2, "responsible_user_id": 11, "status_id": 20})
    _insert_raw(repo, "leads", "3", {"id": 3, "responsible_user_id": 12, "status_id": 10})

    result = FormulaEngine(repo).evaluate({
        "op": "table",
        "columns": {
            "Назначено": {"op": "count", "from": "leads", "group_by": "responsible_user_id"},
            "Договора": {
                "op": "count",
                "from": "leads",
                "group_by": "responsible_user_id",
                "where": [{"field": "status_id", "op": "eq", "value": 20, "value_type": "number"}],
            },
            "Конверсия": {
                "op": "divide",
                "left": {
                    "op": "count",
                    "from": "leads",
                    "group_by": "responsible_user_id",
                    "where": [{"field": "status_id", "op": "eq", "value": 20, "value_type": "number"}],
                },
                "right": {"op": "count", "from": "leads", "group_by": "responsible_user_id"},
            },
        },
    })

    rows = {row["key"]: row for row in result["rows"]}
    assert result["kind"] == "table"
    assert rows["11"]["Назначено"] == 2
    assert rows["11"]["Договора"] == 1
    assert rows["11"]["Конверсия"] == 0.5


def _insert_source(repo: Repository, source_id: int, pipeline_ids: list[int]) -> None:
    repo.conn.execute(
        """
        INSERT INTO sync_sources(id, account_key, name, entity_types_json, pipeline_ids_json, status_ids_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, "test", f"Источник {source_id}", '["leads"]', json.dumps(pipeline_ids), "[]",
         "2026-07-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00"),
    )
    repo.conn.commit()


def test_table_inherits_root_source_id(tmp_path):
    repo = _repo(tmp_path)
    _insert_source(repo, 5, [100])
    _insert_raw(repo, "leads", "1", {"id": 1, "pipeline_id": 100, "responsible_user_id": 11})
    _insert_raw(repo, "leads", "2", {"id": 2, "pipeline_id": 100, "responsible_user_id": 11})
    _insert_raw(repo, "leads", "3", {"id": 3, "pipeline_id": 200, "responsible_user_id": 11})

    result = FormulaEngine(repo).evaluate({
        "op": "table",
        "source_id": 5,
        "columns": {
            "Все": {"op": "count", "from": "leads", "group_by": "responsible_user_id"},
        },
    })

    rows = {row["key"]: row for row in result["rows"]}
    # Сделка из воронки 200 вне источника 5 — в счёт не попала.
    assert rows["11"]["Все"] == 2


def test_table_column_source_overrides_root(tmp_path):
    repo = _repo(tmp_path)
    _insert_source(repo, 5, [100])
    _insert_source(repo, 6, [200])
    _insert_raw(repo, "leads", "1", {"id": 1, "pipeline_id": 100, "responsible_user_id": 11})
    _insert_raw(repo, "leads", "2", {"id": 2, "pipeline_id": 100, "responsible_user_id": 11})
    _insert_raw(repo, "leads", "3", {"id": 3, "pipeline_id": 200, "responsible_user_id": 11})

    result = FormulaEngine(repo).evaluate({
        "op": "table",
        "source_id": 5,
        "columns": {
            "Корневой": {"op": "count", "from": "leads", "group_by": "responsible_user_id"},
            "Свой": {"op": "count", "from": "leads", "group_by": "responsible_user_id", "source_id": 6},
        },
    })

    rows = {row["key"]: row for row in result["rows"]}
    assert rows["11"]["Корневой"] == 2
    # Колонка со своим source_id=6 не перебита корневым 5.
    assert rows["11"]["Свой"] == 1


def test_table_no_source_counts_all(tmp_path):
    repo = _repo(tmp_path)
    _insert_raw(repo, "leads", "1", {"id": 1, "pipeline_id": 100, "responsible_user_id": 11})
    _insert_raw(repo, "leads", "2", {"id": 2, "pipeline_id": 100, "responsible_user_id": 11})
    _insert_raw(repo, "leads", "3", {"id": 3, "pipeline_id": 200, "responsible_user_id": 11})

    result = FormulaEngine(repo).evaluate({
        "op": "table",
        "columns": {
            "Все": {"op": "count", "from": "leads", "group_by": "responsible_user_id"},
        },
    })

    rows = {row["key"]: row for row in result["rows"]}
    # Без source_id где-либо — честный весь хаб.
    assert rows["11"]["Все"] == 3
