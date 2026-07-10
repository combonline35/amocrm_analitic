from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import httpx

from amocrm_service.config import load_settings
from amocrm_service.conversations import (
    _call_analysis_v2_to_analysis,
    _parse_json_object,
    build_call_prompt,
    parse_call_analysis_v2,
)
from amocrm_service.db import connect
from amocrm_service.repository import utc_now


class ChecklistPromptRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_call_checklist_steps(self, account_key: str, active: bool = True) -> list[dict[str, Any]]:
        query = """
            SELECT id, account_key, slug, label, hint, order_index, active, created_at, updated_at
            FROM call_checklist_step
            WHERE account_key = ?
        """
        params: list[Any] = [account_key]
        if active:
            query += " AND active = 1"
        query += " ORDER BY order_index, id"
        try:
            return [dict(row) for row in self.conn.execute(query, params).fetchall()]
        except sqlite3.OperationalError as exc:
            if "call_checklist_step" in str(exc):
                return []
            raise


def load_record(conn: sqlite3.Connection, account_key: str, call_id: str) -> dict[str, Any] | None:
    rows = conn.execute(
        """
        SELECT *
        FROM conversation_records
        WHERE account_key = ?
          AND (
            conversation_id = ?
            OR source_id = ?
            OR conversation_id = 'contact_notes:' || ?
            OR conversation_id = 'lead_notes:' || ?
          )
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (account_key, call_id, call_id, call_id, call_id),
    ).fetchall()
    if not rows:
        return None
    item = dict(rows[0])
    try:
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    except json.JSONDecodeError:
        item["metadata"] = {}
    return item


def post_openrouter_json(*, api_key: str, model: str, prompt: str, record: dict[str, Any]) -> dict[str, Any]:
    public_context = {
        "direction": record.get("direction") or "unknown",
        "duration_seconds": int(record.get("duration_seconds") or 0),
        "product": "натяжные потолки",
        "privacy": "raw transcript is sent to external LLM",
    }
    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:8018",
            "X-Title": "amoCRM call QA",
        },
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a strict sales QA evaluator. Return only a valid JSON object. "
                        "Do not include markdown. The transcript can be raw or anonymized; do not infer hidden personal data."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Public non-sensitive context:\n{json.dumps(public_context, ensure_ascii=False)}\n\n"
                        f"{prompt}"
                    ),
                },
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    content = str(payload["choices"][0]["message"]["content"]).strip()
    return _parse_json_object(content)


def upsert_analysis(conn: sqlite3.Connection, analysis: Any) -> None:
    now = utc_now()
    conn.execute(
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
        (
            analysis.account_key,
            analysis.conversation_id,
            analysis.summary,
            analysis.sentiment,
            int(analysis.score or 0),
            analysis.next_step,
            json.dumps(analysis.objections or [], ensure_ascii=False, separators=(",", ":")),
            json.dumps(analysis.recommendations or [], ensure_ascii=False, separators=(",", ":")),
            json.dumps(analysis.metrics or {}, ensure_ascii=False, separators=(",", ":")),
            json.dumps(analysis.analysis or {}, ensure_ascii=False, separators=(",", ":")),
            now,
            now,
        ),
    )
    conn.commit()


def print_prompt_preview(prompt: str, lines: int = 40) -> None:
    prompt_lines = prompt.splitlines()
    print("\n=== PROMPT PREVIEW ===")
    print("\n".join(prompt_lines[:lines]))
    if len(prompt_lines) > lines:
        print(f"... ({len(prompt_lines) - lines} строк скрыто)")


def print_result(parsed: dict[str, Any], metrics: dict[str, Any], snapshot: dict[str, Any]) -> None:
    print("\n=== RESULT ===")
    print(f"outcome: {parsed.get('outcome') or 'не_применимо'}")
    print(f"refusal_reason: {parsed.get('refusal_reason') or ''}")
    print(f"quality_display: {metrics.get('quality_display') or '—'}")
    print(f"summary: {parsed.get('summary') or ''}")
    print(f"next_step: {parsed.get('next_step') or ''}")
    print(f"coach_tip: {parsed.get('coach_tip') or ''}")

    print("\n=== STEPS ===")
    steps = parsed.get("steps") if isinstance(parsed.get("steps"), dict) else {}
    for step in snapshot.get("steps") or []:
        if not isinstance(step, dict):
            continue
        slug = str(step.get("slug") or "").strip()
        if not slug:
            continue
        item = steps.get(slug) if isinstance(steps.get(slug), dict) else {}
        marker = "ok" if item.get("ok") is True else "✗"
        quote = str(item.get("quote") or "").strip()
        print(f"{slug:16} {marker:2} {quote}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Recalculate one saved call transcript through call analysis v2.")
    parser.add_argument("account_key", help="Account key, for example donpotolok")
    parser.add_argument("call_id", help="conversation_id like contact_notes:291350895 or raw note/call id")
    parser.add_argument("--user", default="default", help="User key for local account storage, default: default")
    parser.add_argument("--model", default=None)
    parser.add_argument("--write", action="store_true", help="Write v2 analysis into conversation_analysis")
    args = parser.parse_args()

    settings = load_settings(account_key=args.account_key, user_key=args.user)
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("OPENROUTER_API_KEY is not configured", file=sys.stderr)
        return 2
    model = str(args.model or os.getenv("OPENROUTER_ANALYSIS_MODEL") or "openai/gpt-4o-mini").strip()

    conn = connect(settings.db_path)
    try:
        record = load_record(conn, args.account_key, args.call_id)
        if not record:
            print(f"Звонок не найден: account={args.account_key}, id={args.call_id}", file=sys.stderr)
            return 1

        transcript = str(record.get("transcript_text") or "").strip()
        if not transcript:
            print("У звонка нет сохраненного transcript_text. Аудио заново не качаю и не транскрибирую.", file=sys.stderr)
            return 1

        prompt_repo = ChecklistPromptRepository(conn)
        if not prompt_repo.list_call_checklist_steps(args.account_key, active=True):
            print("чек-лист пуст, миграцию слайса 1 на боевую БД, видимо, не накатывали")
            return 2

        snapshot = build_call_prompt(args.account_key, prompt_repo)
        prompt = f"{snapshot['prompt']}\n\nТранскрибация:\n\"\"\"\n{transcript}\n\"\"\""
        print_prompt_preview(snapshot["prompt"])

        qa_json = post_openrouter_json(
            api_key=api_key,
            model=model,
            prompt=prompt,
            record=record,
        )
        parsed = parse_call_analysis_v2(qa_json, snapshot["steps"])
        analysis = _call_analysis_v2_to_analysis(
            record,
            transcript,
            qa_json,
            parsed,
            snapshot,
            source="openrouter_v2_qa",
            model=model,
        )
        print_result(parsed, analysis.metrics, snapshot)

        if args.write:
            upsert_analysis(conn, analysis)
            print("\nWRITE: conversation_analysis обновлён источником openrouter_v2_qa")
        else:
            print("\nDRY-RUN: БД не изменялась. Добавь --write, чтобы записать результат.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
