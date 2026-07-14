from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Callable

from amocrm_service.amocrm.errors import AmoCRMEntityNotFound
from amocrm_service.repository import Repository

if TYPE_CHECKING:
    from amocrm_service.amocrm import AmoCRMClient


SyncGetter = Callable[[], list[dict]]
ProgressCallback = Callable[[str, int, int], None]

BOOTSTRAP_ENTITIES = [
    "pipelines",
    "users",
    "lead_custom_fields",
    "contact_custom_fields",
    "company_custom_fields",
    "catalogs",
    "leads",
    "contacts",
    "companies",
    "tasks",
    "events",
    "lead_notes",
    "contact_notes",
    "company_notes",
    "catalog_elements",
    "salesbots",
]

ENTITY_TYPE_ALIASES = {
    "lead": "leads",
    "contact": "contacts",
    "company": "companies",
    "customer": "customers",
    "task": "tasks",
}

POINT_REFRESH_ENTITIES = {"leads", "contacts", "companies", "customers", "tasks"}


def normalize_entity_type(entity_type: str) -> str:
    return ENTITY_TYPE_ALIASES.get(entity_type, entity_type)


# Which payload time field marks "last change" per entity. Most entities carry
# ``updated_at`` (mirrored into the indexed ``raw_entities.updated_at`` column);
# ``events`` only carry ``created_at`` and leave that column empty.
WATERMARK_TIME_FIELD: dict[str, str] = {
    "leads": "updated_at",
    "contacts": "updated_at",
    "tasks": "updated_at",
    "events": "created_at",
}


def incremental_watermark(
    repo: Repository,
    account_key: str,
    entity_type: str,
    overlap_seconds: int = 3600,
) -> int | None:
    """Return the incremental-sync cutoff timestamp for ``entity_type``.

    Takes MAX of the entity's time field across stored ``raw_entities`` rows and
    subtracts ``overlap_seconds`` (default 1 hour), so records mutated around the
    previous run's boundary are re-fetched — the upsert is idempotent, so the
    overlap costs nothing but closes the boundary-loss gap.

    Returns ``None`` when nothing is stored yet (MAX is NULL/0), signalling that
    the increment is not applicable and a full pull should run instead.

    ``account_key`` is accepted for call-site symmetry; the hub database is
    already per-account, so no additional filtering is needed.
    """
    time_field = WATERMARK_TIME_FIELD.get(entity_type, "updated_at")
    if time_field == "updated_at":
        row = repo.conn.execute(
            "SELECT MAX(updated_at) FROM raw_entities WHERE entity_type = ?",
            (entity_type,),
        ).fetchone()
    else:
        # Time lives only inside the JSON payload for this entity.
        row = repo.conn.execute(
            "SELECT MAX(CAST(json_extract(payload_json, ?) AS INTEGER)) "
            "FROM raw_entities WHERE entity_type = ?",
            (f"$.{time_field}", entity_type),
        ).fetchone()
    max_ts = row[0] if row else None
    if not max_ts:
        return None
    return int(max_ts) - int(overlap_seconds)


