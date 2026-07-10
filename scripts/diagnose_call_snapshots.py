from __future__ import annotations

import argparse
import json
from dataclasses import replace

from amocrm_service.amocrm import AmoCRMClient
from amocrm_service.config import load_settings
from amocrm_service.conversation_automation import ConversationAutomationService
from amocrm_service.conversation_settings import conversation_settings, record_matches_filters
from amocrm_service.conversations import extract_conversation_records
from amocrm_service.db import connect
from amocrm_service.repository import Repository
from amocrm_service.tenancy import load_account_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="default")
    parser.add_argument("--account", default="donpotolok")
    parser.add_argument("--limit", type=int, default=125)
    args = parser.parse_args()

    settings = load_settings(user_key=args.user, account_key=args.account)
    account_settings = load_account_settings(
        user_key=settings.user_key,
        account_key=settings.account_key,
        data_root=settings.data_root,
    )
    filters = conversation_settings(account_settings)["filters"]
    service = ConversationAutomationService(settings, Repository(connect(settings.db_path)))
    client = AmoCRMClient(settings)
    rows = []
    try:
        for event in client.get_recent_events(limit=args.limit):
            event_at = int(event.get("created_at") or 0)
            if event_at < int(filters.get("started_at") or 0):
                continue
            if event.get("type") not in {"incoming_call", "outgoing_call"} or event.get("entity_type") != "contact":
                continue
            note_ids = {
                str(((value or {}).get("note") or {}).get("id") or "")
                for value in event.get("value_after") or []
            }
            contact_id = int(event.get("entity_id") or 0)
            if not contact_id:
                continue
            contact = client.get_contact_with_leads(contact_id)
            notes = [
                note
                for note in client.get_contact_notes_by_id(contact_id)
                if str(note.get("id") or "") in note_ids
            ]
            for lead_ref in (contact.get("_embedded") or {}).get("leads") or []:
                lead_id = int(lead_ref.get("id") or 0)
                if not lead_id:
                    continue
                lead = client.get_entity_by_id("leads", str(lead_id))
                for note in notes:
                    for record in extract_conversation_records(settings.account_key, note, "contact_notes"):
                        lead_at_call = service._lead_snapshot_at(client, lead_id, int(note.get("created_at") or event_at), lead)
                        linked_record = replace(
                            record,
                            lead_id=str(lead_id),
                            metadata={**record.metadata, "lead_at_call": lead_at_call},
                        )
                        rows.append({
                            "note_id": note.get("id"),
                            "lead_id": lead_id,
                            "duration": record.duration_seconds,
                            "current": {
                                "pipeline_id": lead.get("pipeline_id"),
                                "status_id": lead.get("status_id"),
                                "responsible_user_id": lead.get("responsible_user_id"),
                            },
                            "at_call": lead_at_call,
                            "passes": record_matches_filters(linked_record.__dict__, lead, filters),
                        })
    finally:
        client.close()
        service.repository.conn.close()
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
