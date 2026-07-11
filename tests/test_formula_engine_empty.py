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


def _engine_with_three_name_states(tmp_path) -> FormulaEngine:
    repo = _repo(tmp_path)
    _insert_raw(repo, "leads", "1", {"id": 1, "name": "Иван"}, "Иван")
    _insert_raw(repo, "leads", "2", {"id": 2, "name": ""})
    _insert_raw(repo, "leads", "3", {"id": 3})
    return FormulaEngine(repo)


def _count_by_op(engine: FormulaEngine, op: str, value=None) -> int:
    condition = {"field": "name", "op": op}
    if value is not None:
        condition["value"] = value
    result = engine.evaluate({"op": "count", "from": "leads", "where": [condition]})
    return result["value"]


def test_empty_matches_null_and_blank(tmp_path):
    engine = _engine_with_three_name_states(tmp_path)

    assert _count_by_op(engine, "empty") == 2
    assert _count_by_op(engine, "is_empty") == 2


def test_not_empty_is_complement(tmp_path):
    engine = _engine_with_three_name_states(tmp_path)

    assert _count_by_op(engine, "not_empty") == 1
    assert _count_by_op(engine, "empty") + _count_by_op(engine, "not_empty") == 3


def test_empty_synonyms_ops_agree(tmp_path):
    engine = _engine_with_three_name_states(tmp_path)

    assert _count_by_op(engine, "empty") == _count_by_op(engine, "is_empty")
    assert (
        _count_by_op(engine, "not_empty")
        == _count_by_op(engine, "is_not_empty")
        == _count_by_op(engine, "filled")
    )


def test_empty_value_string_synonyms(tmp_path):
    engine = _engine_with_three_name_states(tmp_path)

    for synonym in ("не заполнено", "нет", "null", "пусто", "не указано", "none", ""):
        assert _count_by_op(engine, "eq", synonym) == 2, synonym
    assert _count_by_op(engine, "neq", "не заполнено") == 1
