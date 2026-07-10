from __future__ import annotations

import argparse
import datetime
import json

from amocrm_service.amocrm import AmoCRMClient
from amocrm_service.config import load_settings
from amocrm_service.db import connect
from amocrm_service.tenancy import load_account_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="default")
    parser.add_argument("--account", default="donpotolok")
    args = parser.parse_args()

    settings = load_settings(account_key=args.account, user_key=args.user)
    account_settings = load_account_settings(
        user_key=settings.user_key,
        account_key=settings.account_key,
        data_root=settings.data_root,
    )
    ci = account_settings.get("conversation_intelligence") or {}
    filters = ci.get("filters") or {}
    started_at = int(filters.get("started_at") or 0)
    print("settings")
    print(json.dumps({"filters": filters, "actions": ci.get("actions") or {}}, ensure_ascii=False, indent=2))
    print("started_at", started_at, datetime.datetime.fromtimestamp(started_at).isoformat() if started_at else "not set")

    conn = connect(settings.db_path)
    try:
        print("queue")
        print(json.dumps(
            [dict(row) for row in conn.execute("SELECT status, COUNT(*) AS count FROM sync_queue GROUP BY status")],
            ensure_ascii=False,
            indent=2,
        ))
        print("webhooks")
        print(json.dumps(
            [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT event_type, entity_type, entity_id, received_at, status
                    FROM webhook_events
                    ORDER BY received_at DESC
                    LIMIT 10
                    """
                )
            ],
            ensure_ascii=False,
            indent=2,
        ))
        print("local_contact_notes_since_started")
        print(json.dumps(
            [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        entity_id,
                        json_extract(payload_json, '$.note_type') AS note_type,
                        json_extract(payload_json, '$.params.source') AS source,
                        json_extract(payload_json, '$.params.duration') AS duration,
                        json_extract(payload_json, '$.entity_id') AS contact_id,
                        json_extract(payload_json, '$.created_at') AS created_at
                    FROM raw_entities
                    WHERE entity_type = 'contact_notes'
                      AND CAST(json_extract(payload_json, '$.created_at') AS INTEGER) >= ?
                    ORDER BY CAST(json_extract(payload_json, '$.created_at') AS INTEGER) DESC
                    LIMIT 20
                    """,
                    (started_at,),
                )
            ],
            ensure_ascii=False,
            indent=2,
        ))
    finally:
        conn.close()

    print("amocrm_contact_notes_since_started")
    client = AmoCRMClient(settings)
    try:
        data = client.get_v4(
            "/contacts/notes",
            {
                "filter[created_at][from]": started_at,
                "order[created_at]": "desc",
                "limit": 50,
            },
        )
        notes = (data.get("_embedded") or {}).get("notes") or []
        print(json.dumps(
            [
                {
                    "id": note.get("id"),
                    "entity_id": note.get("entity_id"),
                    "note_type": note.get("note_type"),
                    "created_at": note.get("created_at"),
                    "params": note.get("params"),
                }
                for note in notes[:20]
            ],
            ensure_ascii=False,
            indent=2,
        ))
    finally:
        client.close()


if __name__ == "__main__":
    main()
