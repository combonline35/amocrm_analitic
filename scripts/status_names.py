from __future__ import annotations

import json

from amocrm_service.config import load_settings
from amocrm_service.db import connect


def main() -> None:
    settings = load_settings(account_key="donpotolok", user_key="default")
    wanted = {
        55323254,
        55323258,
        55330934,
        55331190,
        143,
        55417882,
        55331206,
        142,
        55408282,
        55331202,
        35038084,
        76506694,
    }
    conn = connect(settings.db_path)
    try:
        rows = conn.execute(
            "SELECT payload_json FROM raw_entities WHERE entity_type = 'pipelines'"
        ).fetchall()
        result = []
        for row in rows:
            pipeline = json.loads(row["payload_json"] or "{}")
            for status in (pipeline.get("_embedded") or {}).get("statuses") or []:
                if int(status.get("id") or 0) in wanted:
                    result.append({
                        "pipeline_id": pipeline.get("id"),
                        "pipeline": pipeline.get("name"),
                        "status_id": status.get("id"),
                        "status": status.get("name"),
                    })
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
