from __future__ import annotations

import json

from amocrm_service.db import connect, init_db
from amocrm_service.repository import Repository
from amocrm_service.sync import SyncService, incremental_watermark


class _FakeLeadsClient:
    """Minimal amoCRM client stand-in: mirrors the real iter_leads param building
    and captures the resulting params so tests can assert the watermark filter."""

    def __init__(self, batches):
        self._batches = batches
        self.captured_params = None

    def iter_leads(self, *, pipeline_ids=None, status_ids=None, updated_from=None):
        params = {"with": "contacts,companies,catalog_elements"}
        if pipeline_ids:
            params["filter[pipeline_id][]"] = [int(i) for i in pipeline_ids]
        if status_ids:
            params["filter[status_id][]"] = [int(i) for i in status_ids]
        if updated_from is not None:
            params["filter[updated_at][from]"] = int(updated_from)
        self.captured_params = params
        return iter(self._batches)

    def __getattr__(self, name):
        # Any other get_*/iter_* referenced only while building the getter dicts.
        return lambda *a, **k: []


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


def _run_leads_job(repo, job_type, batches):
    client = _FakeLeadsClient(batches)
    service = SyncService(client, repo)
    job_id = repo.start_sync_job("acc", job_type, ["leads"])
    service.run_existing_sync_job(job_id, "acc", job_type, ["leads"])
    return client


def test_auto_leads_uses_watermark_filter(tmp_path):
    repo = _repo(tmp_path)
    _insert_raw(repo, "leads", "1", {"id": 1, "updated_at": 1_000_000})

    # auto job + existing data -> params carry the watermark (MAX - 3600).
    client = _run_leads_job(repo, "auto_hot", [[{"id": 9001, "updated_at": 1_234_567}]])

    assert client.captured_params["filter[updated_at][from]"] == 1_000_000 - 3600


def test_manual_leads_full_no_filter(tmp_path):
    repo = _repo(tmp_path)
    # Data present, so a watermark WOULD exist — but manual jobs must stay full.
    _insert_raw(repo, "leads", "1", {"id": 1, "updated_at": 1_000_000})

    client = _run_leads_job(repo, "bootstrap", [[{"id": 9001, "updated_at": 1_234_567}]])

    assert "filter[updated_at][from]" not in client.captured_params


def test_auto_leads_empty_full(tmp_path):
    repo = _repo(tmp_path)
    # auto job but empty base -> watermark is None -> full pull, no filter.
    client = _run_leads_job(repo, "auto_hot", [[{"id": 9001, "updated_at": 1_234_567}]])

    assert "filter[updated_at][from]" not in client.captured_params
