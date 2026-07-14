from __future__ import annotations

import json

from amocrm_service.db import connect, init_db
from amocrm_service.repository import Repository
from amocrm_service.sync import incremental_watermark


def _repo(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    init_db(db_path)
    return Repository(connect(db_path))


def _insert_raw(repo: Repository, entity_type: str, entity_id: str, payload: dict) -> None:
    repo.conn.execute(
        """
        INSERT INTO raw_entities(entity_type, entity_id, name, payload_json, updated_at, synced_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            entity_id,
            payload.get("name", ""),
            json.dumps(payload, ensure_ascii=False),
            payload.get("updated_at", 0),
            "2026-07-04T12:00:00+00:00",
        ),
    )
    repo.conn.commit()


def test_watermark_none_when_empty(tmp_path):
    repo = _repo(tmp_path)
    assert incremental_watermark(repo, "acc", "leads") is None


def test_watermark_subtracts_overlap(tmp_path):
    repo = _repo(tmp_path)
    _insert_raw(repo, "leads", "1", {"id": 1, "updated_at": 1_000_000})
    _insert_raw(repo, "leads", "2", {"id": 2, "updated_at": 1_500_000})
    _insert_raw(repo, "leads", "3", {"id": 3, "updated_at": 1_200_000})

    # MAX(updated_at) == 1_500_000; minus default 3600s overlap.
    assert incremental_watermark(repo, "acc", "leads", overlap_seconds=3600) == 1_500_000 - 3600


def test_watermark_per_entity(tmp_path):
    repo = _repo(tmp_path)
    # events carry no updated_at -> column stays 0; time is created_at in payload.
    _insert_raw(repo, "events", "10", {"id": 10, "type": "lead_status_changed", "created_at": 2_000_000})
    _insert_raw(repo, "events", "11", {"id": 11, "type": "lead_status_changed", "created_at": 2_222_000})
    # A lead in the same DB must not leak into the events watermark.
    _insert_raw(repo, "leads", "1", {"id": 1, "updated_at": 9_999_999})

    # For events the MAX is taken over created_at, not the (zero) updated_at column.
    assert incremental_watermark(repo, "acc", "events", overlap_seconds=1000) == 2_222_000 - 1000


def test_watermark_default_overlap_one_hour(tmp_path):
    repo = _repo(tmp_path)
    _insert_raw(repo, "leads", "1", {"id": 1, "updated_at": 5_000_000})

    # Default overlap must be exactly 3600 seconds (1 hour).
    assert incremental_watermark(repo, "acc", "leads") == 5_000_000 - 3600
