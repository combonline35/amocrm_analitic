from __future__ import annotations

from amocrm_service.amocrm.errors import AmoCRMEntityNotFound
from amocrm_service.db import connect, init_db
from amocrm_service.repository import Repository
from amocrm_service.sync import SyncService


class MissingEntityClient:
    def get_entity_by_id(self, entity_type: str, entity_id: str):
        raise AmoCRMEntityNotFound(f"{entity_type}/{entity_id} is gone")


class ExplodingClient:
    def get_entity_by_id(self, entity_type: str, entity_id: str):
        raise AssertionError("unsupported entity should not be refreshed")


def test_process_queue_closes_missing_entity_as_done_and_deletes_local_copy(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    repo = Repository(conn)
    try:
        repo.upsert_entities("contacts", [{"id": 30516577, "name": "Old contact"}])
        queue_id = repo.enqueue_sync("demo", "contacts", "30516577", reason="update_contact")

        result = SyncService(MissingEntityClient(), repo).process_queue("demo", limit=10)
        queue_item = repo.list_sync_queue_items("demo", limit=1)[0]

        assert result == {"claimed": 1, "processed": 1, "failed": 0}
        assert queue_item["id"] == queue_id
        assert queue_item["status"] == "done"
        assert queue_item["last_error"] is None
        assert repo.list_entities("contacts") == []
    finally:
        conn.close()


def test_process_queue_closes_unsupported_refresh_entities_as_done(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    repo = Repository(conn)
    try:
        queue_id = repo.enqueue_sync("demo", "message", "abc-123", reason="add_message")

        result = SyncService(ExplodingClient(), repo).process_queue("demo", limit=10)
        queue_item = repo.list_sync_queue_items("demo", limit=1)[0]

        assert result == {"claimed": 1, "processed": 1, "failed": 0}
        assert queue_item["id"] == queue_id
        assert queue_item["status"] == "done"
        assert queue_item["last_error"] is None
    finally:
        conn.close()
