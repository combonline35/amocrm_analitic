from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from amocrm_service.amocrm import AmoCRMClient
from amocrm_service.config import Settings
from amocrm_service.conversation_audio import ConversationAudioService
from amocrm_service.conversation_export import ConversationExportService
from amocrm_service.conversation_notes import build_lead_analysis_note, find_record_and_analysis
from amocrm_service.conversation_settings import conversation_settings, record_matches_filters
from amocrm_service.conversation_transcription import build_transcription_service
from amocrm_service.conversations import CALL_NOTE_TYPES, ConversationPipeline, extract_conversation_records
from amocrm_service.repository import Repository, utc_now
from amocrm_service.tenancy import load_account_settings, save_account_settings


class ConversationAutomationService:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository

    def run(self, *, limit: int = 25, dry_run: bool = False) -> dict[str, Any]:
        raw_account_settings = load_account_settings(
            user_key=self.settings.user_key,
            account_key=self.settings.account_key,
            data_root=self.settings.data_root,
        )
        config = conversation_settings(raw_account_settings)
        if not config["enabled"]:
            return {"ok": True, "enabled": False, "message": "conversation automation is disabled"}

        filters = config["filters"]
        actions = config["actions"]
        if filters.get("new_calls_only") and not int(filters.get("started_at") or 0):
            started_at = int(time.time())
            if dry_run:
                filters = dict(filters)
                filters["started_at"] = started_at
            else:
                next_settings = dict(raw_account_settings or {})
                ci = dict(next_settings.get("conversation_intelligence") or {})
                ci_filters = dict((ci.get("filters") or {}))
                ci_filters["new_calls_only"] = True
                ci_filters["started_at"] = started_at
                ci["filters"] = ci_filters
                next_settings["conversation_intelligence"] = ci
                save_account_settings(
                    user_key=self.settings.user_key,
                    account_key=self.settings.account_key,
                    settings=next_settings,
                    data_root=self.settings.data_root,
                )
                return {
                    "ok": True,
                    "enabled": True,
                    "dry_run": False,
                    "baseline_set": True,
                    "started_at": started_at,
                    "eligible_conversations": 0,
                    "message": "New calls baseline was set; historical calls were skipped.",
                    "steps": {},
                }
        imported = {"leads": 0, "lead_notes": 0, "contact_notes": 0, "conversation_records": 0}
        polled = {
            "events": 0,
            "call_events": 0,
            "contacts": 0,
            "lead_ids": [],
            "note_ids": [],
            "would_be_eligible_conversations": 0,
            "would_be_conversation_ids": [],
            "imported": imported,
        }
        if actions.get("import_leads"):
            try:
                polled = self._poll_recent_call_events(filters, limit=max(limit * 5, 100), dry_run=dry_run)
                imported = dict(polled.get("imported") or imported)
            except Exception as exc:
                polled["error"] = str(exc)
                polled["imported"] = imported

        lead_ids = self._candidate_lead_ids(filters, limit=limit)
        for lead_id in polled.get("lead_ids") or []:
            if str(lead_id) not in lead_ids:
                lead_ids.append(str(lead_id))

        already_imported_lead_ids = {str(item) for item in (polled.get("lead_ids") or [])}
        lead_ids_to_import = [lead_id for lead_id in lead_ids if str(lead_id) not in already_imported_lead_ids]
        if actions.get("import_leads") and not dry_run and lead_ids_to_import:
            client = AmoCRMClient(self.settings)
            pipeline = ConversationPipeline(self.repository)
            try:
                for lead_id in lead_ids_to_import:
                    result = pipeline.import_lead_context(self.settings.account_key, client, int(lead_id))
                    for key, value in result.items():
                        imported[key] = imported.get(key, 0) + int(value or 0)
            finally:
                client.close()

        records = self._eligible_records(filters, actions, limit=max(limit * 5, 100))
        conversation_ids = {str(record["conversation_id"]) for record in records[:limit]}
        result: dict[str, Any] = {
            "ok": True,
            "enabled": True,
            "dry_run": dry_run,
            "filters": filters,
            "lead_candidates": len(lead_ids),
            "eligible_conversations": len(conversation_ids),
            "conversation_ids": sorted(conversation_ids),
            "polled": polled,
            "imported": imported,
            "steps": {},
        }
        if dry_run or not conversation_ids:
            return result

        if actions.get("probe_recordings"):
            result["steps"]["probe_recordings"] = ConversationAudioService(self.repository).probe_recordings(
                self.settings.account_key,
                limit=max(limit * 5, 100),
                conversation_ids=conversation_ids,
            )
        if actions.get("download_recordings"):
            result["steps"]["download_recordings"] = ConversationAudioService(self.repository).download_accessible_recordings(
                self.settings.account_key,
                self.settings.workspace_dir / "recordings",
                limit=max(limit * 5, 100),
                conversation_ids=conversation_ids,
            )
        if actions.get("transcribe"):
            try:
                result["steps"]["transcribe"] = build_transcription_service(self.repository).transcribe_downloaded(
                    self.settings.account_key,
                    limit=max(limit * 5, 100),
                    conversation_ids=conversation_ids,
                )
            except Exception as exc:
                result["steps"]["transcribe"] = {"ok": False, "error": str(exc)}
        if actions.get("analyze"):
            result["steps"]["analyze"] = ConversationPipeline(self.repository).analyze_transcribed(
                self.settings.account_key,
                limit=max(limit * 5, 100),
                conversation_ids=conversation_ids,
                analysis_config=config,
            )
        if actions.get("post_note"):
            result["steps"]["post_note"] = self._post_notes(conversation_ids)
        if actions.get("export_google_sheets"):
            output_path = self.settings.workspace_dir / "exports" / "conversation_analysis.csv"
            result["steps"]["export_google_sheets"] = ConversationExportService(self.repository).export_csv(
                self.settings.account_key,
                output_path,
            )
        return result

    def _poll_recent_call_events(self, filters: dict[str, Any], *, limit: int, dry_run: bool) -> dict[str, Any]:
        started_at = int(filters.get("started_at") or 0)
        if filters.get("new_calls_only") and not started_at:
            return {
                "events": 0,
                "call_events": 0,
                "contacts": 0,
                "lead_ids": [],
                "note_ids": [],
                "would_be_eligible_conversations": 0,
                "would_be_conversation_ids": [],
                "imported": {"leads": 0, "lead_notes": 0, "contact_notes": 0, "conversation_records": 0},
            }

        client = AmoCRMClient(self.settings)
        pipeline = ConversationPipeline(self.repository)
        imported = {"leads": 0, "lead_notes": 0, "contact_notes": 0, "conversation_records": 0}
        contact_ids: set[int] = set()
        lead_ids: set[int] = set()
        note_ids: set[str] = set()
        note_event_times: dict[str, int] = {}
        would_be_conversation_ids: set[str] = set()
        events_count = 0
        call_events_count = 0
        try:
            events = client.get_recent_events(limit=limit)
            events_count = len(events)
            for event in events:
                created_at = int(event.get("created_at") or 0)
                if started_at and created_at < started_at:
                    continue
                event_type = str(event.get("type") or "")
                entity_type = str(event.get("entity_type") or "")
                if event_type not in {"incoming_call", "outgoing_call"} or entity_type != "contact":
                    continue
                call_events_count += 1
                contact_id = event.get("entity_id")
                if contact_id is not None:
                    contact_ids.add(int(contact_id))
                for value in event.get("value_after") or []:
                    note = (value or {}).get("note") or {}
                    note_id = note.get("id")
                    if note_id is not None:
                        note_id_text = str(note_id)
                        note_ids.add(note_id_text)
                        note_event_times[note_id_text] = created_at

            for contact_id in sorted(contact_ids):
                contact = client.get_contact_with_leads(contact_id)
                leads = (contact.get("_embedded") or {}).get("leads") or []
                contact_notes = [
                    note
                    for note in client.get_contact_notes_by_id(contact_id)
                    if str(note.get("id") or "") in note_ids
                ]
                for lead in leads:
                    lead_id = lead.get("id")
                    if lead_id is None:
                        continue
                    lead_ids.add(int(lead_id))
                    lead_payload = client.get_entity_by_id("leads", str(lead_id))
                    if not dry_run:
                        result = pipeline.import_lead_context(self.settings.account_key, client, int(lead_id))
                        for key, value in result.items():
                            imported[key] = imported.get(key, 0) + int(value or 0)
                    for note in contact_notes:
                        occurred_at = int(note.get("created_at") or note_event_times.get(str(note.get("id") or ""), 0) or 0)
                        lead_at_call = self._lead_snapshot_at(client, int(lead_id), occurred_at, lead_payload)
                        for record in extract_conversation_records(self.settings.account_key, note, "contact_notes"):
                            linked_record = replace(
                                record,
                                lead_id=str(lead_id),
                                metadata={
                                    **record.metadata,
                                    "lead_at_call": lead_at_call,
                                },
                            )
                            if record_matches_filters(linked_record.__dict__, lead_payload, filters):
                                would_be_conversation_ids.add(linked_record.conversation_id)
                            if not dry_run:
                                imported["conversation_records"] += self.repository.upsert_conversation_records([linked_record])
                    if dry_run:
                        continue

                if not dry_run and not leads:
                    notes = [
                        note
                        for note in client.get_contact_notes_by_id(contact_id)
                        if str(note.get("note_type") or "") in CALL_NOTE_TYPES
                    ]
                    imported["contact_notes"] += self.repository.upsert_entities("contact_notes", notes)
        finally:
            client.close()

        return {
            "events": events_count,
            "call_events": call_events_count,
            "contacts": len(contact_ids),
            "lead_ids": sorted(str(item) for item in lead_ids),
            "note_ids": sorted(note_ids),
            "would_be_eligible_conversations": len(would_be_conversation_ids),
            "would_be_conversation_ids": sorted(would_be_conversation_ids),
            "imported": imported,
        }

    def _lead_snapshot_at(
        self,
        client: AmoCRMClient,
        lead_id: int,
        occurred_at: int,
        current_lead: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        lead = current_lead or client.get_entity_by_id("leads", str(lead_id))
        snapshot = {
            "id": int(lead_id),
            "pipeline_id": int(lead.get("pipeline_id") or 0),
            "status_id": int(lead.get("status_id") or 0),
            "responsible_user_id": int(lead.get("responsible_user_id") or 0),
            "snapshot_at": int(occurred_at or 0),
            "source": "events_rewind_v1",
        }
        if not occurred_at:
            return snapshot
        for event in client.get_lead_events(int(lead_id), limit=250):
            event_at = int(event.get("created_at") or 0)
            if event_at <= occurred_at:
                continue
            event_type = str(event.get("type") or "")
            before = event.get("value_before") or []
            if not before:
                continue
            before_payload = before[0] if isinstance(before[0], dict) else {}
            if event_type == "lead_status_changed":
                status = before_payload.get("lead_status") or {}
                if status.get("id") is not None:
                    snapshot["status_id"] = int(status.get("id") or 0)
                if status.get("pipeline_id") is not None:
                    snapshot["pipeline_id"] = int(status.get("pipeline_id") or 0)
            elif event_type == "entity_responsible_changed":
                responsible = before_payload.get("responsible_user") or {}
                if responsible.get("id") is not None:
                    snapshot["responsible_user_id"] = int(responsible.get("id") or 0)
        return snapshot

    def _candidate_lead_ids(self, filters: dict[str, Any], limit: int) -> list[str]:
        where = ["entity_type = 'leads'"]
        params: list[Any] = []
        if filters.get("pipeline_ids"):
            where.append(f"CAST(json_extract(payload_json, '$.pipeline_id') AS INTEGER) IN ({','.join('?' for _ in filters['pipeline_ids'])})")
            params.extend(filters["pipeline_ids"])
        if filters.get("status_ids"):
            where.append(f"CAST(json_extract(payload_json, '$.status_id') AS INTEGER) IN ({','.join('?' for _ in filters['status_ids'])})")
            params.extend(filters["status_ids"])
        if filters.get("responsible_user_ids"):
            where.append(f"CAST(json_extract(payload_json, '$.responsible_user_id') AS INTEGER) IN ({','.join('?' for _ in filters['responsible_user_ids'])})")
            params.extend(filters["responsible_user_ids"])
        if filters.get("new_calls_only") and int(filters.get("started_at") or 0):
            where.append("COALESCE(updated_at, 0) >= ?")
            params.append(int(filters["started_at"]))
        rows = self.repository.conn.execute(
            f"""
            SELECT entity_id
            FROM raw_entities
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(updated_at, 0) DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        return [str(row["entity_id"]) for row in rows]

    def _eligible_records(self, filters: dict[str, Any], actions: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        records = self.repository.list_conversation_records(self.settings.account_key, limit=limit)
        analysis_ids = {
            str(item.get("conversation_id"))
            for item in self.repository.list_conversation_analyses(self.settings.account_key, limit=limit)
        }
        result = []
        for record in records:
            lead = None
            if record.get("lead_id"):
                lead_entity = self.repository.get_raw_entity("leads", str(record["lead_id"]))
                lead = (lead_entity or {}).get("payload") if lead_entity else None
            if record_matches_filters(record, lead, filters) and self._record_needs_action(record, analysis_ids, actions):
                result.append(record)
        return result

    def _record_needs_action(
        self,
        record: dict[str, Any],
        analysis_ids: set[str],
        actions: dict[str, Any],
    ) -> bool:
        status = str(record.get("status") or "")
        conversation_id = str(record.get("conversation_id") or "")
        metadata = record.get("metadata") or {}
        has_analysis = conversation_id in analysis_ids
        if actions.get("probe_recordings") and status in {"recording_found", "recording_unavailable"}:
            return True
        has_download = self._recording_download_exists(record)
        if actions.get("download_recordings") and (
            status in {"audio_accessible", "audio_download_failed"}
            or (status in {"audio_downloaded", "transcription_failed"} and not has_download)
        ):
            return True
        if actions.get("transcribe") and status in {"recording_found", "audio_accessible", "audio_downloaded", "transcription_failed"} and has_download:
            return True
        if actions.get("analyze") and status == "transcribed" and not has_analysis:
            return True
        if (
            actions.get("post_note")
            and has_analysis
            and record.get("lead_id")
            and not metadata.get("last_posted_note_id")
            and not metadata.get("last_post_note_error")
        ):
            return True
        return False

    def _recording_download_exists(self, record: dict[str, Any]) -> bool:
        download = (record.get("metadata") or {}).get("recording_download") or {}
        raw_path = str(download.get("path") or "").strip()
        return bool(raw_path and Path(raw_path).is_file())

    def _post_notes(self, conversation_ids: set[str]) -> dict[str, Any]:
        records = self.repository.list_conversation_records(self.settings.account_key, limit=500)
        analyses = self.repository.list_conversation_analyses(self.settings.account_key, limit=500)
        client = AmoCRMClient(self.settings)
        posted = []
        failed = []
        try:
            for conversation_id in sorted(conversation_ids):
                try:
                    record, analysis = find_record_and_analysis(records, analyses, conversation_id=conversation_id)
                    metadata = record.get("metadata") or {}
                    if metadata.get("last_posted_note_id"):
                        continue
                    lead_id = record.get("lead_id")
                    if not lead_id:
                        continue
                    note = client.add_lead_note(int(lead_id), build_lead_analysis_note(record, analysis))
                    note_id = str(note.get("id") or "")
                    self.repository.update_conversation_record_status(
                        self.settings.account_key,
                        conversation_id,
                        status=str(record.get("status") or "transcribed"),
                        metadata_patch={
                            "last_posted_note_id": note_id,
                            "last_posted_lead_id": str(lead_id),
                            "last_posted_at": utc_now(),
                        },
                    )
                    posted.append({"conversation_id": conversation_id, "lead_id": lead_id, "note_id": note_id})
                except Exception as exc:
                    try:
                        self.repository.update_conversation_record_status(
                            self.settings.account_key,
                            conversation_id,
                            status=str((record if "record" in locals() else {}).get("status") or "transcribed"),
                            metadata_patch={
                                "last_post_note_error": str(exc),
                                "last_post_note_failed_at": utc_now(),
                            },
                        )
                    except Exception:
                        pass
                    failed.append({"conversation_id": conversation_id, "error": str(exc)})
        finally:
            client.close()
        return {"posted": len(posted), "failed": len(failed), "items": posted, "errors": failed}