class SyncService:
    def __init__(self, client: "AmoCRMClient", repository: Repository):
        self.client = client
        self.repository = repository

    def _getters(self) -> dict[str, SyncGetter]:
        return {
            "leads": self.client.get_leads,
            "contacts": self.client.get_contacts,
            "companies": self.client.get_companies,
            "tasks": self.client.get_tasks,
            "customers": self.client.get_customers,
            "events": self.client.get_events,
            "lead_notes": self.client.get_lead_notes,
            "contact_notes": self.client.get_contact_notes,
            "company_notes": self.client.get_company_notes,
            "customer_notes": self.client.get_customer_notes,
            "users": self.client.get_users,
            "pipelines": self.client.get_pipelines,
            "lead_custom_fields": self.client.get_lead_custom_fields,
            "contact_custom_fields": self.client.get_contact_custom_fields,
            "company_custom_fields": self.client.get_company_custom_fields,
            "customer_custom_fields": self.client.get_customer_custom_fields,
            "salesbots": self.client.get_salesbots,
            "catalogs": self.client.get_catalogs,
            "catalog_elements": self.client.get_catalog_elements,
        }

    def _iter_getters(self):
        return {
            "leads": self.client.iter_leads,
            "contacts": self.client.iter_contacts,
            "companies": self.client.iter_companies,
            "tasks": self.client.iter_tasks,
            "customers": self.client.iter_customers,
            "events": self.client.iter_events,
            "lead_notes": self.client.iter_lead_notes,
            "contact_notes": self.client.iter_contact_notes,
            "company_notes": self.client.iter_company_notes,
            "customer_notes": self.client.iter_customer_notes,
            "catalog_elements": self.client.iter_catalog_elements,
        }

    def sync_entity(
        self,
        entity_type: str,
        progress_callback: ProgressCallback | None = None,
        filters: dict | None = None,
        source_id: int | None = None,
    ) -> dict:
        getters = self._getters()
        if entity_type not in getters:
            raise ValueError(f"Unknown entity_type: {entity_type}")
        run_id = self.repository.start_sync_run(entity_type)
        try:
            count = 0
            pages_count = 0
            if progress_callback:
                progress_callback(entity_type, count, pages_count)
            iter_getters = self._iter_getters()
            if entity_type == "leads" and filters:
                batches = self.client.iter_leads(
                    pipeline_ids=filters.get("pipeline_ids") or None,
                    status_ids=filters.get("status_ids") or None,
                )
            elif entity_type in iter_getters:
                batches = iter_getters[entity_type]()
            else:
                batches = None
            if batches is not None:
                for batch in batches:
                    count += self.repository.upsert_entities(entity_type, batch)
                    if source_id:
                        self.repository.record_sync_source_entities(source_id, entity_type, batch)
                    pages_count += 1
                    if progress_callback:
                        progress_callback(entity_type, count, pages_count)
            else:
                if entity_type == "leads" and filters:
                    items = self.client.get_leads(
                        pipeline_ids=filters.get("pipeline_ids") or None,
                        status_ids=filters.get("status_ids") or None,
                    )
                else:
                    items = getters[entity_type]()
                count = self.repository.upsert_entities(entity_type, items)
                if source_id:
                    self.repository.record_sync_source_entities(source_id, entity_type, items)
                pages_count = 1
                if progress_callback:
                    progress_callback(entity_type, count, pages_count)
            self.repository.finish_sync_run(run_id, "success", count)
            return {
                "entity_type": entity_type,
                "items_count": count,
                "pages_count": pages_count,
                "run_id": run_id,
            }
        except Exception as exc:
            self.repository.finish_sync_run(run_id, "failed", 0, str(exc))
            raise

    def sync_all(self) -> list[dict]:
        return self.sync_entities(list(self._getters()))

    def bootstrap_entities(self) -> list[str]:
        return [entity for entity in BOOTSTRAP_ENTITIES if entity in self._getters()]

    def sync_entities(self, entity_types: list[str]) -> list[dict]:
        results = []
        for entity_type in entity_types:
            try:
                results.append(self.sync_entity(entity_type))
            except Exception as exc:
                results.append({
                    "entity_type": entity_type,
                    "items_count": 0,
                    "status": "failed",
                    "error": str(exc),
                })
        return results

    def process_queue(self, account_key: str, limit: int = 25) -> dict:
        items = self.repository.claim_sync_queue(account_key, limit)
        processed = 0
        failed = 0
        for item in items:
            queue_id = int(item["id"])
            entity_type = normalize_entity_type(str(item["entity_type"]))
            try:
                if item["action"] == "delete":
                    self.repository.delete_entity(entity_type, str(item["entity_id"]))
                    self.repository.refresh_sync_source_memberships(
                        account_key,
                        entity_type,
                        {"id": item["entity_id"]},
                        deleted=True,
                    )
                    self.repository.finish_sync_queue_item(queue_id, "done")
                    processed += 1
                    continue
                if entity_type not in POINT_REFRESH_ENTITIES:
                    self.repository.finish_sync_queue_item(queue_id, "done")
                    processed += 1
                    continue
                payload = self.client.get_entity_by_id(entity_type, item["entity_id"])
                self.repository.upsert_entities(entity_type, [payload])
                self.repository.refresh_sync_source_memberships(account_key, entity_type, payload)
                self.repository.finish_sync_queue_item(queue_id, "done")
                processed += 1
            except AmoCRMEntityNotFound:
                self.repository.delete_entity(entity_type, str(item["entity_id"]))
                self.repository.refresh_sync_source_memberships(
                    account_key,
                    entity_type,
                    {"id": item["entity_id"]},
                    deleted=True,
                )
                self.repository.finish_sync_queue_item(queue_id, "done")
                processed += 1
            except Exception as exc:
                self.repository.finish_sync_queue_item(queue_id, "failed", str(exc))
                failed += 1
        return {
            "claimed": len(items),
            "processed": processed,
            "failed": failed,
        }

    def run_sync_job(
        self,
        account_key: str,
        job_type: str = "bootstrap",
        entity_types: list[str] | None = None,
    ) -> dict:
        entities = entity_types or self.bootstrap_entities()
        job_id = self.repository.start_sync_job(account_key, job_type, entities)
        return self.run_existing_sync_job(job_id, account_key, job_type, entities)

    def run_existing_sync_job(
        self,
        job_id: int,
        account_key: str,
        job_type: str,
        entity_types: list[str],
        filters: dict | None = None,
        source_id: int | None = None,
    ) -> dict:
        entities = entity_types or self.bootstrap_entities()
        self.repository.mark_sync_job_running(job_id)
        result: list[dict] = []
        items_count = 0
        failed_count = 0
        try:
            for entity_type in entities:
                if source_id:
                    self.repository.clear_sync_source_entities(source_id, entity_type)

                def publish_current_progress(
                    current_entity_type: str,
                    current_items_count: int,
                    current_pages_count: int,
                ) -> None:
                    self.repository.update_sync_job_progress(
                        job_id,
                        items_count=items_count + current_items_count,
                        failed_count=failed_count,
                        result=[
                            *result,
                            {
                                "entity_type": current_entity_type,
                                "items_count": current_items_count,
                                "pages_count": current_pages_count,
                                "status": "running",
                            },
                        ],
                    )

                try:
                    item = self.sync_entity(
                        entity_type,
                        progress_callback=publish_current_progress,
                        filters=filters,
                        source_id=source_id,
                    )
                    result.append(item)
                    items_count += int(item.get("items_count") or 0)
                    if item.get("items_count", 0) and not source_id:
                        self.repository.rebuild_hub_indexes([entity_type])
                except Exception as exc:
                    failed_count += 1
                    result.append({
                        "entity_type": entity_type,
                        "items_count": 0,
                        "status": "failed",
                        "error": str(exc),
                    })
                self.repository.update_sync_job_progress(
                    job_id,
                    items_count=items_count,
                    failed_count=failed_count,
                    result=result,
                )
            self.repository.finish_sync_job(
                job_id,
                "success" if failed_count == 0 else "partial",
                items_count=items_count,
                failed_count=failed_count,
                result=result,
            )
            return {
                "job_id": job_id,
                "status": "success" if failed_count == 0 else "partial",
                "items_count": items_count,
                "failed_count": failed_count,
                "result": result,
            }
        except Exception as exc:
            self.repository.finish_sync_job(job_id, "failed", error=str(exc))
            raise
