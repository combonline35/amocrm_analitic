from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


EXPORT_HEADERS = [
    "account_key",
    "lead_id",
    "contact_id",
    "conversation_id",
    "occurred_at",
    "direction",
    "duration_seconds",
    "source",
    "status",
    "score",
    "sentiment",
    "summary",
    "next_step",
    "objections",
    "recommendations",
    "transcript",
    "posted_note_id",
]


class ConversationExportService:
    def __init__(self, repository: Any):
        self.repository = repository

    def export_csv(self, account_key: str, output_path: Path, limit: int = 1000) -> dict[str, Any]:
        rows = self.export_rows(account_key, limit=limit)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=EXPORT_HEADERS)
            writer.writeheader()
            writer.writerows(rows)
        return {
            "ok": True,
            "format": "csv",
            "path": str(output_path),
            "rows": len(rows),
        }

    def export_rows(self, account_key: str, limit: int = 1000) -> list[dict[str, Any]]:
        records = self.repository.list_conversation_records(account_key, limit=limit)
        analyses = self.repository.list_conversation_analyses(account_key, limit=limit)
        analysis_by_id = {str(item.get("conversation_id")): item for item in analyses}
        rows = []
        for record in records:
            analysis = analysis_by_id.get(str(record.get("conversation_id"))) or {}
            metadata = record.get("metadata") or {}
            rows.append({
                "account_key": account_key,
                "lead_id": record.get("lead_id") or "",
                "contact_id": record.get("contact_id") or "",
                "conversation_id": record.get("conversation_id") or "",
                "occurred_at": record.get("occurred_at") or "",
                "direction": record.get("direction") or "",
                "duration_seconds": record.get("duration_seconds") or "",
                "source": metadata.get("source") or "",
                "status": record.get("status") or "",
                "score": analysis.get("score") if analysis else "",
                "sentiment": analysis.get("sentiment") if analysis else "",
                "summary": analysis.get("summary") if analysis else "",
                "next_step": analysis.get("next_step") if analysis else "",
                "objections": ", ".join(str(item) for item in (analysis.get("objections") or [])),
                "recommendations": "\n".join(str(item) for item in (analysis.get("recommendations") or [])),
                "transcript": record.get("transcript_text") or "",
                "posted_note_id": metadata.get("last_posted_note_id") or "",
            })
        return rows
