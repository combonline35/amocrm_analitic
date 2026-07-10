from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from amocrm_service.filters import AnalyticsFilter


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _call_checklist_step_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["active"] = bool(item.get("active"))
        return item

    def upsert_account(
        self,
        account_key: str,
        *,
        account_id: int | None = None,
        subdomain: str | None = None,
        base_domain: str | None = None,
        name: str | None = None,
    ) -> None:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO amo_accounts(account_key, account_id, subdomain, base_domain, name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_key) DO UPDATE SET
                account_id = COALESCE(excluded.account_id, amo_accounts.account_id),
                subdomain = COALESCE(excluded.subdomain, amo_accounts.subdomain),
                base_domain = COALESCE(excluded.base_domain, amo_accounts.base_domain),
                name = COALESCE(excluded.name, amo_accounts.name),
                updated_at = excluded.updated_at
            """,
            (account_key, account_id, subdomain, base_domain, name, now, now),
        )
        self.conn.commit()

    def list_call_checklist_steps(
        self,
        account_key: str,
        *,
        active: bool | None = True,
    ) -> list[dict[str, Any]]:
        where = ["account_key = ?"]
        params: list[Any] = [account_key]
        if active is not None:
            where.append("active = ?")
            params.append(1 if active else 0)
        rows = self.conn.execute(
            f"""
            SELECT id, account_key, slug, label, hint, order_index, active, created_at, updated_at
            FROM call_checklist_step
            WHERE {' AND '.join(where)}
            ORDER BY order_index, id
            """,
            params,
        ).fetchall()
        return [self._call_checklist_step_row(row) for row in rows]

    def create_call_checklist_step(
        self,
        account_key: str,
        *,
        slug: str,
        label: str,
        hint: str,
        order_index: int,
        active: bool = True,
    ) -> dict[str, Any]:
        slug = str(slug or "").strip()
        label = str(label or "").strip()
        hint = str(hint or "").strip()
        if not slug:
            raise ValueError("slug is required")
        if not label:
            raise ValueError("label is required")
        now = utc_now()
        cursor = self.conn.execute(
            """
            INSERT INTO call_checklist_step(
                account_key, slug, label, hint, order_index, active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_key,
                slug,
                label,
                hint,
                int(order_index),
                1 if active else 0,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get_call_checklist_step(account_key, int(cursor.lastrowid))

    def get_call_checklist_step(self, account_key: str, step_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT id, account_key, slug, label, hint, order_index, active, created_at, updated_at
            FROM call_checklist_step
            WHERE account_key = ? AND id = ?
            """,
            (account_key, int(step_id)),
        ).fetchone()
        if not row:
            raise LookupError("call checklist step not found")
        return self._call_checklist_step_row(row)

    def update_call_checklist_step(
        self,
        account_key: str,
        step_id: int,
        *,
        label: str | None = None,
        hint: str | None = None,
        order_index: int | None = None,
        active: bool | None = None,
    ) -> dict[str, Any]:
        self.get_call_checklist_step(account_key, step_id)
        assignments: list[str] = []
        params: list[Any] = []
        if label is not None:
            label = str(label).strip()
            if not label:
                raise ValueError("label must not be empty")
            assignments.append("label = ?")
            params.append(label)
        if hint is not None:
            assignments.append("hint = ?")
            params.append(str(hint).strip())
        if order_index is not None:
            assignments.append("order_index = ?")
            params.append(int(order_index))
        if active is not None:
            assignments.append("active = ?")
            params.append(1 if active else 0)
        if assignments:
            assignments.append("updated_at = ?")
            params.append(utc_now())
            self.conn.execute(
                f"""
                UPDATE call_checklist_step
                SET {', '.join(assignments)}
                WHERE account_key = ? AND id = ?
                """,
                [*params, account_key, int(step_id)],
            )
            self.conn.commit()
        return self.get_call_checklist_step(account_key, step_id)

    def deactivate_call_checklist_step(self, account_key: str, step_id: int) -> dict[str, Any]:
        return self.update_call_checklist_step(account_key, step_id, active=False)

    def create_sync_source(
        self,
        account_key: str,
        *,
        name: str,
        entity_types: list[str],
        pipeline_ids: list[int] | None = None,
        status_ids: list[int] | None = None,
    ) -> int:
        now = utc_now()
        cursor = self.conn.execute(
            """
            INSERT INTO sync_sources(
                account_key, name, entity_types_json, pipeline_ids_json, status_ids_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_key,
                name,
                json.dumps(entity_types, ensure_ascii=False, separators=(",", ":")),
                json.dumps(pipeline_ids or [], ensure_ascii=False, separators=(",", ":")),
                json.dumps(status_ids or [], ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_sync_source_job(self, source_id: int, job_id: int) -> None:
        self.conn.execute(
            """
            UPDATE sync_sources
            SET last_job_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (job_id, utc_now(), source_id),
        )
        self.conn.commit()

    def list_sync_sources(self, account_key: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
                source.id,
                source.account_key,
                source.name,
                source.entity_types_json,
                source.pipeline_ids_json,
                source.status_ids_json,
                source.last_job_id,
                source.created_at,
                source.updated_at,
                source.updated_at AS source_checked_at,
                (SELECT MAX(synced_at) FROM raw_entities WHERE entity_type = 'leads') AS hub_leads_synced_at,
                job.status AS last_job_status,
                job.started_at AS last_job_started_at,
                job.finished_at AS last_job_finished_at,
                job.items_count AS last_job_items_count,
                job.failed_count AS last_job_failed_count,
                job.error AS last_job_error,
                MAX(link.synced_at) AS linked_synced_at,
                COUNT(DISTINCT CASE WHEN link.entity_type = 'leads' THEN link.entity_id END) AS linked_leads_count,
                COUNT(DISTINCT link.entity_type || ':' || link.entity_id) AS linked_count
            FROM sync_sources AS source
            LEFT JOIN sync_source_entities AS link ON link.source_id = source.id
            LEFT JOIN sync_jobs AS job ON job.id = source.last_job_id
            WHERE source.account_key = ?
            GROUP BY source.id
            ORDER BY source.updated_at DESC, source.id DESC
            """,
            (account_key,),
        ).fetchall()
        pipeline_index = self._pipeline_index()
        result = []
        for row in rows:
            item = dict(row)
            item["entity_types"] = json.loads(item.pop("entity_types_json") or "[]")
            item["pipeline_ids"] = json.loads(item.pop("pipeline_ids_json") or "[]")
            item["status_ids"] = json.loads(item.pop("status_ids_json") or "[]")
            pipeline_names = []
            source_statuses = []
            pipeline_status_total = 0
            source_status_ids = {int(value) for value in item["status_ids"] if str(value).strip()}
            for pipeline_id in item["pipeline_ids"]:
                pipeline = pipeline_index.get(int(pipeline_id))
                if not pipeline:
                    continue
                pipeline_names.append(pipeline["name"])
                pipeline_status_total += int(pipeline["status_count"])
                for status in pipeline.get("statuses", []):
                    if source_status_ids and int(status["id"]) not in source_status_ids:
                        continue
                    source_statuses.append(status)
            item["pipeline_names"] = pipeline_names
            item["statuses"] = source_statuses
            item["pipeline_status_total"] = pipeline_status_total
            result.append(item)
        return result

    def _pipeline_index(self) -> dict[int, dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT entity_id, name, payload_json
            FROM raw_entities
            WHERE entity_type = 'pipelines'
            """
        ).fetchall()
        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            pipeline_id = int(row["entity_id"])
            payload = json.loads(row["payload_json"] or "{}")
            statuses = (payload.get("_embedded") or {}).get("statuses") or []
            result[pipeline_id] = {
                "name": str(payload.get("name") or row["name"] or pipeline_id),
                "status_count": len(statuses),
                "statuses": [
                    {
                        "id": int(status.get("id") or 0),
                        "name": str(status.get("name") or status.get("id") or ""),
                        "pipeline_id": pipeline_id,
                    }
                    for status in statuses
                    if int(status.get("id") or 0)
                ],
            }
        return result

    def get_sync_source(self, source_id: int, account_key: str) -> dict[str, Any] | None:
        for source in self.list_sync_sources(account_key):
            if int(source["id"]) == int(source_id):
                return source
        return None

    def clear_sync_source_entities(self, source_id: int, entity_type: str | None = None) -> int:
        params: list[Any] = [source_id]
        where = "source_id = ?"
        if entity_type:
            where += " AND entity_type = ?"
            params.append(entity_type)
        cursor = self.conn.execute(
            f"DELETE FROM sync_source_entities WHERE {where}",
            params,
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def record_sync_source_entities(
        self,
        source_id: int,
        entity_type: str,
        items: list[dict[str, Any]],
    ) -> int:
        now = utc_now()
        rows = [
            (source_id, entity_type, str(item.get("id")), now)
            for item in items
            if item.get("id") is not None
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO sync_source_entities(source_id, entity_type, entity_id, synced_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_id, entity_type, entity_id) DO UPDATE SET
                synced_at = excluded.synced_at
            """,
            rows,
        )
        self.conn.execute(
            "UPDATE sync_sources SET updated_at = ? WHERE id = ?",
            (now, source_id),
        )
        self.conn.commit()
        return len(rows)

    def refresh_sync_source_memberships(
        self,
        account_key: str,
        entity_type: str,
        item: dict[str, Any],
        *,
        deleted: bool = False,
    ) -> dict[str, int]:
        entity_id = item.get("id")
        if entity_id is None:
            return {"checked": 0, "linked": 0, "unlinked": 0}

        sources = [
            source
            for source in self.list_sync_sources(account_key)
            if entity_type in {str(value) for value in source.get("entity_types", [])}
        ]
        if not sources:
            return {"checked": 0, "linked": 0, "unlinked": 0}

        now = utc_now()
        linked = 0
        unlinked = 0
        source_ids = [int(source["id"]) for source in sources]

        for source in sources:
            source_id = int(source["id"])
            should_link = False
            if not deleted:
                should_link = self._sync_source_matches_item(source, entity_type, item)
            if should_link:
                self.conn.execute(
                    """
                    INSERT INTO sync_source_entities(source_id, entity_type, entity_id, synced_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source_id, entity_type, entity_id) DO UPDATE SET
                        synced_at = excluded.synced_at
                    """,
                    (source_id, entity_type, str(entity_id), now),
                )
                linked += 1
            else:
                cursor = self.conn.execute(
                    """
                    DELETE FROM sync_source_entities
                    WHERE source_id = ? AND entity_type = ? AND entity_id = ?
                    """,
                    (source_id, entity_type, str(entity_id)),
                )
                unlinked += int(cursor.rowcount or 0)

        placeholders = ",".join("?" for _ in source_ids)
        self.conn.execute(
            f"UPDATE sync_sources SET updated_at = ? WHERE id IN ({placeholders})",
            [now, *source_ids],
        )
        self.conn.commit()
        return {"checked": len(sources), "linked": linked, "unlinked": unlinked}

    def _sync_source_matches_item(self, source: dict[str, Any], entity_type: str, item: dict[str, Any]) -> bool:
        if entity_type != "leads":
            return True
        pipeline_ids = {int(value) for value in source.get("pipeline_ids", []) if str(value).strip()}
        status_ids = {int(value) for value in source.get("status_ids", []) if str(value).strip()}
        pipeline_id = int(item.get("pipeline_id") or 0)
        status_id = int(item.get("status_id") or 0)
        if pipeline_ids and pipeline_id not in pipeline_ids:
            return False
        if status_ids and status_id not in status_ids:
            return False
        return True

    def add_webhook_event(
        self,
        account_key: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        raw_body: str | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO webhook_events(
                account_key, event_type, entity_type, entity_id, payload_json, raw_body, received_at, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                account_key,
                event_type,
                entity_type,
                entity_id,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                raw_body,
                utc_now(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def enqueue_sync(
        self,
        account_key: str,
        entity_type: str,
        entity_id: str,
        *,
        action: str = "refresh",
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        payload_json = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
        cursor = self.conn.execute(
            """
            INSERT INTO sync_queue(
                account_key, entity_type, entity_id, action, reason, payload_json,
                available_at, created_at, updated_at, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(account_key, entity_type, entity_id, action) DO UPDATE SET
                reason = excluded.reason,
                payload_json = excluded.payload_json,
                available_at = excluded.available_at,
                updated_at = excluded.updated_at,
                status = 'pending',
                last_error = NULL
            RETURNING id
            """,
            (account_key, entity_type, entity_id, action, reason, payload_json, now, now, now),
        )
        row = cursor.fetchone()
        self.conn.commit()
        return int(row["id"]) if row else 0

    def queue_summary(self, account_key: str | None = None) -> list[dict[str, Any]]:
        where = ""
        params: list[Any] = []
        if account_key:
            where = "WHERE account_key = ?"
            params.append(account_key)
        rows = self.conn.execute(
            f"""
            SELECT account_key, entity_type, action, status, COUNT(*) AS items_count
            FROM sync_queue
            {where}
            GROUP BY account_key, entity_type, action, status
            ORDER BY account_key, entity_type, action, status
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def queue_status_counts(self, account_key: str | None = None) -> dict[str, int]:
        summary = self.queue_summary(account_key)
        counts: dict[str, int] = {}
        for row in summary:
            status = str(row["status"])
            counts[status] = counts.get(status, 0) + int(row["items_count"] or 0)
        return counts

    def list_sync_queue_items(
        self,
        account_key: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where = ["account_key = ?"]
        params: list[Any] = [account_key]
        if status:
            where.append("status = ?")
            params.append(status)
        rows = self.conn.execute(
            f"""
            SELECT id, account_key, entity_type, entity_id, action, reason, payload_json,
                   attempts, status, available_at, created_at, updated_at, locked_at, last_error
            FROM sync_queue
            WHERE {' AND '.join(where)}
            ORDER BY
                CASE status
                    WHEN 'failed' THEN 0
                    WHEN 'running' THEN 1
                    WHEN 'pending' THEN 2
                    ELSE 3
                END,
                updated_at DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload_json") or "{}")
            except (TypeError, ValueError):
                item["payload"] = {}
            items.append(item)
        return items

    def retry_sync_queue_items(self, account_key: str, queue_ids: list[int]) -> int:
        ids = [int(item) for item in queue_ids if int(item) > 0]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cursor = self.conn.execute(
            f"""
            UPDATE sync_queue
            SET status = 'pending',
                available_at = ?,
                updated_at = ?,
                locked_at = NULL,
                last_error = NULL
            WHERE account_key = ?
              AND status IN ('failed', 'ignored')
              AND id IN ({placeholders})
            """,
            [utc_now(), utc_now(), account_key, *ids],
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def ignore_sync_queue_items(self, account_key: str, queue_ids: list[int]) -> int:
        ids = [int(item) for item in queue_ids if int(item) > 0]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cursor = self.conn.execute(
            f"""
            UPDATE sync_queue
            SET status = 'ignored',
                updated_at = ?,
                locked_at = NULL
            WHERE account_key = ?
              AND status = 'failed'
              AND id IN ({placeholders})
            """,
            [utc_now(), account_key, *ids],
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def cleanup_operational_rows(
        self,
        account_key: str,
        *,
        done_queue_days: int = 30,
        ignored_queue_days: int = 30,
        webhook_days: int = 90,
        sync_history_days: int = 90,
    ) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        done_cutoff = (now - timedelta(days=done_queue_days)).isoformat()
        ignored_cutoff = (now - timedelta(days=ignored_queue_days)).isoformat()
        webhook_cutoff = (now - timedelta(days=webhook_days)).isoformat()
        sync_cutoff = (now - timedelta(days=sync_history_days)).isoformat()
        done_queue = self.conn.execute(
            """
            DELETE FROM sync_queue
            WHERE account_key = ?
              AND status = 'done'
              AND updated_at <= ?
            """,
            (account_key, done_cutoff),
        ).rowcount
        ignored_queue = self.conn.execute(
            """
            DELETE FROM sync_queue
            WHERE account_key = ?
              AND status = 'ignored'
              AND updated_at <= ?
            """,
            (account_key, ignored_cutoff),
        ).rowcount
        webhooks = self.conn.execute(
            """
            DELETE FROM webhook_events
            WHERE account_key = ?
              AND received_at <= ?
            """,
            (account_key, webhook_cutoff),
        ).rowcount
        jobs = self.conn.execute(
            """
            DELETE FROM sync_jobs
            WHERE account_key = ?
              AND status NOT IN ('pending', 'running')
              AND COALESCE(finished_at, started_at) <= ?
            """,
            (account_key, sync_cutoff),
        ).rowcount
        runs = self.conn.execute(
            """
            DELETE FROM sync_runs
            WHERE status NOT IN ('pending', 'running')
              AND COALESCE(finished_at, started_at) <= ?
            """,
            (sync_cutoff,),
        ).rowcount
        self.conn.commit()
        return {
            "sync_queue_done": int(done_queue or 0),
            "sync_queue_ignored": int(ignored_queue or 0),
            "webhook_events": int(webhooks or 0),
            "sync_jobs": int(jobs or 0),
            "sync_runs": int(runs or 0),
        }

    def reset_stale_sync_queue(self, account_key: str | None = None, *, stale_after_minutes: int = 15) -> int:
        now = utc_now()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)).isoformat()
        where = """
            status = 'running'
            AND (
                locked_at IS NULL
                OR locked_at <= ?
            )
        """
        params: list[Any] = [cutoff]
        if account_key:
            where += " AND account_key = ?"
            params.append(account_key)
        cursor = self.conn.execute(
            f"""
            UPDATE sync_queue
            SET status = 'pending',
                locked_at = NULL,
                updated_at = ?,
                last_error = NULL
            WHERE {where}
            """,
            [now, *params],
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def claim_sync_queue(self, account_key: str, limit: int = 25) -> list[dict[str, Any]]:
        now = utc_now()
        rows = self.conn.execute(
            """
            SELECT id, account_key, entity_type, entity_id, action, reason, payload_json, attempts
            FROM sync_queue
            WHERE account_key = ?
              AND status = 'pending'
              AND available_at <= ?
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (account_key, now, limit),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            self.conn.execute(
                f"""
                UPDATE sync_queue
                SET status = 'running', locked_at = ?, updated_at = ?, attempts = attempts + 1
                WHERE id IN ({placeholders})
                """,
                [now, now, *ids],
            )
            self.conn.commit()
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload_json"] or "{}"),
            }
            for row in rows
        ]

    def finish_sync_queue_item(self, queue_id: int, status: str, error: str | None = None) -> None:
        self.conn.execute(
            """
            UPDATE sync_queue
            SET status = ?, updated_at = ?, locked_at = NULL, last_error = ?
            WHERE id = ?
            """,
            (status, utc_now(), error, queue_id),
        )
        self.conn.commit()

    def delete_entity(self, entity_type: str, entity_id: str) -> int:
        params = (entity_type, str(entity_id))
        source_rows = self.conn.execute(
            """
            SELECT DISTINCT source_id
            FROM sync_source_entities
            WHERE entity_type = ? AND entity_id = ?
            """,
            params,
        ).fetchall()
        raw_deleted = self.conn.execute(
            "DELETE FROM raw_entities WHERE entity_type = ? AND entity_id = ?",
            params,
        ).rowcount
        self.conn.execute(
            "DELETE FROM sync_source_entities WHERE entity_type = ? AND entity_id = ?",
            params,
        )
        self.conn.execute(
            "DELETE FROM entity_relations WHERE source_type = ? AND source_id = ?",
            params,
        )
        self.conn.execute(
            "DELETE FROM entity_relations WHERE target_type = ? AND target_id = ?",
            params,
        )
        self.conn.execute(
            "DELETE FROM entity_custom_field_values WHERE entity_type = ? AND entity_id = ?",
            params,
        )
        source_ids = [int(row["source_id"]) for row in source_rows]
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            self.conn.execute(
                f"UPDATE sync_sources SET updated_at = ? WHERE id IN ({placeholders})",
                [utc_now(), *source_ids],
            )
        self.conn.commit()
        return int(raw_deleted or 0)

    def start_sync_job(
        self,
        account_key: str,
        job_type: str,
        entity_types: list[str],
        *,
        status: str = "running",
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO sync_jobs(account_key, job_type, status, entity_types_json, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (account_key, job_type, status, json.dumps(entity_types, ensure_ascii=False), utc_now()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def mark_sync_job_running(self, job_id: int) -> None:
        self.conn.execute(
            """
            UPDATE sync_jobs
            SET status = 'running', error = NULL
            WHERE id = ?
            """,
            (job_id,),
        )
        self.conn.commit()

    def update_sync_job_progress(
        self,
        job_id: int,
        *,
        items_count: int,
        failed_count: int,
        result: list[dict[str, Any]],
        status: str = "running",
    ) -> None:
        self.conn.execute(
            """
            UPDATE sync_jobs
            SET status = ?, items_count = ?, failed_count = ?, result_json = ?
            WHERE id = ?
            """,
            (
                status,
                items_count,
                failed_count,
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                job_id,
            ),
        )
        self.conn.commit()

    def finish_sync_job(
        self,
        job_id: int,
        status: str,
        *,
        items_count: int = 0,
        failed_count: int = 0,
        result: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE sync_jobs
            SET status = ?, finished_at = ?, items_count = ?, failed_count = ?, result_json = ?, error = ?
            WHERE id = ?
            """,
            (
                status,
                utc_now(),
                items_count,
                failed_count,
                json.dumps(result or [], ensure_ascii=False, separators=(",", ":")),
                error,
                job_id,
            ),
        )
        self.conn.commit()

    def interrupt_stale_sync_jobs(
        self,
        account_key: str | None = None,
        *,
        active_job_ids: set[int] | None = None,
        stale_after_minutes: int = 2,
    ) -> int:
        active_job_ids = active_job_ids or set()
        now = utc_now()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)).isoformat()
        where = [
            "status IN ('pending', 'running')",
            "started_at <= ?",
        ]
        params: list[Any] = [cutoff]
        if account_key:
            where.append("account_key = ?")
            params.append(account_key)
        if active_job_ids:
            placeholders = ",".join("?" for _ in active_job_ids)
            where.append(f"id NOT IN ({placeholders})")
            params.extend(sorted(active_job_ids))
        cursor = self.conn.execute(
            f"""
            UPDATE sync_jobs
            SET status = 'interrupted',
                finished_at = ?,
                error = COALESCE(error, ?)
            WHERE {' AND '.join(where)}
            """,
            [
                now,
                "Сервис перезапускался, фоновая задача остановлена. Запустите выгрузку еще раз.",
                *params,
            ],
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def interrupt_stale_sync_runs(self, *, stale_after_minutes: int = 2) -> int:
        now = utc_now()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)).isoformat()
        cursor = self.conn.execute(
            """
            UPDATE sync_runs
            SET status = 'interrupted',
                finished_at = ?,
                error = COALESCE(error, ?)
            WHERE status = 'running'
              AND started_at <= ?
            """,
            (
                now,
                "Сервис перезапускался, запуск синхронизации остановлен.",
                cutoff,
            ),
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def latest_sync_jobs(self, account_key: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, account_key, job_type, status, entity_types_json, started_at,
                   finished_at, items_count, failed_count, error
            FROM sync_jobs
            WHERE account_key = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (account_key, limit),
        ).fetchall()
        return [
            self._decode_sync_job(row)
            for row in rows
        ]

    def get_sync_job(self, job_id: int, account_key: str | None = None) -> dict[str, Any] | None:
        where = "WHERE id = ?"
        params: list[Any] = [job_id]
        if account_key:
            where += " AND account_key = ?"
            params.append(account_key)
        row = self.conn.execute(
            f"""
            SELECT id, account_key, job_type, status, entity_types_json, started_at,
                   finished_at, items_count, failed_count, result_json, error
            FROM sync_jobs
            {where}
            """,
            params,
        ).fetchone()
        return self._decode_sync_job(row) if row else None

    def _decode_sync_job(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        entity_types = json.loads(data.get("entity_types_json") or "[]")
        result = json.loads(data.get("result_json") or "[]")
        done_entities = {
            str(item.get("entity_type"))
            for item in result
            if item.get("entity_type")
            and item.get("status") not in {"pending", "running"}
        }
        data["entity_types"] = entity_types
        data["result"] = result
        data["done_entities"] = len(done_entities)
        data["total_entities"] = len(entity_types)
        data["progress_percent"] = round(len(done_entities) / len(entity_types) * 100) if entity_types else 0
        return data

    def latest_sync_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, entity_type, started_at, finished_at, status, items_count, error
            FROM sync_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_errors(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT 'sync_run' AS source, entity_type, status, error, finished_at AS happened_at
            FROM sync_runs
            WHERE error IS NOT NULL AND error != ''
            UNION ALL
            SELECT 'sync_job' AS source, job_type AS entity_type, status, error, finished_at AS happened_at
            FROM sync_jobs
            WHERE error IS NOT NULL AND error != ''
            UNION ALL
            SELECT 'sync_queue' AS source, entity_type, status, last_error AS error, updated_at AS happened_at
            FROM sync_queue
            WHERE last_error IS NOT NULL AND last_error != ''
            ORDER BY happened_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def start_sync_run(self, entity_type: str) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO sync_runs(entity_type, started_at, status)
            VALUES (?, ?, ?)
            """,
            (entity_type, utc_now(), "running"),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        run_id: int,
        status: str,
        items_count: int,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE sync_runs
            SET finished_at = ?, status = ?, items_count = ?, error = ?
            WHERE id = ?
            """,
            (utc_now(), status, items_count, error, run_id),
        )
        self.conn.commit()

    def upsert_entities(self, entity_type: str, items: list[dict[str, Any]]) -> int:
        synced_at = utc_now()
        rows = []
        for item in items:
            entity_id = str(item.get("id"))
            rows.append((
                entity_type,
                entity_id,
                item.get("name"),
                json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                item.get("updated_at"),
                synced_at,
            ))
        entity_keys = [(entity_type, str(item.get("id"))) for item in items if item.get("id") is not None]
        self.conn.executemany(
            """
            INSERT INTO raw_entities(entity_type, entity_id, name, payload_json, updated_at, synced_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                name = excluded.name,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at,
                synced_at = excluded.synced_at
            """,
            rows,
        )
        if entity_keys:
            self.conn.executemany(
                "DELETE FROM entity_relations WHERE source_type = ? AND source_id = ?",
                entity_keys,
            )
            self.conn.executemany(
                "DELETE FROM entity_custom_field_values WHERE entity_type = ? AND entity_id = ?",
                entity_keys,
            )
            self._insert_relation_rows(entity_type, items, synced_at)
            self._insert_custom_field_rows(entity_type, items, synced_at)
        self.conn.commit()
        return len(rows)

    def _insert_relation_rows(self, entity_type: str, items: list[dict[str, Any]], synced_at: str) -> None:
        rows = []
        for item in items:
            source_id = item.get("id")
            if source_id is None:
                continue
            parent_type, parent_id = self._parent_relation(entity_type, item)
            if parent_type and parent_id is not None:
                rows.append((
                    entity_type,
                    str(source_id),
                    parent_type,
                    str(parent_id),
                    "parent",
                    None,
                    synced_at,
                ))
            embedded = item.get("_embedded") or {}
            relation_specs = [
                ("contacts", "contacts", "linked_contact"),
                ("companies", "companies", "linked_company"),
                ("leads", "leads", "linked_lead"),
                ("customers", "customers", "linked_customer"),
                ("catalog_elements", "catalog_elements", "linked_catalog_element"),
                ("tags", "tags", "tag"),
            ]
            for key, target_type, relation_type in relation_specs:
                values = embedded.get(key) or []
                if isinstance(values, dict):
                    values = [values]
                for value in values:
                    if not isinstance(value, dict) or value.get("id") is None:
                        continue
                    rows.append((
                        entity_type,
                        str(source_id),
                        target_type,
                        str(value.get("id")),
                        relation_type,
                        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                        synced_at,
                    ))
        if rows:
            self.conn.executemany(
                """
                INSERT INTO entity_relations(
                    source_type, source_id, target_type, target_id, relation_type, payload_json, synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_type, source_id, target_type, target_id, relation_type) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    synced_at = excluded.synced_at
                """,
                rows,
            )

    def _parent_relation(self, entity_type: str, item: dict[str, Any]) -> tuple[str | None, Any | None]:
        note_parents = {
            "lead_notes": "leads",
            "contact_notes": "contacts",
            "company_notes": "companies",
            "customer_notes": "customers",
        }
        if entity_type in note_parents:
            return note_parents[entity_type], item.get("entity_id")
        if entity_type == "tasks":
            parent_type = item.get("entity_type")
            parent_id = item.get("entity_id")
            if parent_type and parent_id is not None:
                return str(parent_type), parent_id
        if entity_type == "events":
            parent_type = item.get("entity_type") or item.get("entity")
            parent_id = item.get("entity_id")
            if parent_type and parent_id is not None:
                return str(parent_type), parent_id
        if entity_type == "catalog_elements":
            return "catalogs", item.get("catalog_id")
        return None, None

    def _insert_custom_field_rows(self, entity_type: str, items: list[dict[str, Any]], synced_at: str) -> None:
        rows = []
        for item in items:
            entity_id = item.get("id")
            if entity_id is None:
                continue
            fields = item.get("custom_fields_values") or []
            if not isinstance(fields, list):
                continue
            for field in fields:
                if not isinstance(field, dict) or field.get("field_id") is None:
                    continue
                values = field.get("values") or []
                if not isinstance(values, list):
                    values = [values]
                for index, value in enumerate(values):
                    if not isinstance(value, dict):
                        value = {"value": value}
                    raw_value = value.get("value")
                    value_num = None
                    try:
                        if raw_value not in (None, ""):
                            value_num = float(raw_value)
                    except (TypeError, ValueError):
                        value_num = None
                    rows.append((
                        entity_type,
                        str(entity_id),
                        int(field.get("field_id")),
                        field.get("field_code"),
                        field.get("field_name"),
                        index,
                        value.get("enum_id"),
                        value.get("enum_code"),
                        None if raw_value is None else str(raw_value),
                        value_num,
                        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                        synced_at,
                    ))
        if rows:
            self.conn.executemany(
                """
                INSERT INTO entity_custom_field_values(
                    entity_type, entity_id, field_id, field_code, field_name, value_index,
                    enum_id, enum_code, value_text, value_num, value_json, synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_type, entity_id, field_id, value_index) DO UPDATE SET
                    field_code = excluded.field_code,
                    field_name = excluded.field_name,
                    enum_id = excluded.enum_id,
                    enum_code = excluded.enum_code,
                    value_text = excluded.value_text,
                    value_num = excluded.value_num,
                    value_json = excluded.value_json,
                    synced_at = excluded.synced_at
                """,
                rows,
            )

    def list_entities(self, entity_type: str, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            """
            SELECT entity_id, name, payload_json, updated_at, synced_at
            FROM raw_entities
            WHERE entity_type = ?
            ORDER BY updated_at DESC NULLS LAST, entity_id DESC
            LIMIT ? OFFSET ?
            """,
            (entity_type, limit, offset),
        )
        return [
            {
                "id": row["entity_id"],
                "name": row["name"],
                "updated_at": row["updated_at"],
                "synced_at": row["synced_at"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in cursor.fetchall()
        ]

    def all_payloads(self, entity_type: str) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT payload_json FROM raw_entities WHERE entity_type = ?",
            (entity_type,),
        )
        return [json.loads(row["payload_json"]) for row in cursor.fetchall()]

    def raw_entities_since(self, entity_type: str, since: datetime, limit: int = 5000) -> list[dict[str, Any]]:
        timestamp = int(since.timestamp())
        cursor = self.conn.execute(
            """
            SELECT entity_id, name, payload_json, updated_at, synced_at
            FROM raw_entities
            WHERE entity_type = ?
              AND (
                updated_at IS NULL
                OR updated_at >= ?
                OR synced_at >= ?
              )
            ORDER BY COALESCE(updated_at, 0) DESC, synced_at DESC
            LIMIT ?
            """,
            (entity_type, timestamp, since.isoformat(), limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_raw_entity(self, entity_type: str, entity_id: str | int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT entity_id, name, payload_json, updated_at, synced_at
            FROM raw_entities
            WHERE entity_type = ? AND entity_id = ?
            """,
            (entity_type, str(entity_id)),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def webhook_events_since(self, since: datetime, limit: int = 5000) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            """
            SELECT id, account_key, event_type, entity_type, entity_id, payload_json, received_at, status, error
            FROM webhook_events
            WHERE received_at >= ?
            ORDER BY received_at DESC
            LIMIT ?
            """,
            (since.isoformat(), limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def replace_activity_marts(self, pulse: dict[str, Any]) -> dict[str, int]:
        activity_date = str(pulse["date"])
        slot_minutes = int(pulse.get("slot_minutes") or 15)
        slot_labels = list(pulse.get("slot_labels") or [])
        updated_at = utc_now()
        daily_rows = []
        slot_rows = []
        for user in pulse.get("users") or []:
            user_id = user.get("user_id")
            user_name = str(user.get("user_name") or "System")
            user_key = str(user_id or f"name:{user_name}")
            daily_rows.append((
                activity_date,
                slot_minutes,
                user_key,
                None if user_id in (None, "") else str(user_id),
                user_name,
                int(user.get("activity_count") or 0),
                int(user.get("activity_score") or 0),
                int(user.get("active_minutes") or 0),
                user.get("first_activity") or "",
                user.get("last_activity") or "",
                int(user.get("idle_minutes") or 0),
                int(user.get("idle_periods_count") or 0),
                json.dumps(user.get("idle_periods") or [], ensure_ascii=False, separators=(",", ":")),
                int(user.get("tasks_due") or 0),
                int(user.get("tasks_completed") or 0),
                int(user.get("tasks_overdue") or 0),
                int(user.get("calls_out") or 0),
                int(user.get("calls_in") or 0),
                int(user.get("calls_missed") or 0),
                int(user.get("notes") or 0),
                int(user.get("leads_created") or 0),
                int(user.get("stage_changes") or 0),
                updated_at,
            ))
            for index, slot in enumerate(user.get("slots") or []):
                slot_rows.append((
                    activity_date,
                    slot_minutes,
                    user_key,
                    index,
                    str(slot_labels[index] if index < len(slot_labels) else ""),
                    int(slot.get("count") or 0),
                    int(slot.get("score") or 0),
                    int(slot.get("level") or 0),
                    updated_at,
                ))
        self.conn.execute(
            "DELETE FROM activity_slots WHERE activity_date = ? AND slot_minutes = ?",
            (activity_date, slot_minutes),
        )
        self.conn.execute(
            "DELETE FROM activity_daily_user WHERE activity_date = ? AND slot_minutes = ?",
            (activity_date, slot_minutes),
        )
        if daily_rows:
            self.conn.executemany(
                """
                INSERT INTO activity_daily_user(
                    activity_date, slot_minutes, user_key, user_id, user_name,
                    activity_count, activity_score, active_minutes, first_activity, last_activity,
                    idle_minutes, idle_periods_count, idle_periods_json,
                    tasks_due, tasks_completed, tasks_overdue,
                    calls_out, calls_in, calls_missed, notes, leads_created, stage_changes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                daily_rows,
            )
        if slot_rows:
            self.conn.executemany(
                """
                INSERT INTO activity_slots(
                    activity_date, slot_minutes, user_key, slot_index, slot_label,
                    activity_count, activity_score, level, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                slot_rows,
            )
        self.conn.commit()
        return {"daily_rows": len(daily_rows), "slot_rows": len(slot_rows)}

    def activity_pulse_mart(self, activity_date: str, slot_minutes: int = 15) -> dict[str, Any] | None:
        daily = self.conn.execute(
            """
            SELECT *
            FROM activity_daily_user
            WHERE activity_date = ? AND slot_minutes = ?
            ORDER BY activity_score DESC, activity_count DESC, user_name
            """,
            (activity_date, slot_minutes),
        ).fetchall()
        if not daily:
            return None
        slot_rows = self.conn.execute(
            """
            SELECT *
            FROM activity_slots
            WHERE activity_date = ? AND slot_minutes = ?
            ORDER BY user_key, slot_index
            """,
            (activity_date, slot_minutes),
        ).fetchall()
        slots_by_user: dict[str, list[dict[str, int]]] = {}
        slot_labels_by_index: dict[int, str] = {}
        for row in slot_rows:
            slot_labels_by_index[int(row["slot_index"])] = str(row["slot_label"] or "")
            slots_by_user.setdefault(str(row["user_key"]), []).append({
                "count": int(row["activity_count"] or 0),
                "score": int(row["activity_score"] or 0),
                "level": int(row["level"] or 0),
            })
        slot_labels = [
            slot_labels_by_index[index]
            for index in sorted(slot_labels_by_index)
        ]
        users = []
        for row in daily:
            user_key = str(row["user_key"])
            users.append({
                "user_id": row["user_id"],
                "user_name": row["user_name"],
                "activity_count": int(row["activity_count"] or 0),
                "activity_score": int(row["activity_score"] or 0),
                "slots": slots_by_user.get(user_key, []),
                "active_minutes": int(row["active_minutes"] or 0),
                "first_activity": row["first_activity"] or "",
                "last_activity": row["last_activity"] or "",
                "idle_minutes": int(row["idle_minutes"] or 0),
                "idle_periods_count": int(row["idle_periods_count"] or 0),
                "idle_periods": json.loads(row["idle_periods_json"] or "[]"),
                "tasks_due": int(row["tasks_due"] or 0),
                "tasks_completed": int(row["tasks_completed"] or 0),
                "tasks_overdue": int(row["tasks_overdue"] or 0),
                "calls_out": int(row["calls_out"] or 0),
                "calls_in": int(row["calls_in"] or 0),
                "calls_missed": int(row["calls_missed"] or 0),
                "notes": int(row["notes"] or 0),
                "leads_created": int(row["leads_created"] or 0),
                "stage_changes": int(row["stage_changes"] or 0),
            })
        return {
            "date": activity_date,
            "slot_minutes": slot_minutes,
            "slot_labels": slot_labels,
            "totals": {
                "activity_count": sum(row["activity_count"] for row in users),
                "activity_score": sum(row["activity_score"] for row in users),
                "active_users": len([row for row in users if row["activity_count"] > 0 and row["user_name"] != "System"]),
                "idle_periods": sum(row["idle_periods_count"] for row in users),
                "tasks_due": sum(row["tasks_due"] for row in users),
                "tasks_completed": sum(row["tasks_completed"] for row in users),
                "tasks_overdue": sum(row["tasks_overdue"] for row in users),
                "calls_out": sum(row["calls_out"] for row in users),
                "calls_in": sum(row["calls_in"] for row in users),
                "calls_missed": sum(row["calls_missed"] for row in users),
                "calls_total": sum(row["calls_out"] + row["calls_in"] + row["calls_missed"] for row in users),
            },
            "users": users,
        }

    def activity_mart_status(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT activity_date, slot_minutes, COUNT(*) AS users_count,
                   SUM(activity_count) AS activity_count,
                   SUM(activity_score) AS activity_score,
                   MAX(updated_at) AS updated_at
            FROM activity_daily_user
            GROUP BY activity_date, slot_minutes
            ORDER BY activity_date DESC, slot_minutes
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def replace_lead_kpi_daily(self, activity_date: str, rows: list[dict[str, Any]]) -> dict[str, int]:
        updated_at = utc_now()
        self.conn.execute(
            "DELETE FROM lead_kpi_daily WHERE activity_date = ?",
            (activity_date,),
        )
        insert_rows = []
        for row in rows:
            user_id = row.get("user_id")
            user_name = str(row.get("user_name") or "System")
            user_key = str(user_id or f"name:{user_name}")
            insert_rows.append((
                activity_date,
                user_key,
                None if user_id in (None, "") else str(user_id),
                user_name,
                int(row.get("pipeline_id") or 0),
                str(row.get("pipeline_name") or ""),
                int(row.get("status_id") or 0),
                str(row.get("status_name") or ""),
                int(row.get("created_count") or 0),
                int(row.get("updated_count") or 0),
                int(row.get("closed_count") or 0),
                int(row.get("won_count") or 0),
                int(row.get("lost_count") or 0),
                int(row.get("open_count") or 0),
                int(row.get("created_price") or 0),
                int(row.get("closed_price") or 0),
                int(row.get("open_price") or 0),
                updated_at,
            ))
        if insert_rows:
            self.conn.executemany(
                """
                INSERT INTO lead_kpi_daily(
                    activity_date, user_key, user_id, user_name,
                    pipeline_id, pipeline_name, status_id, status_name,
                    created_count, updated_count, closed_count, won_count, lost_count, open_count,
                    created_price, closed_price, open_price, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                insert_rows,
            )
        self.conn.commit()
        return {"rows": len(insert_rows)}

    def lead_kpi_daily(self, activity_date: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM lead_kpi_daily
            WHERE activity_date = ?
            ORDER BY created_count DESC, updated_count DESC, open_count DESC, user_name, pipeline_name, status_name
            LIMIT ?
            """,
            (activity_date, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def lead_kpi_daily_totals(self, activity_date: str) -> dict[str, int]:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS rows_count,
                   COUNT(DISTINCT user_key) AS active_users,
                   COALESCE(SUM(created_count), 0) AS created_count,
                   COALESCE(SUM(updated_count), 0) AS updated_count,
                   COALESCE(SUM(closed_count), 0) AS closed_count,
                   COALESCE(SUM(won_count), 0) AS won_count,
                   COALESCE(SUM(lost_count), 0) AS lost_count,
                   COALESCE(SUM(open_count), 0) AS open_count,
                   COALESCE(SUM(created_price), 0) AS created_price,
                   COALESCE(SUM(closed_price), 0) AS closed_price,
                   COALESCE(SUM(open_price), 0) AS open_price
            FROM lead_kpi_daily
            WHERE activity_date = ?
            """,
            (activity_date,),
        ).fetchone()
        if not row:
            return {
                "created_count": 0,
                "updated_count": 0,
                "closed_count": 0,
                "won_count": 0,
                "lost_count": 0,
                "open_count": 0,
                "created_price": 0,
                "closed_price": 0,
                "open_price": 0,
                "rows_count": 0,
                "active_users": 0,
            }
        return {
            "created_count": int(row["created_count"] or 0),
            "updated_count": int(row["updated_count"] or 0),
            "closed_count": int(row["closed_count"] or 0),
            "won_count": int(row["won_count"] or 0),
            "lost_count": int(row["lost_count"] or 0),
            "open_count": int(row["open_count"] or 0),
            "created_price": int(row["created_price"] or 0),
            "closed_price": int(row["closed_price"] or 0),
            "open_price": int(row["open_price"] or 0),
            "rows_count": int(row["rows_count"] or 0),
            "active_users": int(row["active_users"] or 0),
        }

    def lead_kpi_daily_status(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT activity_date, COUNT(*) AS rows_count,
                   SUM(created_count) AS created_count,
                   SUM(updated_count) AS updated_count,
                   SUM(closed_count) AS closed_count,
                   SUM(won_count) AS won_count,
                   SUM(lost_count) AS lost_count,
                   SUM(open_count) AS open_count,
                   MAX(updated_at) AS updated_at
            FROM lead_kpi_daily
            GROUP BY activity_date
            ORDER BY activity_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_conversation_records(self, records: list[Any]) -> int:
        if not records:
            return 0
        now = utc_now()
        rows = []
        for record in records:
            item = record if isinstance(record, dict) else record.__dict__
            rows.append((
                str(item["account_key"]),
                str(item["conversation_id"]),
                str(item["source_type"]),
                str(item["source_id"]),
                None if item.get("lead_id") is None else str(item.get("lead_id")),
                None if item.get("contact_id") is None else str(item.get("contact_id")),
                str(item.get("direction") or "unknown"),
                str(item.get("kind") or "call"),
                item.get("recording_url"),
                item.get("transcript_text"),
                item.get("duration_seconds"),
                item.get("occurred_at"),
                str(item.get("status") or "pending"),
                json.dumps(item.get("metadata") or {}, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ))
        self.conn.executemany(
            """
            INSERT INTO conversation_records(
                account_key, conversation_id, source_type, source_id, lead_id, contact_id,
                direction, kind, recording_url, transcript_text, duration_seconds, occurred_at,
                status, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_key, conversation_id) DO UPDATE SET
                source_type = excluded.source_type,
                source_id = excluded.source_id,
                lead_id = excluded.lead_id,
                contact_id = excluded.contact_id,
                direction = excluded.direction,
                kind = excluded.kind,
                recording_url = excluded.recording_url,
                transcript_text = COALESCE(excluded.transcript_text, conversation_records.transcript_text),
                duration_seconds = excluded.duration_seconds,
                occurred_at = excluded.occurred_at,
                status = CASE
                    WHEN conversation_records.transcript_text IS NOT NULL
                     AND excluded.transcript_text IS NULL
                    THEN conversation_records.status
                    WHEN conversation_records.status IN ('audio_downloaded', 'transcribed')
                     AND excluded.status IN ('recording_found', 'audio_accessible')
                    THEN conversation_records.status
                    ELSE excluded.status
                END,
                metadata_json = CASE
                    WHEN conversation_records.metadata_json IS NULL
                      OR conversation_records.metadata_json = ''
                    THEN excluded.metadata_json
                    ELSE json_patch(conversation_records.metadata_json, excluded.metadata_json)
                END,
                updated_at = excluded.updated_at
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def list_conversation_records(
        self,
        account_key: str,
        *,
        status: str | None = None,
        statuses: list[str] | None = None,
        without_analysis: bool = False,
        stale_analysis_sources: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where = ["record.account_key = ?"]
        params: list[Any] = [account_key]
        if status:
            where.append("record.status = ?")
            params.append(status)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where.append(f"record.status IN ({placeholders})")
            params.extend(statuses)
        join = ""
        stale_analysis_sources = stale_analysis_sources or []
        if without_analysis or stale_analysis_sources:
            join = """
            LEFT JOIN conversation_analysis AS analysis
              ON analysis.account_key = record.account_key
             AND analysis.conversation_id = record.conversation_id
            """
        if without_analysis and not stale_analysis_sources:
            where.append("analysis.conversation_id IS NULL")
        elif stale_analysis_sources:
            stale_placeholders = ",".join("?" for _ in stale_analysis_sources)
            where.append(
                f"""(
                    analysis.conversation_id IS NULL
                    OR json_extract(analysis.analysis_json, '$.source') IS NULL
                    OR json_extract(analysis.analysis_json, '$.source') = ''
                    OR json_extract(analysis.analysis_json, '$.source') IN ({stale_placeholders})
                )"""
            )
            params.extend(stale_analysis_sources)
        rows = self.conn.execute(
            f"""
            SELECT record.*
            FROM conversation_records AS record
            {join}
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(record.occurred_at, 0) DESC, record.updated_at DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            result.append(item)
        return result

    def update_conversation_record_status(
        self,
        account_key: str,
        conversation_id: str,
        *,
        status: str,
        metadata_patch: dict[str, Any] | None = None,
    ) -> None:
        row = self.conn.execute(
            """
            SELECT metadata_json
            FROM conversation_records
            WHERE account_key = ? AND conversation_id = ?
            """,
            (account_key, conversation_id),
        ).fetchone()
        if not row:
            return
        metadata = json.loads(row["metadata_json"] or "{}")
        if metadata_patch:
            metadata.update(metadata_patch)
        self.conn.execute(
            """
            UPDATE conversation_records
            SET status = ?, metadata_json = ?, updated_at = ?
            WHERE account_key = ? AND conversation_id = ?
            """,
            (
                status,
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                utc_now(),
                account_key,
                conversation_id,
            ),
        )
        self.conn.commit()

    def set_conversation_transcript(
        self,
        account_key: str,
        conversation_id: str,
        *,
        transcript_text: str,
        status: str = "transcribed",
        metadata_patch: dict[str, Any] | None = None,
    ) -> None:
        row = self.conn.execute(
            """
            SELECT metadata_json
            FROM conversation_records
            WHERE account_key = ? AND conversation_id = ?
            """,
            (account_key, conversation_id),
        ).fetchone()
        if not row:
            return
        metadata = json.loads(row["metadata_json"] or "{}")
        if metadata_patch:
            metadata.update(metadata_patch)
        self.conn.execute(
            """
            UPDATE conversation_records
            SET transcript_text = ?, status = ?, metadata_json = ?, updated_at = ?
            WHERE account_key = ? AND conversation_id = ?
            """,
            (
                transcript_text,
                status,
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                utc_now(),
                account_key,
                conversation_id,
            ),
        )
        self.conn.commit()

    def upsert_conversation_analyses(self, analyses: list[Any]) -> int:
        if not analyses:
            return 0
        now = utc_now()
        rows = []
        for analysis in analyses:
            item = analysis if isinstance(analysis, dict) else analysis.__dict__
            rows.append((
                str(item["account_key"]),
                str(item["conversation_id"]),
                str(item.get("summary") or ""),
                str(item.get("sentiment") or "neutral"),
                int(item.get("score") or 0),
                item.get("next_step"),
                json.dumps(item.get("objections") or [], ensure_ascii=False, separators=(",", ":")),
                json.dumps(item.get("recommendations") or [], ensure_ascii=False, separators=(",", ":")),
                json.dumps(item.get("metrics") or {}, ensure_ascii=False, separators=(",", ":")),
                json.dumps(item.get("analysis") or {}, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ))
        self.conn.executemany(
            """
            INSERT INTO conversation_analysis(
                account_key, conversation_id, summary, sentiment, score, next_step,
                objections_json, recommendations_json, metrics_json, analysis_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_key, conversation_id) DO UPDATE SET
                summary = excluded.summary,
                sentiment = excluded.sentiment,
                score = excluded.score,
                next_step = excluded.next_step,
                objections_json = excluded.objections_json,
                recommendations_json = excluded.recommendations_json,
                metrics_json = excluded.metrics_json,
                analysis_json = excluded.analysis_json,
                updated_at = excluded.updated_at
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def list_conversation_analyses(self, account_key: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
                analysis.*,
                record.lead_id,
                record.direction,
                record.recording_url,
                record.occurred_at
            FROM conversation_analysis AS analysis
            LEFT JOIN conversation_records AS record
              ON record.account_key = analysis.account_key
             AND record.conversation_id = analysis.conversation_id
            WHERE analysis.account_key = ?
            ORDER BY analysis.updated_at DESC
            LIMIT ?
            """,
            (account_key, limit),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["objections"] = json.loads(item.pop("objections_json") or "[]")
            item["recommendations"] = json.loads(item.pop("recommendations_json") or "[]")
            item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
            item["analysis"] = json.loads(item.pop("analysis_json") or "{}")
            result.append(item)
        return result

    def lead_kpi_source_rows(self, day_start_ts: int, day_end_ts: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            WITH lead_values AS (
                SELECT
                    CAST(json_extract(payload_json, '$.responsible_user_id') AS TEXT) AS user_id,
                    COALESCE(CAST(json_extract(payload_json, '$.pipeline_id') AS INTEGER), 0) AS pipeline_id,
                    COALESCE(CAST(json_extract(payload_json, '$.status_id') AS INTEGER), 0) AS status_id,
                    COALESCE(CAST(json_extract(payload_json, '$.price') AS INTEGER), 0) AS price,
                    COALESCE(CAST(json_extract(payload_json, '$.created_at') AS INTEGER), 0) AS created_at,
                    COALESCE(CAST(json_extract(payload_json, '$.updated_at') AS INTEGER), updated_at, 0) AS updated_at,
                    COALESCE(CAST(json_extract(payload_json, '$.closed_at') AS INTEGER), 0) AS closed_at
                FROM raw_entities
                WHERE entity_type = 'leads'
            )
            SELECT
                user_id,
                pipeline_id,
                status_id,
                SUM(CASE WHEN created_at >= ? AND created_at < ? THEN 1 ELSE 0 END) AS created_count,
                SUM(CASE WHEN updated_at >= ? AND updated_at < ? THEN 1 ELSE 0 END) AS updated_count,
                SUM(CASE WHEN closed_at >= ? AND closed_at < ? THEN 1 ELSE 0 END) AS closed_count,
                SUM(CASE WHEN status_id = 142 AND closed_at >= ? AND closed_at < ? THEN 1 ELSE 0 END) AS won_count,
                SUM(CASE WHEN status_id = 143 AND closed_at >= ? AND closed_at < ? THEN 1 ELSE 0 END) AS lost_count,
                SUM(CASE WHEN status_id NOT IN (142, 143) AND (created_at = 0 OR created_at < ?) THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN created_at >= ? AND created_at < ? THEN price ELSE 0 END) AS created_price,
                SUM(CASE WHEN closed_at >= ? AND closed_at < ? THEN price ELSE 0 END) AS closed_price,
                SUM(CASE WHEN status_id NOT IN (142, 143) AND (created_at = 0 OR created_at < ?) THEN price ELSE 0 END) AS open_price
            FROM lead_values
            GROUP BY user_id, pipeline_id, status_id
            HAVING created_count > 0
                OR updated_count > 0
                OR closed_count > 0
                OR won_count > 0
                OR lost_count > 0
                OR open_count > 0
            """,
            (
                day_start_ts,
                day_end_ts,
                day_start_ts,
                day_end_ts,
                day_start_ts,
                day_end_ts,
                day_start_ts,
                day_end_ts,
                day_start_ts,
                day_end_ts,
                day_end_ts,
                day_start_ts,
                day_end_ts,
                day_start_ts,
                day_end_ts,
                day_end_ts,
            ),
        ).fetchall()
        return [dict(row) for row in rows]

    def hub_overview(self) -> dict[str, Any]:
        entity_rows = self.hub_entity_overview()
        relation_rows = self.conn.execute(
            """
            SELECT source_type, target_type, relation_type, COUNT(*) AS items_count
            FROM entity_relations
            GROUP BY source_type, target_type, relation_type
            ORDER BY source_type, target_type, relation_type
            """
        ).fetchall()
        field_rows = self.conn.execute(
            """
            SELECT entity_type, field_id, field_name, COUNT(*) AS values_count
            FROM entity_custom_field_values
            GROUP BY entity_type, field_id, field_name
            ORDER BY values_count DESC
            LIMIT 100
            """
        ).fetchall()
        return {
            "entities": entity_rows,
            "relations": [dict(row) for row in relation_rows],
            "custom_fields": [dict(row) for row in field_rows],
        }

    def hub_entity_overview(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT entity_type, COUNT(*) AS items_count, MAX(synced_at) AS last_synced_at
            FROM raw_entities
            GROUP BY entity_type
            ORDER BY entity_type
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def lead_analytics_fields(self) -> dict[str, list[dict[str, Any]]]:
        base_fields = [
            {"value": "lead_id", "label": "ID сделки", "kind": "base", "type": "numeric", "groupable": True},
            {"value": "name", "label": "Название сделки", "kind": "base", "type": "text", "groupable": True},
            {"value": "pipeline_id", "label": "Воронка ID", "kind": "base", "type": "numeric", "groupable": True},
            {"value": "status_id", "label": "Этап ID", "kind": "base", "type": "numeric", "groupable": True},
            {"value": "responsible_user_id", "label": "Ответственный ID", "kind": "base", "type": "numeric", "groupable": True},
            {"value": "price", "label": "Бюджет", "kind": "base", "type": "numeric", "groupable": True},
            {"value": "created_at", "label": "Дата создания", "kind": "base", "type": "date", "groupable": False},
            {"value": "updated_at", "label": "Дата обновления", "kind": "base", "type": "date", "groupable": False},
            {"value": "closed_at", "label": "Дата закрытия", "kind": "base", "type": "date", "groupable": False},
            {"value": "created_month", "label": "Месяц создания", "kind": "base", "type": "month", "groupable": True},
            {"value": "updated_month", "label": "Месяц обновления", "kind": "base", "type": "month", "groupable": True},
            {"value": "closed_month", "label": "Месяц закрытия", "kind": "base", "type": "month", "groupable": True},
        ]
        fields: dict[int, dict[str, Any]] = {}
        rows = self.conn.execute(
            """
            SELECT entity_id, name, payload_json
            FROM raw_entities
            WHERE entity_type = 'lead_custom_fields'
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            field_id = int(payload.get("id") or row["entity_id"] or 0)
            if not field_id:
                continue
            name = html.unescape(str(payload.get("name") or row["name"] or f"Поле {field_id}"))
            field_type = str(payload.get("type") or "")
            fields[field_id] = {
                "value": f"cf_{field_id}",
                "label": name,
                "kind": "custom",
                "type": field_type,
                "groupable": True,
            }
            if field_type in {"date", "birthday"}:
                fields[field_id]["month_value"] = f"cf_month_{field_id}"

        indexed_rows = self.conn.execute(
            """
            SELECT field_id, MAX(field_name) AS field_name, COUNT(*) AS values_count,
                   SUM(CASE WHEN value_num IS NOT NULL THEN 1 ELSE 0 END) AS numeric_count
            FROM entity_custom_field_values
            WHERE entity_type = 'leads'
            GROUP BY field_id
            ORDER BY field_name COLLATE NOCASE
            """
        ).fetchall()
        for row in indexed_rows:
            field_id = int(row["field_id"] or 0)
            if not field_id:
                continue
            fields.setdefault(
                field_id,
                {
                    "value": f"cf_{field_id}",
                    "label": html.unescape(str(row["field_name"] or f"Поле {field_id}")),
                    "kind": "custom",
                    "type": "unknown",
                    "groupable": True,
                },
            )
            fields[field_id]["values_count"] = int(row["values_count"] or 0)
            fields[field_id]["numeric_count"] = int(row["numeric_count"] or 0)

        for field in fields.values():
            values_count = int(field.get("values_count") or 0)
            numeric_count = int(field.get("numeric_count") or 0)
            if field.get("type") in {"date_time", "date_time_range"}:
                field["suggested_value_type"] = "datetime"
            elif field.get("type") in {"date", "birthday"}:
                field["suggested_value_type"] = "date"
            elif values_count and numeric_count / values_count >= 0.9:
                field["suggested_value_type"] = "number"
            else:
                field["suggested_value_type"] = "text"

        custom_fields = sorted(fields.values(), key=lambda item: str(item["label"]).lower())
        group_fields = [
            field
            for field in base_fields + custom_fields
            if field.get("groupable")
        ]
        for field in custom_fields:
            month_value = field.get("month_value")
            if month_value:
                group_fields.append({
                    "value": month_value,
                    "label": f"Месяц: {field['label']}",
                    "kind": "custom_month",
                    "type": "month",
                    "groupable": True,
                })
        return {
            "filter_fields": base_fields[:9] + custom_fields,
            "group_fields": group_fields,
        }

    def rebuild_hub_indexes(self, entity_types: list[str] | None = None) -> dict[str, int]:
        where = ""
        params: list[Any] = []
        if entity_types:
            placeholders = ",".join("?" for _ in entity_types)
            where = f"WHERE entity_type IN ({placeholders})"
            params.extend(entity_types)
        rows = self.conn.execute(
            f"SELECT entity_type, payload_json FROM raw_entities {where} ORDER BY entity_type",
            params,
        ).fetchall()
        if entity_types:
            self.conn.execute(
                f"DELETE FROM entity_relations WHERE source_type IN ({placeholders})",
                params,
            )
            self.conn.execute(
                f"DELETE FROM entity_custom_field_values WHERE entity_type IN ({placeholders})",
                params,
            )
        else:
            self.conn.execute("DELETE FROM entity_relations")
            self.conn.execute("DELETE FROM entity_custom_field_values")
        counts: dict[str, int] = {}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            entity_type = row["entity_type"]
            grouped.setdefault(entity_type, []).append(json.loads(row["payload_json"]))
        synced_at = utc_now()
        for entity_type, items in grouped.items():
            self._insert_relation_rows(entity_type, items, synced_at)
            self._insert_custom_field_rows(entity_type, items, synced_at)
            counts[entity_type] = len(items)
        self.conn.commit()
        return counts

    def tasks_summary_counts(self) -> dict[str, int]:
        try:
            row = self.conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE
                        WHEN json_extract(payload_json, '$.is_completed') THEN 1
                        ELSE 0
                    END), 0) AS completed
                FROM raw_entities
                WHERE entity_type = 'tasks'
                """
            ).fetchone()
            total = int(row["total"] or 0)
            completed = int(row["completed"] or 0)
        except Exception:
            tasks = self.all_payloads("tasks")
            total = len(tasks)
            completed = sum(1 for task in tasks if task.get("is_completed"))
        return {
            "total": total,
            "completed": completed,
            "incomplete": total - completed,
        }

    def lead_stage_summary_rows(self, analytics_filter: AnalyticsFilter) -> list[dict[str, int]]:
        where = ["entity_type = 'leads'"]
        params: list[Any] = []
        if analytics_filter.pipeline_ids:
            placeholders = ",".join("?" for _ in analytics_filter.pipeline_ids)
            where.append(f"CAST(json_extract(payload_json, '$.pipeline_id') AS INTEGER) IN ({placeholders})")
            params.extend(sorted(analytics_filter.pipeline_ids))
        if analytics_filter.status_ids:
            placeholders = ",".join("?" for _ in analytics_filter.status_ids)
            where.append(f"CAST(json_extract(payload_json, '$.status_id') AS INTEGER) IN ({placeholders})")
            params.extend(sorted(analytics_filter.status_ids))

        cursor = self.conn.execute(
            f"""
            SELECT
                CAST(json_extract(payload_json, '$.pipeline_id') AS INTEGER) AS pipeline_id,
                CAST(json_extract(payload_json, '$.status_id') AS INTEGER) AS status_id,
                COUNT(*) AS leads_count,
                COALESCE(SUM(CAST(json_extract(payload_json, '$.price') AS INTEGER)), 0) AS total_price
            FROM raw_entities
            WHERE {' AND '.join(where)}
            GROUP BY pipeline_id, status_id
            """,
            params,
        )
        return [
            {
                "pipeline_id": int(row["pipeline_id"] or 0),
                "status_id": int(row["status_id"] or 0),
                "leads_count": int(row["leads_count"] or 0),
                "total_price": int(row["total_price"] or 0),
            }
            for row in cursor.fetchall()
        ]
