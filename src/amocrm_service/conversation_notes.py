from __future__ import annotations

from typing import Any


def build_lead_analysis_note(record: dict[str, Any], analysis: dict[str, Any]) -> str:
    recommendations = analysis.get("recommendations") or []
    objections = analysis.get("objections") or []
    metrics = analysis.get("metrics") or {}
    analysis_payload = analysis.get("analysis") or {}
    qa_json = analysis_payload.get("qa_json") or {}
    report = metrics.get("report_metrics") or {}
    score_max = int(metrics.get("score_max") or analysis_payload.get("score_max") or 100)
    probability = qa_json.get("Вероятность продажи") or metrics.get("probability_percent")
    manager_recommendations = _string_list(qa_json.get("Рекомендации менеджеру"))
    rop_recommendations = _string_list(qa_json.get("Рекомендации РОП"))
    duration = record.get("duration_seconds") or metrics.get("duration_seconds") or 0

    lines = [
        "AI-анализ звонка",
        "",
        f"Звонок: {_direction_label(record.get('direction'))}, {_duration_label(duration)}.",
        f"Источник: {record.get('metadata', {}).get('source') or 'не указан'}",
        f"Оценка: {analysis.get('score', 0)}/{score_max}, тональность: {_sentiment_label(analysis.get('sentiment'))}",
    ]
    if probability:
        lines.append(f"Вероятность продажи: {probability}")
    if isinstance(report, dict) and report:
        outcome = report.get("outcome") if isinstance(report.get("outcome"), dict) else {}
        fit = report.get("offer_fit") if isinstance(report.get("offer_fit"), dict) else {}
        leadership = report.get("leadership") if isinstance(report.get("leadership"), dict) else {}
        lines.extend([
            f"Запись на замер: {outcome.get('booking') or 'не определено'}",
            f"Причина отказа: {outcome.get('refusal_reason') or 'не определено'}",
            f"Соответствие потребностям: {fit.get('fit') or 'не определено'}",
            f"Лидер разговора: {leadership.get('leader') or 'не определено'}",
        ])

    lines.extend([
        "",
        "Кратко:",
        str(analysis.get("summary") or "").strip(),
        "",
        "Следующий шаг:",
        str(analysis.get("next_step") or "Уточнить следующий контакт.").strip(),
    ])

    if objections:
        lines.extend(["", "Сигналы/возражения:", ", ".join(str(item) for item in objections)])
    if manager_recommendations:
        lines.append("")
        lines.append("Рекомендации менеджеру:")
        lines.extend(f"- {item}" for item in manager_recommendations)
    if rop_recommendations:
        lines.append("")
        lines.append("Рекомендации РОП:")
        lines.extend(f"- {item}" for item in rop_recommendations)
    if recommendations and not (manager_recommendations or rop_recommendations):
        lines.append("")
        lines.append("Рекомендации:")
        lines.extend(f"- {item}" for item in recommendations)
    if qa_json:
        explanation = str(qa_json.get("Объяснение оценки (вероятность продажи)") or "").strip()
        if explanation:
            lines.extend(["", "Почему такая оценка:", explanation])

    lines.extend([
        "",
        f"ID разговора: {record.get('conversation_id')}",
        "Сформировано автоматически, проверьте перед использованием в коммуникации с клиентом.",
    ])
    return "\n".join(lines).strip()


def _direction_label(value: Any) -> str:
    labels = {
        "incoming": "Входящий звонок",
        "outgoing": "Исходящий звонок",
        "inbound": "Входящий звонок",
        "outbound": "Исходящий звонок",
        "unknown": "Направление не указано",
    }
    text = str(value or "unknown").strip().lower()
    return labels.get(text, text.replace("_", " ").strip() or "Направление не указано")


def _sentiment_label(value: Any) -> str:
    labels = {
        "positive": "Позитивный",
        "neutral": "Нейтральный",
        "negative": "Негативный",
        "error": "Ошибка анализа",
        "unknown": "Не определено",
    }
    text = str(value or "unknown").strip().lower()
    return labels.get(text, text.replace("_", " ").strip() or "Не определено")


def _duration_label(value: Any) -> str:
    seconds = max(0, int(value or 0))
    minutes, rest = divmod(seconds, 60)
    if minutes and rest:
        return f"{minutes} мин {rest} сек"
    if minutes:
        return f"{minutes} мин"
    return f"{rest} сек"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    return [text] if text else []


def find_record_and_analysis(
    records: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    *,
    conversation_id: str | None = None,
    lead_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    indexed_records = {str(record.get("conversation_id")): record for record in records}
    for analysis in analyses:
        record = indexed_records.get(str(analysis.get("conversation_id")))
        if not record:
            continue
        if conversation_id and str(record.get("conversation_id")) != str(conversation_id):
            continue
        if lead_id and str(record.get("lead_id")) != str(lead_id):
            continue
        return record, analysis
    raise LookupError("No analyzed conversation matched the requested filters")
