from __future__ import annotations

import json

from amocrm_service.db import connect, init_db
from amocrm_service.formula_engine import FormulaEngine
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


def _three_leads_engine(tmp_path) -> FormulaEngine:
    repo = _repo(tmp_path)
    _insert_raw(repo, "leads", "1", {"id": 1, "status_id": 10, "price": 1000})
    _insert_raw(repo, "leads", "2", {"id": 2, "status_id": 20, "price": 2500})
    _insert_raw(repo, "leads", "3", {"id": 3, "status_id": 10, "price": 1500})
    return FormulaEngine(repo)


def test_diagnose_returns_items_dict(tmp_path):
    engine = FormulaEngine(_repo(tmp_path))

    assert engine.diagnose([]) == {"items": []}
    assert engine.diagnose("count") == {"items": []}
    assert engine.diagnose(None) == {"items": []}


def test_diagnose_aggregate_builds_waterfall(tmp_path):
    engine = _three_leads_engine(tmp_path)

    result = engine.diagnose({
        "op": "count",
        "from": "leads",
        "where": [{"field": "status_id", "op": "eq", "value": 10, "value_type": "number"}],
    })

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["op"] == "count"
    assert item["entity"] == "leads"
    stages = item["stages"]
    assert len(stages) == 2
    assert all("label" in stage and "value" in stage for stage in stages)
    assert stages[0]["value"] == 3
    assert stages[-1]["value"] == 2


def test_diagnose_reads_filters_key_same_as_where(tmp_path):
    engine = _three_leads_engine(tmp_path)
    condition = {"field": "status_id", "op": "eq", "value": 10, "value_type": "number"}

    by_where = engine.diagnose({"op": "count", "from": "leads", "where": [condition]})
    by_filters = engine.diagnose({"op": "count", "from": "leads", "filters": [condition]})

    assert by_where == by_filters
    assert [stage["value"] for stage in by_filters["items"][0]["stages"]] == [3, 2]


def test_diagnose_table_returns_item_per_column(tmp_path):
    engine = _three_leads_engine(tmp_path)

    result = engine.diagnose({
        "op": "table",
        "columns": {
            "A": {"op": "count", "from": "leads"},
            "B": {"op": "count", "from": "leads"},
        },
    })

    items = result["items"]
    assert len(items) == 2
    assert {item["title"] for item in items} == {"A", "B"}


def test_diagnose_swallows_errors_as_text(tmp_path):
    engine = _three_leads_engine(tmp_path)

    result = engine.diagnose({
        "op": "count",
        "from": "leads",
        "where": [{"field": "status_id", "op": "bogus_op", "value": 10, "value_type": "number"}],
    })

    stages = result["items"][0]["stages"]
    assert stages[0]["value"] == 3
    assert isinstance(stages[-1]["value"], str)
    assert stages[-1]["value"].startswith("Ошибка")
