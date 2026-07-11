from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from amocrm_service.db import connect, init_db
from amocrm_service.formula_engine import FormulaEngine
from amocrm_service.repository import Repository

BUSINESS_TZ = ZoneInfo("Europe/Moscow")


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


def _engine_with_current_and_previous_month_leads(tmp_path) -> FormulaEngine:
    # Опорные даты считаем так же, как движок: от now() в Europe/Moscow.
    # 2-е число 12:00 всегда внутри окна this_month, 15-е прошлого месяца —
    # всегда внутри previous_month, в какой бы день тест ни запускался.
    now = datetime.now(BUSINESS_TZ)
    current_ts = int(now.replace(day=2, hour=12, minute=0, second=0, microsecond=0).timestamp())
    first_of_month = now.replace(day=1, hour=12, minute=0, second=0, microsecond=0)
    previous_ts = int((first_of_month - timedelta(days=1)).replace(day=15).timestamp())

    repo = _repo(tmp_path)
    _insert_raw(repo, "leads", "1", {"id": 1, "created_at": current_ts})
    _insert_raw(repo, "leads", "2", {"id": 2, "created_at": previous_ts})
    return FormulaEngine(repo)


def _count_created_at(engine: FormulaEngine, op: str) -> int:
    result = engine.evaluate({"op": "count", "from": "leads", "where": [{"field": "created_at", "op": op}]})
    return result["value"]


def test_this_month_selects_current_not_previous(tmp_path):
    engine = _engine_with_current_and_previous_month_leads(tmp_path)

    assert _count_created_at(engine, "this_month") == 1


def test_previous_month_selects_previous_not_current(tmp_path):
    engine = _engine_with_current_and_previous_month_leads(tmp_path)

    assert _count_created_at(engine, "previous_month") == 1


def test_week_preset_on_month_field_raises(tmp_path):
    engine = _engine_with_current_and_previous_month_leads(tmp_path)

    with pytest.raises(ValueError, match="only with date fields"):
        engine.evaluate({"op": "count", "from": "leads", "where": [{"field": "created_month", "op": "this_week"}]})


def test_unknown_preset_raises(tmp_path):
    engine = _engine_with_current_and_previous_month_leads(tmp_path)

    with pytest.raises(ValueError, match="this_decade"):
        engine.evaluate({"op": "count", "from": "leads", "where": [{"field": "created_at", "op": "this_decade"}]})
