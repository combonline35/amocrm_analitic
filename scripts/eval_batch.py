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


TRANSCRIPT_PREVIEW_CHARS = 1200


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


def load_recent_records(conn: sqlite3.Connection, account_key: str, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM conversation_records
        WHERE account_key = ?
          AND status IN ('transcribed', 'analyzed')
          AND transcript_text IS NOT NULL
          AND TRIM(transcript_text) != ''
        ORDER BY occurred_at DESC
        LIMIT ?
        """,
        (account_key, limit),
    ).fetchall()
    records: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
        records.append(item)
    return records


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


def print_call_result(
    record: dict[str, Any],
    transcript: str,
    parsed: dict[str, Any],
    metrics: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    conversation_id = record.get("conversation_id") or "?"
    direction = record.get("direction") or "unknown"
    duration = int(record.get("duration_seconds") or 0)
    print(f"\n===== {conversation_id} | {direction} | {duration}s =====")

    preview = transcript[:TRANSCRIPT_PREVIEW_CHARS]
    print(f"--- ТРАНСКРИПТ (перв. ~{TRANSCRIPT_PREVIEW_CHARS} симв) ---")
    print(preview + ("…" if len(transcript) > TRANSCRIPT_PREVIEW_CHARS else ""))

    print("--- V2 ---")
    print(f"outcome: {parsed.get('outcome') or 'не_применимо'}   refusal_reason: {parsed.get('refusal_reason') or ''}")
    print("steps:")
    steps = parsed.get("steps") if isinstance(parsed.get("steps"), dict) else {}
    for step in snapshot.get("steps") or []:
        if not isinstance(step, dict):
            continue
        slug = str(step.get("slug") or "").strip()
        if not slug:
            continue
        item = steps.get(slug) if isinstance(steps.get(slug), dict) else {}
        ok = "T" if item.get("ok") is True else "F"
        quote = str(item.get("quote") or "").strip()
        print(f"  {slug:<12} ok={ok}  «{quote}»")
    quality_passed = int(metrics.get("quality_passed") or 0)
    quality_total = int(metrics.get("quality_total") or 0)
    print(f"quality: {quality_passed}/{quality_total}")
    print(f"summary: {parsed.get('summary') or ''}")
    print(f"next_step: {parsed.get('next_step') or ''}")
    print(f"coach_tip: {parsed.get('coach_tip') or ''}")


def print_batch_summary(results: list[dict[str, Any]]) -> None:
    print("\n===== СВОДКА =====")
    print(f"звонков в выборке: {len(results)}")
    print("outcome:")
    for outcome in ("записан", "перезвон", "отказ", "не_применимо"):
        count = sum(1 for item in results if item["outcome"] == outcome)
        print(f"  {outcome:<13} {count}")
    if results:
        avg_passed = sum(item["quality_passed"] for item in results) / len(results)
        quality_total = max(item["quality_total"] for item in results)
        print(f"средний quality: {avg_passed:.1f}/{quality_total}")
    else:
        print("средний quality: —")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run batch evaluation of recent transcribed calls through call analysis v2. Never writes to DB."
    )
    parser.add_argument("account_key", help="Account key, for example donpotolok")
    parser.add_argument("--limit", type=int, default=8, help="How many recent calls to evaluate, default: 8")
    parser.add_argument("--user", default="default", help="User key for local account storage, default: default")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    settings = load_settings(account_key=args.account_key, user_key=args.user)
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("OPENROUTER_API_KEY is not configured", file=sys.stderr)
        return 2
    model = str(args.model or os.getenv("OPENROUTER_ANALYSIS_MODEL") or "openai/gpt-4o-mini").strip()

    conn = connect(settings.db_path)
    try:
        prompt_repo = ChecklistPromptRepository(conn)
        if not prompt_repo.list_call_checklist_steps(args.account_key, active=True):
            print("чек-лист пуст, миграцию слайса 1 на боевую БД, видимо, не накатывали")
            return 2

        records = load_recent_records(conn, args.account_key, max(1, args.limit))
        if not records:
            print(f"Нет звонков с транскриптом: account={args.account_key}", file=sys.stderr)
            return 1

        snapshot = build_call_prompt(args.account_key, prompt_repo)
        print(f"DRY-RUN: {len(records)} звонков, model={model}, account={args.account_key}. БД не изменяется.")

        results: list[dict[str, Any]] = []
        for record in records:
            transcript = str(record.get("transcript_text") or "").strip()
            prompt = f"{snapshot['prompt']}\n\nТранскрибация:\n\"\"\"\n{transcript}\n\"\"\""
            try:
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
            except Exception as exc:
                print(f"\n===== {record.get('conversation_id') or '?'} | ОШИБКА =====")
                print(f"{type(exc).__name__}: {exc}")
                continue

            metrics = analysis.metrics or {}
            print_call_result(record, transcript, parsed, metrics, snapshot)
            results.append(
                {
                    "outcome": str(parsed.get("outcome") or "не_применимо"),
                    "quality_passed": int(metrics.get("quality_passed") or 0),
                    "quality_total": int(metrics.get("quality_total") or 0),
                }
            )

        print_batch_summary(results)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
