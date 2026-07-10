from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any

import httpx


CALL_NOTE_TYPES = {"call_in", "call_out"}

CHECKLIST_MAX_SCORE = 147

CHECKLIST_ITEMS: list[dict[str, Any]] = [
    {"id": 1, "criterion": "Спросили имя и повторили 2 раза во время диалога", "max_score": 2, "critical_tag": "имя клиента"},
    {"id": 2, "criterion": "Все вопросы о замере: предложили 2 даты замера", "max_score": 5, "critical_tag": "закрытый вопрос"},
    {"id": 3, "criterion": "Первое предложение о замере при первом вопросе о цене: кратко, от 500 руб/м2, замерщик уточнит стоимость, выбор без выбора", "max_score": 7, "critical_tag": "душный разговор"},
    {"id": 4, "criterion": "После вопроса клиента менеджер отвечает кратким пояснением и сразу задает встречный вопрос", "max_score": 13, "critical_tag": "менеджер зависает"},
    {"id": 5, "criterion": "Если клиент несколько раз в одном звонке просит конкретный расчет, менеджер задает уточняющие параметры и минимум 4 раза предлагает замер в разных вариациях", "max_score": 47, "critical_tag": "нет много попыток"},
    {"id": 6, "criterion": "Менеджер использует не менее 30% слов и словосочетаний клиента", "max_score": 8, "critical_tag": "нет присоединения"},
    {"id": 7, "criterion": "Не говорить клиенту, что мы не считаем по телефону или у нас такая система", "max_score": 5, "critical_tag": "не согласны с клиентом"},
    {"id": 8, "criterion": "Есть вопрос: что смущает или останавливает от вызова замерщика?", "max_score": 3, "critical_tag": "рано сдались"},
    {"id": 9, "criterion": "Если клиент не спрашивает цену и не записался на замер, менеджер задает минимум 4 вопроса о замере в формате выбор без выбора", "max_score": 47, "critical_tag": "мало попыток"},
    {"id": 10, "criterion": "Менеджер задал больше вопросов чем клиент, инициатива у менеджера", "max_score": 10, "critical_tag": "потеря инициативы"},
]

CHECKLIST_PROMPT = """

ЧЕК-ЛИСТ AI ДЛЯ ОЦЕНКИ ЗВОНКА
Оцени разговор дополнительно по чек-листу ниже. Ставь частичные баллы, если критерий выполнен частично. Итог чек-листа имеет сырой максимум 147, но итоговая оценка для UI нормализуется до 100.

Ветвление:
- Правила 5 и 9 взаимоисключающие.
- Если клиент в рамках одного звонка несколько раз просит цену/расчет ("сколько стоит", "рассчитайте", "так сколько получится", "сделайте расчет"), оценивай правило 5.
- Если клиент не спрашивал цену/расчет и не записался на замер, оценивай правило 9.
- Если клиент спрашивал цену/расчет, правило 9 ставь 0 и объясни "не применялось, сработала ветка правила 5".
- Если клиент не спрашивал цену/расчет, правило 5 ставь 0 и объясни "не применялось, сработала ветка правила 9 или запись на замер".

Правило закрытых вопросов:
- Если менеджер предложил замер через хороший "выбор без выбора" с двумя вариантами даты/времени, давай до 100% балла пункта.
- Если попытка была, но вопрос закрытый формата "удобно?", "сможете?", "записать?", на который можно ответить да/нет, давай примерно 30% максимума пункта.
- Если попытки нет, ставь 0.

Паузы:
- Если нет таймкодов, не измеряй реальные паузы. Оценивай "менеджер зависает" только по тексту: менеджер ответил и не задал встречный вопрос, потерял инициативу, не продвинул к замеру.

Правило 5 разбей внутри объяснения на подпункты: профиль 80/110/70/112, периметр, люстра/закладная/провод, светильники/вклейка/диаметр, гардина, полотно/микроны/оттенок, минимум 4 подхода с предложением замера. Не обязательно должны быть все параметры; важно минимум 4 разных подхода или похожих уточняющих параметра.

Правило 6:
- Сравни слова и словосочетания клиента со словами менеджера. Считай от всех смысловых слов клиента. Если менеджер явно перефразирует/использует около 30% клиентских слов/смыслов, ставь высокий балл.

Верни в JSON дополнительные ключи:
"Чек-лист AI": [
  {"id":1,"Критерий":"...","Балл":0,"Максимум":2,"Тег ошибки":"имя клиента","Статус":"выполнено/частично/не выполнено/не применялось","Доказательство":"короткая цитата или не определено","Комментарий":"почему такой балл"}
],
"ИТОГО по чек-листу (из 147)": 0,
"ИТОГО нормализовано (из 100)": 0,
"Критичные ошибки": ["теги ошибок, которые реально проявились"]

Список критериев:
1. 2 балла - Спросили имя и повторили 2 раза во время диалога. Тег: имя клиента.
2. 5 баллов - Все вопросы о замере: предложили 2 даты замера. Тег: закрытый вопрос.
3. 7 баллов - Первое предложение о замере при первом вопросе о цене: "потолок стоит от 500 руб. за квадратный метр", пояснение про выезд замерщика не более 11 слов, предложение даты замера "выбор без выбора". Тег: душный разговор.
4. 13 баллов - После вопроса клиента менеджер отвечает: краткое пояснение + вопрос, без зависания. Тег: менеджер зависает.
5. 47 баллов - При повторных просьбах клиента о конкретном расчете менеджер задает уточняющие технические параметры и в момент растерянности предлагает замерщика, минимум 4 подхода. Тег: нет много попыток.
6. 8 баллов - Менеджер использует не менее 30% слов/словосочетаний клиента. Тег: нет присоединения.
7. 5 баллов - Не говорить клиенту "мы не считаем по телефону" или "у нас такая система". Тег: не согласны с клиентом.
8. 3 балла - Есть вопрос "что смущает/останавливает от вызова замерщика?". Тег: рано сдались.
9. 47 баллов - Если не записали на замер и клиент не спрашивал цену, должно быть минимум 4 вопроса о замере в формате "выбор без выбора". Тег: мало попыток.
10. 10 баллов - Менеджер задал больше вопросов чем клиент, инициатива у менеджера. Тег: потеря инициативы.
"""

REPORT_METRICS_PROMPT = """

ДОПОЛНИТЕЛЬНЫЕ ПОКАЗАТЕЛИ ИЗ ОТЧЕТА
Сохрани основной чек-лист как главный источник итогового балла. Дополнительно верни управленческие показатели, которые помогают РОПу и менеджеру понять сделку и качество разговора.

Верни в JSON дополнительный ключ "Показатели отчета":
{
  "Исход звонка": {
    "Запись на замер": "да/нет/не применимо",
    "Причина отказа": "если замера нет - конкретная причина, если замер есть - клиент не отказался",
    "Следующий шаг": "конкретное действие",
    "Вероятность продажи": "__%",
    "Объяснение вероятности": "почему такая вероятность"
  },
  "Профиль клиента": {
    "ЛПР": "да/нет/неясно + короткое обоснование",
    "Факты": ["факты из разговора"],
    "Потребности": ["что клиенту нужно"],
    "Боли": ["что беспокоит клиента"],
    "Возражения": ["сомнения/возражения клиента"]
  },
  "Попадание в потребности": {
    "Предложение соответствует потребностям": "да/частично/нет",
    "Потребности закрыли": ["что менеджер закрыл"],
    "Потребности НЕ закрыли": ["что менеджер не закрыл"]
  },
  "Навыки менеджера": {
    "Установление контакта": {"percent": 0, "explanation": "почему"},
    "Выявление потребностей": {"percent": 0, "explanation": "почему"},
    "Усиление боли": {"percent": 0, "explanation": "почему"},
    "Презентация": {"percent": 0, "explanation": "почему"},
    "Отработка возражений": {"percent": 0, "explanation": "почему"}
  },
  "Лидерство в разговоре": {
    "Кто лидер": "менеджер/клиент/на равных/неясно",
    "Почему": "кто вел разговор и за счет чего"
  }
}

Правила оценки:
- "Запись на замер" ставь "да", только если в разговоре реально согласовали замер/дату/окно/следующий контакт замерщика. Если это контроль после замера, ставь "не применимо" и объясняй в причине.
- "Причина отказа" не должна быть общей фразой. Пиши конкретно: цена, хочет расчет без замера, думает, нет времени, выбрал конкурента, не ЛПР, район не обслуживается, контроль после замера и т.д.
- "Предложение соответствует потребностям" оценивай по тому, связал ли менеджер свое предложение с тем, что сказал клиент.
- Проценты навыков ставь 0-100. Объяснения должны быть короткими и прикладными, как в таблице: что сделал хорошо и чего не хватило.
- "Кто лидер" важен для РОПа: менеджер лидер, если он задает вопросы, ведет к замеру/следующему шагу и не просто отвечает клиенту.
"""


@dataclass(frozen=True)
class ConversationRecord:
    account_key: str
    conversation_id: str
    source_type: str
    source_id: str
    lead_id: str | None
    contact_id: str | None
    direction: str
    kind: str
    recording_url: str | None
    transcript_text: str | None
    duration_seconds: int | None
    occurred_at: int | None
    status: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ConversationAnalysis:
    account_key: str
    conversation_id: str
    summary: str
    sentiment: str
    score: int
    next_step: str | None
    objections: list[str]
    recommendations: list[str]
    metrics: dict[str, Any]
    analysis: dict[str, Any]


CALL_ANALYSIS_V2_OUTCOMES = {"записан", "перезвон", "отказ", "не_применимо"}

CALL_ANALYSIS_V2_OUTCOME_STATUS = {
    "записан": {"sentiment": "positive", "color": "green", "conversion_excluded": False},
    "перезвон": {"sentiment": "neutral", "color": "yellow", "conversion_excluded": False},
    "отказ": {"sentiment": "negative", "color": "red", "conversion_excluded": False},
    "не_применимо": {"sentiment": "neutral", "color": "gray", "conversion_excluded": True},
}


def extract_conversation_records(
    account_key: str,
    note: dict[str, Any],
    source_type: str = "lead_notes",
) -> list[ConversationRecord]:
    note_type = str(note.get("note_type") or "")
    if note_type not in CALL_NOTE_TYPES:
        return []

    note_id = note.get("id")
    if note_id is None:
        return []

    params = note.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    recording_url = _first_string(
        params.get("link"),
        params.get("record_link"),
        params.get("recording_url"),
        params.get("url"),
    )
    transcript_text = _first_string(
        params.get("transcript"),
        params.get("transcription"),
        params.get("text"),
    )
    status = "transcribed" if transcript_text else "recording_found" if recording_url else "metadata_only"
    direction = "incoming" if note_type == "call_in" else "outgoing"
    source_id = str(note_id)
    lead_id = _first_string(note.get("entity_id"), note.get("lead_id")) if source_type == "lead_notes" else None
    contact_id = (
        _first_string(note.get("entity_id"), note.get("contact_id"), note.get("linked_talk_contact_id"))
        if source_type == "contact_notes"
        else _first_string(note.get("contact_id"), note.get("linked_talk_contact_id"))
    )

    metadata = {
        "note_type": note_type,
        "phone": params.get("phone"),
        "source": params.get("source"),
        "call_responsible": params.get("call_responsible"),
        "uniq": params.get("uniq"),
        "raw_params": params,
    }

    return [
        ConversationRecord(
            account_key=account_key,
            conversation_id=f"{source_type}:{source_id}",
            source_type=source_type,
            source_id=source_id,
            lead_id=lead_id,
            contact_id=contact_id,
            direction=direction,
            kind="call",
            recording_url=recording_url,
            transcript_text=transcript_text,
            duration_seconds=_safe_int(params.get("duration")),
            occurred_at=_safe_int(note.get("created_at")),
            status=status,
            metadata={key: value for key, value in metadata.items() if value not in (None, "", [])},
        )
    ]


class ConversationPipeline:
    def __init__(self, repository: Any):
        self.repository = repository

    def import_lead_context(self, account_key: str, client: Any, lead_id: int) -> dict[str, int]:
        lead = client.get_entity_by_id("leads", str(lead_id))
        contacts = (lead.get("_embedded") or {}).get("contacts") or []
        lead_notes = client.get_lead_notes_by_id(int(lead_id))
        contact_notes: list[dict[str, Any]] = []
        for contact in contacts:
            contact_id = contact.get("id")
            if contact_id is None:
                continue
            contact_notes.extend(client.get_contact_notes_by_id(int(contact_id)))

        saved_leads = self.repository.upsert_entities("leads", [lead])
        saved_lead_notes = self.repository.upsert_entities("lead_notes", lead_notes)
        saved_contact_notes = self.repository.upsert_entities("contact_notes", contact_notes)
        records = []
        for note in lead_notes:
            records.extend(extract_conversation_records(account_key, note, "lead_notes"))
        for note in contact_notes:
            for record in extract_conversation_records(account_key, note, "contact_notes"):
                records.append(replace(record, lead_id=str(lead_id)))
        saved_records = self.repository.upsert_conversation_records(records)
        return {
            "leads": saved_leads,
            "lead_notes": saved_lead_notes,
            "contact_notes": saved_contact_notes,
            "conversation_records": saved_records,
        }

    def discover_from_hub(self, account_key: str) -> dict[str, int]:
        records: list[ConversationRecord] = []
        for note in self.repository.all_payloads("lead_notes"):
            records.extend(extract_conversation_records(account_key, note, "lead_notes"))
        for note in self.repository.all_payloads("contact_notes"):
            records.extend(extract_conversation_records(account_key, note, "contact_notes"))
        return {"records": self.repository.upsert_conversation_records(records)}

    def analyze_transcribed(
        self,
        account_key: str,
        limit: int = 100,
        force: bool = False,
        conversation_ids: set[str] | None = None,
        analysis_config: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        analyzer = build_conversation_analyzer(analysis_config, repository=self.repository)
        stale_sources = _stale_analysis_sources_for_config(analysis_config) if not force else []
        records = self.repository.list_conversation_records(
            account_key,
            status="transcribed",
            without_analysis=not force and not stale_sources,
            stale_analysis_sources=stale_sources,
            limit=limit,
        )
        if conversation_ids is not None:
            records = [record for record in records if str(record.get("conversation_id")) in conversation_ids]
        analyses = []
        for record in records:
            transcript = str(record.get("transcript_text") or "").strip()
            if not transcript:
                continue
            analyses.append(analyzer.analyze(record, transcript))
        return {"analyses": self.repository.upsert_conversation_analyses(analyses)}


def _stale_analysis_sources_for_config(analysis_config: dict[str, Any] | None = None) -> list[str]:
    config = analysis_config or {}
    external = config.get("external_analysis") if isinstance(config.get("external_analysis"), dict) else {}
    mode = str((external or {}).get("mode") or "openrouter_raw").strip()
    if mode not in {"anonymized_openrouter", "openrouter_raw"}:
        return []
    return [
        "local_qa_schema_v1",
        "rule_based_v1",
        "openrouter_error_fallback",
    ]


class RuleBasedConversationAnalyzer:
    NEGATIVE_MARKERS = ("дорого", "не подходит", "конкурент", "подумаю", "нет бюджета", "сомнева")
    POSITIVE_MARKERS = ("готов", "оплат", "подходит", "берем", "соглас", "интересно")
    OBJECTION_MARKERS = {
        "price": ("дорого", "цена", "бюджет"),
        "timing": ("срок", "позже", "не сейчас"),
        "competitor": ("конкурент", "другие", "сравнить"),
        "decision_maker": ("директор", "руковод", "согласовать"),
    }
    APPOINTMENT_MARKERS = ("записала вас", "записали вас", "замер назнач", "мастер к вам", "с 10 до 11")

    def __init__(self, analysis_config: dict[str, Any] | None = None):
        self.analysis_config = analysis_config or {}

    def analyze(self, record: dict[str, Any], transcript: str) -> ConversationAnalysis:
        lowered = transcript.lower()
        positive = sum(1 for marker in self.POSITIVE_MARKERS if marker in lowered)
        negative = sum(1 for marker in self.NEGATIVE_MARKERS if marker in lowered)
        appointment_scheduled = any(marker in lowered for marker in self.APPOINTMENT_MARKERS)
        score = max(0, min(100, 55 + positive * 10 - negative * 12))
        if appointment_scheduled:
            score = max(score, 78)
        sentiment = "positive" if score >= 70 else "negative" if score < 45 else "neutral"
        objections = [
            key
            for key, markers in self.OBJECTION_MARKERS.items()
            if any(marker in lowered for marker in markers)
        ]
        if appointment_scheduled:
            objections = [item for item in objections if item not in {"timing", "competitor"}]
        recommendations = self._recommendations(objections, score)
        if appointment_scheduled:
            recommendations.insert(0, "Проверить, что замер стоит в графике, и передать мастеру адрес/окно визита.")
        summary = _compact_summary(transcript)
        next_step = "Зафиксировать следующий контакт и закрыть выявленные возражения."
        if appointment_scheduled:
            next_step = "Проконтролировать визит мастера и после замера довести клиента до договора."
        elif score >= 75:
            next_step = "Перевести сделку к конкретному коммерческому предложению или оплате."
        elif "decision_maker" in objections:
            next_step = "Выяснить ЛПР и договориться о контакте с ним."

        return ConversationAnalysis(
            account_key=str(record["account_key"]),
            conversation_id=str(record["conversation_id"]),
            summary=summary,
            sentiment=sentiment,
            score=score,
            next_step=next_step,
            objections=objections,
            recommendations=recommendations,
            metrics={
                "transcript_chars": len(transcript),
                "positive_markers": positive,
                "negative_markers": negative,
                "duration_seconds": record.get("duration_seconds"),
                "appointment_scheduled": appointment_scheduled,
                "scoring": self._score_breakdown(score, appointment_scheduled),
            },
            analysis={
                "source": "rule_based_v1",
                "analysis_prompt": self.analysis_config.get("analysis_prompt"),
                "scoring_config": self.analysis_config.get("scoring") or [],
                "record": {
                    key: record.get(key)
                    for key in ("lead_id", "direction", "kind", "recording_url", "occurred_at")
                },
            },
        )

    def _score_breakdown(self, total_score: int, appointment_scheduled: bool) -> dict[str, Any]:
        scoring = self.analysis_config.get("scoring") or []
        if not isinstance(scoring, list) or not scoring:
            return {}
        max_total = sum(int(item.get("max_score") or 0) for item in scoring if isinstance(item, dict)) or 100
        result = {}
        for item in scoring:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or item.get("label") or "").strip()
            if not key:
                continue
            max_score = int(item.get("max_score") or 0)
            value = round(max_score * total_score / max_total)
            if appointment_scheduled and key in {"next_step", "crm_hygiene"}:
                value = max(value, max_score)
            result[key] = {
                "label": str(item.get("label") or key),
                "score": min(max_score, value),
                "max_score": max_score,
            }
        return result

    def _recommendations(self, objections: list[str], score: int) -> list[str]:
        result = []
        if "price" in objections:
            result.append("Показать ценность и разложить стоимость через выгоды, а не скидку.")
        if "timing" in objections:
            result.append("Назначить точную дату следующего контакта и причину вернуться к вопросу.")
        if "competitor" in objections:
            result.append("Собрать критерии сравнения и отправить короткое отличие от конкурентов.")
        if "decision_maker" in objections:
            result.append("Попросить контакт ЛПР или встречу с участником принятия решения.")
        if score < 45:
            result.append("Провести повторный контакт с фокусом на ключевое возражение клиента.")
        if not result:
            result.append("Сохранить темп сделки и зафиксировать следующий конкретный шаг.")
        return result


def analysis_to_dict(analysis: ConversationAnalysis) -> dict[str, Any]:
    return asdict(analysis)


def build_call_prompt(account_key: str, repository: Any) -> dict[str, Any]:
    steps = [
        {
            "slug": str(item.get("slug") or "").strip(),
            "label": str(item.get("label") or "").strip(),
            "hint": str(item.get("hint") or "").strip(),
        }
        for item in repository.list_call_checklist_steps(account_key, active=True)
        if str(item.get("slug") or "").strip()
    ]
    if not steps:
        raise ValueError(f"active call checklist steps not found for account {account_key}")

    step_lines = "\n".join(
        f"- {step['slug']}: {step['label']}. {step['hint']}".rstrip()
        for step in steps
    )
    step_schema = ",\n".join(
        f'    "{step["slug"]}": {{"ok": false, "quote": ""}}'
        for step in steps
    )
    prompt = f"""РОЛЬ: специалист контроля качества продаж (натяжные потолки).
ЦЕЛЬ ЗВОНКА: записать клиента на бесплатный замер.
Оценивай ТОЛЬКО по фактам из транскрибации. Нет данных → ok:false, quote:"".

1. Определи outcome строго одно из: записан | перезвон | отказ | не_применимо.
   "записан" — только если реально согласован замер (дата/окно/визит замерщика).
   "не_применимо" — контроль после замера, не первичный звонок.
   При любом кроме "записан" заполни refusal_reason.
2. По каждому шагу поставь ok:true/false и приведи КОРОТКУЮ цитату-доказательство из разговора:
{step_lines}
3. Дай summary (1 строка), next_step (конкретное действие), coach_tip (что подтянуть менеджеру, 1 фраза).

ПРАВИЛА ЗАСЧИТЫВАНИЯ ШАГОВ:
- Цитата-доказательство должна быть репликой МЕНЕДЖЕРА (продавца), подтверждающей выполнение шага. Если доказательство — только слова клиента, шаг менеджером НЕ выполнен → ok:false.
- Ставь ok:true ТОЛЬКО при явном доказательстве в тексте. При сомнении ставь ok:false. Лучше недооценить, чем завысить.

Верни СТРОГО валидный JSON по схеме ниже. Без markdown, без общего балла, без процентов, без лишних ключей.
{{
  "outcome": "записан|перезвон|отказ|не_применимо",
  "refusal_reason": "",
  "steps": {{
{step_schema}
  }},
  "summary": "",
  "next_step": "",
  "coach_tip": ""
}}"""
    return {
        "version": datetime.now(timezone.utc).isoformat(),
        "steps": steps,
        "prompt": prompt,
    }


def parse_call_analysis_v2(qa_json: dict[str, Any], snapshot_steps: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(qa_json, dict):
        raise ValueError("call analysis v2 response must be a JSON object")

    outcome = str(qa_json.get("outcome") or "").strip()
    if outcome not in CALL_ANALYSIS_V2_OUTCOMES:
        print(f"call analysis v2 invalid outcome: {outcome!r}")
        outcome = "не_применимо"

    refusal_reason = str(qa_json.get("refusal_reason") or "").strip()
    if outcome == "записан":
        refusal_reason = ""

    raw_steps = qa_json.get("steps") if isinstance(qa_json.get("steps"), dict) else {}
    steps: dict[str, dict[str, Any]] = {}
    for step in snapshot_steps:
        slug = str(step.get("slug") or "").strip()
        if not slug:
            continue
        value = raw_steps.get(slug) if isinstance(raw_steps, dict) else {}
        value = value if isinstance(value, dict) else {}
        steps[slug] = {
            "ok": _truthy_bool(value.get("ok")),
            "quote": str(value.get("quote") or "").strip(),
        }

    return {
        "outcome": outcome,
        "refusal_reason": refusal_reason,
        "steps": steps,
        "summary": str(qa_json.get("summary") or "").strip(),
        "next_step": str(qa_json.get("next_step") or "").strip(),
        "coach_tip": str(qa_json.get("coach_tip") or "").strip(),
    }


def _truthy_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "да", "ok", "выполнено"}


def build_conversation_analyzer(analysis_config: dict[str, Any] | None = None, repository: Any | None = None) -> Any:
    config = analysis_config or {}
    external = config.get("external_analysis") if isinstance(config.get("external_analysis"), dict) else {}
    mode = str((external or {}).get("mode") or "openrouter_raw")
    if mode in {"anonymized_openrouter", "openrouter_raw"}:
        return OpenRouterConversationAnalyzer(config, anonymize=mode == "anonymized_openrouter", repository=repository)
    return QASchemaConversationAnalyzer(config)


class OpenRouterConversationAnalyzer:
    def __init__(
        self,
        analysis_config: dict[str, Any] | None = None,
        *,
        anonymize: bool = False,
        repository: Any | None = None,
    ):
        self.analysis_config = analysis_config or {}
        self.anonymize = anonymize
        self.repository = repository
        self.fallback = QASchemaConversationAnalyzer(analysis_config)
        external = self.analysis_config.get("external_analysis") or {}
        self.model = str(external.get("model") or os.getenv("OPENROUTER_ANALYSIS_MODEL") or "openai/gpt-4o-mini").strip()
        self.api_key = os.getenv("OPENROUTER_API_KEY", "").strip()

    def analyze(self, record: dict[str, Any], transcript: str) -> ConversationAnalysis:
        if not self.api_key:
            return self._error_analysis(record, transcript, "OPENROUTER_API_KEY is not configured")
        prepared = anonymize_transcript(transcript) if self.anonymize else {"text": transcript, "stats": {}}
        try:
            try:
                v2 = self._request_call_analysis_v2(record, prepared["text"])
                parsed_v2 = parse_call_analysis_v2(v2["qa_json"], v2["snapshot"]["steps"])
                return _call_analysis_v2_to_analysis(
                    record,
                    transcript,
                    v2["qa_json"],
                    parsed_v2,
                    v2["snapshot"],
                    source="openrouter_v2_qa",
                    model=self.model,
                    redaction=prepared if self.anonymize else None,
                )
            except Exception as v2_exc:
                qa_json = self._request_qa_json(record, prepared["text"])
                analysis = _qa_json_to_analysis(
                    record,
                    transcript,
                    qa_json,
                    source="openrouter_anonymized_qa_v1" if self.anonymize else "openrouter_raw_qa_v1",
                    model=self.model,
                    analysis_prompt=self.analysis_config.get("analysis_prompt"),
                    redaction=prepared if self.anonymize else None,
                )
                analysis.analysis["v2_fallback_error"] = str(v2_exc)
                return analysis
        except Exception as exc:
            return self._error_analysis(record, transcript, str(exc))

    def _error_analysis(self, record: dict[str, Any], transcript: str, error: str) -> ConversationAnalysis:
        local = self.fallback.analyze(record, transcript)
        local.analysis["external_analysis_error"] = error
        local.analysis["external_analysis_mode"] = "anonymized_openrouter" if self.anonymize else "openrouter_raw"
        local.analysis["source"] = "openrouter_error_fallback"
        if self.anonymize:
            local.analysis["redaction_stats"] = anonymize_transcript(transcript)["stats"]
        return replace(
            local,
            summary=f"LLM-анализ не выполнен: {error}",
            sentiment="error",
            score=0,
            next_step="Проверить настройки LLM и пересчитать анализ.",
            recommendations=["Проверить OPENROUTER_API_KEY, модель и доступность OpenRouter, затем пересчитать анализ."],
        )

    def _request_call_analysis_v2(self, record: dict[str, Any], transcript: str) -> dict[str, Any]:
        if self.repository is None:
            raise ValueError("repository is required for call analysis v2 prompt")
        snapshot = build_call_prompt(str(record["account_key"]), self.repository)
        prompt = f"{snapshot['prompt']}\n\nТранскрибация:\n\"\"\"\n{transcript}\n\"\"\""
        public_context = {
            "direction": record.get("direction") or "unknown",
            "duration_seconds": int(record.get("duration_seconds") or 0),
            "product": "натяжные потолки",
            "privacy": "raw transcript is sent to external LLM" if not self.anonymize else "transcript was anonymized before external analysis",
        }
        return {
            "qa_json": self._post_json_prompt(public_context, prompt),
            "snapshot": snapshot,
        }

    def _request_qa_json(self, record: dict[str, Any], transcript: str) -> dict[str, Any]:
        prompt = _safe_analysis_prompt(self.analysis_config.get("analysis_prompt"))
        if "Чек-лист AI" not in prompt:
            prompt = f"{prompt}\n{CHECKLIST_PROMPT}"
        if "Показатели отчета" not in prompt:
            prompt = f"{prompt}\n{REPORT_METRICS_PROMPT}"
        prompt = prompt.replace("{{ТРАНСКРИБАЦИЯ}}", transcript)
        prompt = prompt.replace("{{?????????????}}", transcript)
        if transcript not in prompt:
            prompt = f"{prompt}\n\nТранскрибация:\n\"\"\"\n{transcript}\n\"\"\""
        public_context = {
            "direction": record.get("direction") or "unknown",
            "duration_seconds": int(record.get("duration_seconds") or 0),
            "product": "натяжные потолки",
            "privacy": "raw transcript is sent to external LLM" if not self.anonymize else "transcript was anonymized before external analysis",
        }
        qa_json = self._post_json_prompt(public_context, prompt)
        if not _normalize_checklist(qa_json):
            qa_json.update(self._request_checklist_json(public_context, transcript))
        return qa_json

    def _request_checklist_json(self, public_context: dict[str, Any], transcript: str) -> dict[str, Any]:
        prompt = f"""
Верни строго валидный JSON без markdown только с ключами чек-листа:
{{
  "Чек-лист AI": [
    {{"id":1,"Критерий":"Спросили имя и повторили 2 раза во время диалога","Балл":0,"Максимум":2,"Тег ошибки":"имя клиента","Статус":"выполнено/частично/не выполнено/не применялось","Доказательство":"","Комментарий":""}}
  ],
  "ИТОГО по чек-листу (из 147)": 0,
  "ИТОГО нормализовано (из 100)": 0,
  "Критичные ошибки": []
}}

Оцени все 10 пунктов из инструкции ниже. В массиве "Чек-лист AI" обязательно должно быть 10 объектов с id 1-10.
{CHECKLIST_PROMPT}

Транскрибация:
\"\"\"
{transcript}
\"\"\"
"""
        return self._post_json_prompt(public_context, prompt)

    def _post_json_prompt(self, public_context: dict[str, Any], prompt: str) -> dict[str, Any]:
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:8018",
                "X-Title": "amoCRM call QA",
            },
            json={
                "model": self.model,
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


def _first_string(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _compact_summary(transcript: str) -> str:
    normalized = " ".join(transcript.split())
    if len(normalized) <= 240:
        return normalized
    return f"{normalized[:237].rstrip()}..."


def format_transcript_with_roles(transcript: str, direction: str | None = None) -> str:
    """Best-effort role formatting for transcripts that do not include diarization."""
    text = str(transcript or "").strip()
    if not text:
        return ""

    normalized_lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    if _has_role_labels(normalized_lines):
        return repair_role_transcript(
            "\n".join(_normalize_role_line(line) for line in normalized_lines),
            direction=direction,
        )

    chunks = _split_transcript_chunks(text)
    if not chunks:
        return text

    role = _first_dialog_role(chunks[0], direction=direction)
    lines = []
    for chunk in chunks:
        label = "Менеджер" if role == "manager" else "Клиент"
        lines.append(f"{label}: {chunk}")
        role = "client" if role == "manager" else "manager"
    return "\n".join(lines)


def repair_role_transcript(transcript: str, direction: str | None = None) -> str:
    lines = [line.strip() for line in str(transcript or "").splitlines() if line.strip()]
    if not lines or not _has_role_labels(lines):
        return str(transcript or "").strip()
    normalized = [_normalize_role_line(line) for line in lines]
    if not _looks_inverted_role_transcript(normalized, direction):
        return "\n".join(normalized)
    repaired = []
    for line in normalized:
        if line.startswith("Менеджер:"):
            repaired.append("Клиент:" + line[len("Менеджер:"):])
        elif line.startswith("Клиент:"):
            repaired.append("Менеджер:" + line[len("Клиент:"):])
        else:
            repaired.append(line)
    return "\n".join(repaired)


def _looks_inverted_role_transcript(lines: list[str], direction: str | None = None) -> bool:
    direction_value = str(direction or "").casefold()
    first = lines[0] if lines else ""
    if direction_value == "outgoing" and first.startswith("Менеджер:"):
        first_text = first.split(":", 1)[1].strip().casefold()
        if first_text in {"угу.", "угу", "алло.", "алло", "да.", "да", "слушаю.", "слушаю"}:
            return True

    manager_as_client = 0
    client_as_manager = 0
    manager_markers = (
        "компания",
        "оставляли заявку",
        "у нас на сайте",
        "сколько квадрат",
        "сколько потол",
        "планируете установить",
        "запиш",
        "замер",
    )
    client_markers = (
        "я не знаю",
        "не знаю",
        "хотела уточнить",
        "хотел уточнить",
        "мне нужно",
        "интересует",
        "подскажите",
    )
    for line in lines:
        lowered = line.casefold()
        if line.startswith("Клиент:") and any(marker in lowered for marker in manager_markers):
            manager_as_client += 1
        if line.startswith("Менеджер:") and any(marker in lowered for marker in client_markers):
            client_as_manager += 1
    return manager_as_client >= 2 or (manager_as_client >= 1 and client_as_manager >= 1)


def _has_role_labels(lines: list[str]) -> bool:
    return any(re.match(r"(?i)^\s*(менеджер|оператор|сотрудник|клиент|покупатель|абонент)\s*[:：-]", line) for line in lines)


def _normalize_role_line(line: str) -> str:
    match = re.match(r"(?i)^\s*(менеджер|оператор|сотрудник|клиент|покупатель|абонент)\s*[:：-]\s*(.*)$", line)
    if not match:
        return line
    raw_role = match.group(1).casefold()
    label = "Менеджер" if raw_role in {"менеджер", "оператор", "сотрудник"} else "Клиент"
    return f"{label}: {match.group(2).strip()}"


def _split_transcript_chunks(text: str) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"[\r\n]+", text) if item.strip()]
    if len(paragraphs) >= 2:
        return paragraphs
    sentences = re.split(r"(?<=[.!?。！？])\s+", " ".join(text.split()))
    chunks = [sentence.strip() for sentence in sentences if sentence.strip()]
    if len(chunks) <= 1:
        return chunks
    return chunks


def _first_dialog_role(first_chunk: str, direction: str | None = None) -> str:
    lowered = first_chunk.casefold()
    manager_markers = ("здравствуйте", "компания", "меня зовут", "слушаю", "добрый день", "чем могу")
    client_markers = ("хочу", "интересует", "подскажите", "сколько", "мне нужно", "заявк")
    if any(marker in lowered for marker in manager_markers):
        return "manager"
    if any(marker in lowered for marker in client_markers):
        return "client"
    direction_value = str(direction or "").casefold()
    if direction_value == "outgoing":
        return "client"
    if direction_value == "incoming":
        return "manager"
    return "manager"


class QASchemaConversationAnalyzer:
    """Local QA evaluator that stores analysis in the user's strict JSON-shaped schema."""

    NEED_MARKERS = ("замер", "потол", "площад", "квадрат", "комнат", "адрес", "дата", "время", "монтаж", "цена")
    PAIN_MARKERS = ("дорого", "срочно", "не знаю", "подума", "сомнева", "нет времени", "нужно быстрее")
    OBJECTION_MARKERS = ("дорого", "подума", "перезвон", "не сейчас", "сравн", "конкурент", "муж", "жена")
    APPOINTMENT_MARKERS = ("замер", "назнач", "запис", "к 10", "в 10", "завтра", "сегодня", "мастер")
    NAME_MARKERS = ("как вас зовут", "ваше имя", "обращаться")

    def __init__(self, analysis_config: dict[str, Any] | None = None):
        self.analysis_config = analysis_config or {}
        self.fallback = RuleBasedConversationAnalyzer(analysis_config)

    def analyze(self, record: dict[str, Any], transcript: str) -> ConversationAnalysis:
        base = self.fallback.analyze(record, transcript)
        qa_json = self._build_qa_json(record, transcript, base)
        score = _safe_score(qa_json.get("ИТОГО баллов (из 95)"), 95)
        probability = _percent_value(qa_json.get("Вероятность продажи"))
        return ConversationAnalysis(
            account_key=str(record["account_key"]),
            conversation_id=str(record["conversation_id"]),
            summary=str(qa_json.get("Итог разговора") or base.summary),
            sentiment="positive" if probability >= 70 else "negative" if probability < 40 else "neutral",
            score=score,
            next_step=str(qa_json.get("Следующий шаг") or base.next_step or ""),
            objections=_string_list(qa_json.get("Возражения")),
            recommendations=[
                item
                for item in [
                    str(qa_json.get("Как продать?") or "").strip(),
                    str(qa_json.get("Как улучшить") or "").strip(),
                    *_string_list(qa_json.get("Рекомендации менеджеру")),
                    *_string_list(qa_json.get("Рекомендации РОП")),
                ]
                if item
            ],
            metrics={
                "score_max": 95,
                "probability_percent": probability,
                "duration_seconds": record.get("duration_seconds"),
                "score_blocks": {
                    "Работа с именем": {"score": qa_json["Работа с именем (0-2)"], "max_score": 2},
                    "Техника записи на замер": {"score": qa_json["Техника записи на замер (0-7)"], "max_score": 7},
                    "Динамика разговора": {"score": qa_json["Динамика разговора (0-23)"], "max_score": 23},
                    "Стратегические техники": {"score": qa_json["Стратегические техники (0-55)"], "max_score": 55},
                    "Работа с возражениями": {"score": qa_json["Работа с возражениями (0-8)"], "max_score": 8},
                },
                "report_metrics": _normalize_report_metrics(qa_json),
                "percent_blocks": {
                    "Установление контакта": _percent_value(qa_json["Установление контакта (%)"]),
                    "Выявление потребностей": _percent_value(qa_json["Выявление потребностей (%)"]),
                    "Усиление боли": _percent_value(qa_json["Усиление боли (%)"]),
                    "Презентация": _percent_value(qa_json["Презентация (%)"]),
                    "Отработка возражений": _percent_value(qa_json["Отработка возражений (%)"]),
                },
                "transcript_chars": len(transcript),
            },
            analysis={
                "source": "local_qa_schema_v1",
                "score_max": 95,
                "qa_json": qa_json,
                "analysis_prompt": self.analysis_config.get("analysis_prompt"),
                "record": {
                    key: record.get(key)
                    for key in ("lead_id", "contact_id", "direction", "kind", "recording_url", "occurred_at")
                },
            },
        )

    def _build_qa_json(self, record: dict[str, Any], transcript: str, base: ConversationAnalysis) -> dict[str, Any]:
        role_transcript = format_transcript_with_roles(transcript, direction=str(record.get("direction") or ""))
        text = " ".join(transcript.split())
        lowered = text.lower()
        summary = str(base.summary or "").strip()
        if summary.casefold() in {"", "none", "null"}:
            summary = _compact_summary(transcript)
        has_appointment = any(marker in lowered for marker in self.APPOINTMENT_MARKERS)
        needs = _matched_phrases(text, self.NEED_MARKERS)
        pains = _matched_phrases(text, self.PAIN_MARKERS)
        objections = _matched_phrases(text, self.OBJECTION_MARKERS)
        name_score = 1 if any(marker in lowered for marker in self.NAME_MARKERS) else 0
        measurement_score = 2 if "замер" in lowered else 0
        if has_appointment:
            measurement_score += 3
        if any(marker in lowered for marker in ("адрес", "время", "завтра", "сегодня", "к 10", "в 10")):
            measurement_score += 2
        dynamic_score = 14 + (3 if int(record.get("duration_seconds") or 0) >= 30 else 0)
        strategy_score = 18 + min(12, len(needs) * 3) + (8 if has_appointment else 0) + (4 if pains else 0)
        objection_score = 0 if not objections else min(8, 2 + (2 if "?" in text else 0) + (2 if has_appointment else 0))
        total = min(95, name_score + min(7, measurement_score) + min(23, dynamic_score) + min(55, strategy_score) + objection_score)
        probability = min(95, max(15, round(total / 95 * 100)))
        next_step = "Проконтролировать назначенный замер и довести клиента до договора." if has_appointment else "Назначить конкретный следующий контакт и довести разговор до записи на замер."
        how_to_sell = "Зафиксировать договоренность, подтвердить адрес/время и следующий шаг." if has_appointment else "Вернуться к цели звонка: уточнить потребность, показать ценность замера и предложить конкретные слоты."
        improve = "Добавить больше уточняющих вопросов, проговаривать ценность замера и закрывать разговор конкретной договоренностью."
        if objections:
            improve += " Возражение нужно не только услышать, но и проверить, снято ли оно."
        manager_recommendations = _non_empty_list([
            "В следующем контакте начать с подтверждения потребности клиента и коротко проговорить ценность бесплатного замера.",
            "Задавать больше уточняющих вопросов: площадь, комнаты, сроки, кто принимает решение, что важно по цене/качеству.",
            "Фиксировать конкретный следующий шаг: дата, время, ответственный и что клиент должен подготовить.",
            "После возражения обязательно проверять, снято ли оно, а не переходить дальше автоматически." if objections else "",
        ])
        rop_recommendations = _non_empty_list([
            "Проверить, довел ли менеджер разговор до целевого действия: запись на замер или конкретная договоренность о следующем контакте.",
            "Разобрать с менеджером вопросы выявления потребности и фиксации ЛПР, если в звонке это не прозвучало.",
            "Контролировать повторный контакт по сделке и наличие задачи в CRM.",
            "Использовать этот звонок как материал для короткого коучинга по отработке возражений." if objections else "",
        ])
        return {
            "Итог разговора": summary,
            "Подробное резюме разговора": summary,
            "Следующий шаг": next_step,
            "Вероятность продажи": f"{probability}%",
            "Объяснение оценки (вероятность продажи)": "Оценка рассчитана локально по фактам транскрипта: наличие записи на замер, потребностей, возражений и следующего шага.",
            "Как продать?": how_to_sell,
            "Рекомендации менеджеру": manager_recommendations,
            "Рекомендации РОП": rop_recommendations,
            "ЛПР?": "не определено — в разговоре нет надежного подтверждения, что клиент принимает решение самостоятельно.",
            "Факты": _non_empty_list([
                f"Длительность звонка: {record.get('duration_seconds') or 0} сек.",
                f"Направление: {record.get('direction') or 'не определено'}.",
                "В разговоре упоминался замер." if "замер" in lowered else "",
            ]),
            "Потребности": needs or ["не определено"],
            "Боли": pains or ["не определено"],
            "Возражения": objections or ["не определено"],
            "Предложение соответствует потребностям": "частично — локальная оценка видит отдельные потребности, но без LLM не интерпретирует полный смысл диалога.",
            "Потребности закрыли": ["запись/обсуждение замера"] if has_appointment else ["не определено"],
            "Потребности НЕ закрыли": ["не определено"] if has_appointment else ["нет зафиксированной записи на замер"],
            "Установление контакта (%)": f"{60 + name_score * 20}%",
            "Объяснение (Установление контакта)": "Оценено по признакам приветствия и работы с именем в транскрипте.",
            "Выявление потребностей (%)": f"{min(100, len(needs) * 18)}%",
            "Объяснение (Выявление потребностей)": "Учитывались фактически упомянутые параметры: замер, площадь, адрес, сроки, цена и связанные детали.",
            "Усиление боли (%)": f"{min(100, len(pains) * 25)}%",
            "Объяснение (Усиление болей)": "Боли засчитываются только при явных маркерах в речи клиента или менеджера.",
            "Как улучшить": improve,
            "Презентация (%)": f"{75 if has_appointment else 35}%",
            "Объяснение оценки (Презентация)": "Выше оценивается разговор, где менеджер довел клиента до понятного следующего шага.",
            "Отработка возражений (%)": f"{round(objection_score / 8 * 100) if objections else 0}%",
            "Объяснение (Отработка возражений)": "Если возражений в транскрипте не найдено, этап не оценивается и получает 0.",
            "Кто лидер?": "менеджер" if has_appointment else "клиент",
            "Лидер, почему": "Менеджер довел разговор до следующего шага." if has_appointment else "В транскрипте не видно уверенного закрытия на конкретную договоренность.",
            "Работа с именем (0-2)": name_score,
            "Объяснение оценки (Работа с именем)": "Балл ставится только если в тексте есть явные признаки узнавания или использования имени.",
            "Техника записи на замер (0-7)": min(7, measurement_score),
            "Объяснение оценки (Техника записи на замер)": "Учитывается явное предложение замера, конкретика времени/адреса и финальная фиксация.",
            "Динамика разговора (0-23)": min(23, dynamic_score),
            "Объяснение оценки (Динамика разговора)": "Локально оценены длительность, наличие пауз по тексту и движение к следующему шагу.",
            "Стратегические техники (0-55)": min(55, strategy_score),
            "Объяснение оценки (Стратегические техники)": "Оценены выявленные потребности, боли, презентация и закрытие на действие.",
            "Работа с возражениями (0-8)": objection_score,
            "Объяснение оценки (Работа с возражениями)": "Оценивается только при явных возражениях в транскрипте.",
            "ИТОГО баллов (из 95)": total,
            "Транскрибация": role_transcript or text,
        }


def _safe_score(value: Any, maximum: int) -> int:
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value or ""))
    number = round(float(match.group(0).replace(",", "."))) if match else 0
    return max(0, min(maximum, number))


def _percent_value(value: Any) -> int:
    return _safe_score(value, 100)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _non_empty_list(values: list[str]) -> list[str]:
    return [value for value in values if value]


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and not value:
            continue
        return value
    return None


def _normalize_report_metrics(qa_json: dict[str, Any]) -> dict[str, Any]:
    report = qa_json.get("Показатели отчета") or qa_json.get("report_metrics") or {}
    report = report if isinstance(report, dict) else {}

    outcome = report.get("Исход звонка") if isinstance(report.get("Исход звонка"), dict) else {}
    client = report.get("Профиль клиента") if isinstance(report.get("Профиль клиента"), dict) else {}
    fit = report.get("Попадание в потребности") if isinstance(report.get("Попадание в потребности"), dict) else {}
    skills = report.get("Навыки менеджера") if isinstance(report.get("Навыки менеджера"), dict) else {}
    leadership = report.get("Лидерство в разговоре") if isinstance(report.get("Лидерство в разговоре"), dict) else {}

    def text(*values: Any, default: str = "не определено") -> str:
        value = _first_value(*values)
        return str(value).strip() if value is not None else default

    def items(*values: Any) -> list[str]:
        for value in values:
            found = _string_list(value)
            if found:
                return found
        return []

    def skill(label: str, percent_key: str, explanation_keys: tuple[str, ...]) -> dict[str, Any]:
        value = skills.get(label)
        value = value if isinstance(value, dict) else {}
        explanation = text(
            value.get("explanation"),
            value.get("Объяснение"),
            *(qa_json.get(key) for key in explanation_keys),
            default="",
        )
        return {
            "percent": _percent_value(_first_value(value.get("percent"), value.get("Процент"), qa_json.get(percent_key))),
            "explanation": explanation,
        }

    return {
        "outcome": {
            "booking": text(outcome.get("Запись на замер"), qa_json.get("Запись на замер")),
            "refusal_reason": text(outcome.get("Причина отказа"), qa_json.get("Причина отказа")),
            "next_step": text(outcome.get("Следующий шаг"), qa_json.get("Следующий шаг")),
            "probability": text(outcome.get("Вероятность продажи"), qa_json.get("Вероятность продажи")),
            "probability_explanation": text(
                outcome.get("Объяснение вероятности"),
                qa_json.get("Объяснение оценки (вероятность продажи)"),
                qa_json.get("Объяснение оценки"),
                default="",
            ),
        },
        "client_profile": {
            "decision_maker": text(client.get("ЛПР"), qa_json.get("ЛПР?")),
            "facts": items(client.get("Факты"), qa_json.get("Факты")),
            "needs": items(client.get("Потребности"), qa_json.get("Потребности")),
            "pains": items(client.get("Боли"), qa_json.get("Боли")),
            "objections": items(client.get("Возражения"), qa_json.get("Возражения")),
        },
        "offer_fit": {
            "fit": text(fit.get("Предложение соответствует потребностям"), qa_json.get("Предложение соответствует потребностям")),
            "closed_needs": items(fit.get("Потребности закрыли"), qa_json.get("Потребности закрыли")),
            "unclosed_needs": items(fit.get("Потребности НЕ закрыли"), qa_json.get("Потребности НЕ закрыли")),
        },
        "manager_skills": {
            "Установление контакта": skill("Установление контакта", "Установление контакта (%)", ("Объяснение (Установление контакта)",)),
            "Выявление потребностей": skill("Выявление потребностей", "Выявление потребностей (%)", ("Объяснение (Выявление потребностей)",)),
            "Усиление боли": skill("Усиление боли", "Усиление боли (%)", ("Объяснение (Усиление болей)",)),
            "Презентация": skill("Презентация", "Презентация (%)", ("Объяснение оценки (Презентация)", "Объяснение оценки")),
            "Отработка возражений": skill("Отработка возражений", "Отработка возражений (%)", ("Объяснение (Отработка возражений)", "Объяснение (Отработка возраежений)")),
        },
        "leadership": {
            "leader": text(leadership.get("Кто лидер"), qa_json.get("Кто лидер?")),
            "reason": text(leadership.get("Почему"), qa_json.get("Лидер, почему"), default=""),
        },
    }


def _normalize_checklist(qa_json: dict[str, Any]) -> dict[str, Any] | None:
    raw_items = qa_json.get("Чек-лист AI") or qa_json.get("checklist_ai") or qa_json.get("checklist")
    if not isinstance(raw_items, list) or not raw_items:
        return None
    by_id: dict[int, dict[str, Any]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            item_id = int(item.get("id") or item.get("№") or item.get("number") or 0)
        except (TypeError, ValueError):
            item_id = 0
        if item_id:
            by_id[item_id] = item

    normalized_items: list[dict[str, Any]] = []
    raw_score = 0
    for template in CHECKLIST_ITEMS:
        item = by_id.get(int(template["id"]), {})
        max_score = int(template["max_score"])
        score = _safe_score(
            item.get("Балл") or item.get("score") or item.get("score_awarded"),
            max_score,
        )
        raw_score += score
        normalized_items.append({
            "id": int(template["id"]),
            "criterion": str(item.get("Критерий") or item.get("criterion") or template["criterion"]),
            "score": score,
            "max_score": max_score,
            "critical_tag": str(item.get("Тег ошибки") or item.get("critical_tag") or template["critical_tag"]),
            "status": str(item.get("Статус") or item.get("status") or ("выполнено" if score >= max_score else "не выполнено")),
            "evidence": str(item.get("Доказательство") or item.get("evidence") or "").strip(),
            "comment": str(item.get("Комментарий") or item.get("comment") or "").strip(),
        })

    model_errors = _string_list(qa_json.get("Критичные ошибки") or qa_json.get("critical_errors"))
    generated_errors = [
        item["critical_tag"]
        for item in normalized_items
        if item["score"] < item["max_score"] and "не примен" not in item["status"].casefold()
    ]
    critical_errors = []
    for error in [*model_errors, *generated_errors]:
        if error and error not in critical_errors:
            critical_errors.append(error)

    normalized_score = round(raw_score / CHECKLIST_MAX_SCORE * 100) if CHECKLIST_MAX_SCORE else 0
    return {
        "items": normalized_items,
        "raw_score": raw_score,
        "max_score": CHECKLIST_MAX_SCORE,
        "normalized_score": max(0, min(100, normalized_score)),
        "critical_errors": critical_errors,
    }


def _matched_phrases(text: str, markers: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    result = []
    for marker in markers:
        if marker in lowered:
            result.append(marker)
    return result[:8]


QA_KEYS = {
    "summary": "\u0418\u0442\u043e\u0433 \u0440\u0430\u0437\u0433\u043e\u0432\u043e\u0440\u0430",
    "detailed_summary": "\u041f\u043e\u0434\u0440\u043e\u0431\u043d\u043e\u0435 \u0440\u0435\u0437\u044e\u043c\u0435 \u0440\u0430\u0437\u0433\u043e\u0432\u043e\u0440\u0430",
    "next_step": "\u0421\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0439 \u0448\u0430\u0433",
    "probability": "\u0412\u0435\u0440\u043e\u044f\u0442\u043d\u043e\u0441\u0442\u044c \u043f\u0440\u043e\u0434\u0430\u0436\u0438",
    "sell": "\u041a\u0430\u043a \u043f\u0440\u043e\u0434\u0430\u0442\u044c?",
    "improve": "\u041a\u0430\u043a \u0443\u043b\u0443\u0447\u0448\u0438\u0442\u044c",
    "manager_recommendations": "\u0420\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0430\u0446\u0438\u0438 \u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440\u0443",
    "rop_recommendations": "\u0420\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0430\u0446\u0438\u0438 \u0420\u041e\u041f",
    "facts": "\u0424\u0430\u043a\u0442\u044b",
    "needs": "\u041f\u043e\u0442\u0440\u0435\u0431\u043d\u043e\u0441\u0442\u0438",
    "pains": "\u0411\u043e\u043b\u0438",
    "objections": "\u0412\u043e\u0437\u0440\u0430\u0436\u0435\u043d\u0438\u044f",
    "name_score": "\u0420\u0430\u0431\u043e\u0442\u0430 \u0441 \u0438\u043c\u0435\u043d\u0435\u043c (0-2)",
    "booking_score": "\u0422\u0435\u0445\u043d\u0438\u043a\u0430 \u0437\u0430\u043f\u0438\u0441\u0438 \u043d\u0430 \u0437\u0430\u043c\u0435\u0440 (0-7)",
    "dynamics_score": "\u0414\u0438\u043d\u0430\u043c\u0438\u043a\u0430 \u0440\u0430\u0437\u0433\u043e\u0432\u043e\u0440\u0430 (0-23)",
    "strategy_score": "\u0421\u0442\u0440\u0430\u0442\u0435\u0433\u0438\u0447\u0435\u0441\u043a\u0438\u0435 \u0442\u0435\u0445\u043d\u0438\u043a\u0438 (0-55)",
    "objection_score": "\u0420\u0430\u0431\u043e\u0442\u0430 \u0441 \u0432\u043e\u0437\u0440\u0430\u0436\u0435\u043d\u0438\u044f\u043c\u0438 (0-8)",
    "total": "\u0418\u0422\u041e\u0413\u041e \u0431\u0430\u043b\u043b\u043e\u0432 (\u0438\u0437 95)",
    "contact_pct": "\u0423\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 \u043a\u043e\u043d\u0442\u0430\u043a\u0442\u0430 (%)",
    "needs_pct": "\u0412\u044b\u044f\u0432\u043b\u0435\u043d\u0438\u0435 \u043f\u043e\u0442\u0440\u0435\u0431\u043d\u043e\u0441\u0442\u0435\u0439 (%)",
    "pain_pct": "\u0423\u0441\u0438\u043b\u0435\u043d\u0438\u0435 \u0431\u043e\u043b\u0438 (%)",
    "presentation_pct": "\u041f\u0440\u0435\u0437\u0435\u043d\u0442\u0430\u0446\u0438\u044f (%)",
    "objection_pct": "\u041e\u0442\u0440\u0430\u0431\u043e\u0442\u043a\u0430 \u0432\u043e\u0437\u0440\u0430\u0436\u0435\u043d\u0438\u0439 (%)",
}


def _qa_json_to_analysis(
    record: dict[str, Any],
    transcript: str,
    qa_json: dict[str, Any],
    *,
    source: str,
    model: str | None = None,
    analysis_prompt: Any = None,
    redaction: dict[str, Any] | None = None,
) -> ConversationAnalysis:
    checklist = _normalize_checklist(qa_json)
    report_metrics = _normalize_report_metrics(qa_json)
    if checklist:
        score = int(checklist["normalized_score"])
        score_max = 100
        score_blocks = {
            "Чек-лист AI": {"score": int(checklist["raw_score"]), "max_score": int(checklist["max_score"])},
            "Итог 0-100": {"score": score, "max_score": 100},
            "Критичные ошибки": {"score": len(checklist["critical_errors"]), "max_score": len(CHECKLIST_ITEMS)},
        }
    else:
        score = _safe_score(_qa_get(qa_json, "total"), 95)
        score_max = 95
        score_blocks = {
            "\u0420\u0430\u0431\u043e\u0442\u0430 \u0441 \u0438\u043c\u0435\u043d\u0435\u043c": {"score": _safe_score(_qa_get(qa_json, "name_score"), 2), "max_score": 2},
            "\u0422\u0435\u0445\u043d\u0438\u043a\u0430 \u0437\u0430\u043f\u0438\u0441\u0438 \u043d\u0430 \u0437\u0430\u043c\u0435\u0440": {"score": _safe_score(_qa_get(qa_json, "booking_score"), 7), "max_score": 7},
            "\u0414\u0438\u043d\u0430\u043c\u0438\u043a\u0430 \u0440\u0430\u0437\u0433\u043e\u0432\u043e\u0440\u0430": {"score": _safe_score(_qa_get(qa_json, "dynamics_score"), 23), "max_score": 23},
            "\u0421\u0442\u0440\u0430\u0442\u0435\u0433\u0438\u0447\u0435\u0441\u043a\u0438\u0435 \u0442\u0435\u0445\u043d\u0438\u043a\u0438": {"score": _safe_score(_qa_get(qa_json, "strategy_score"), 55), "max_score": 55},
            "\u0420\u0430\u0431\u043e\u0442\u0430 \u0441 \u0432\u043e\u0437\u0440\u0430\u0436\u0435\u043d\u0438\u044f\u043c\u0438": {"score": _safe_score(_qa_get(qa_json, "objection_score"), 8), "max_score": 8},
        }
    probability = _percent_value(_qa_get(qa_json, "probability"))
    summary = str(_qa_get(qa_json, "summary") or _qa_get(qa_json, "detailed_summary") or _compact_summary(transcript))
    next_step = str(_qa_get(qa_json, "next_step") or "")
    recommendations = [
        item
        for item in [
            str(_qa_get(qa_json, "sell") or "").strip(),
            str(_qa_get(qa_json, "improve") or "").strip(),
            *_string_list(_qa_get(qa_json, "manager_recommendations")),
            *_string_list(_qa_get(qa_json, "rop_recommendations")),
        ]
        if item
    ]
    analysis_payload = {
        "source": source,
        "score_max": score_max,
        "qa_json": qa_json,
        "analysis_prompt": analysis_prompt,
        "record": {
            key: record.get(key)
            for key in ("lead_id", "contact_id", "direction", "kind", "recording_url", "occurred_at")
        },
    }
    if model:
        analysis_payload["model"] = model
    if redaction:
        analysis_payload["external_analysis_mode"] = "anonymized_openrouter"
        analysis_payload["redaction_stats"] = redaction.get("stats") or {}
        analysis_payload["anonymized_preview"] = str(redaction.get("text") or "")[:1200]
    return ConversationAnalysis(
        account_key=str(record["account_key"]),
        conversation_id=str(record["conversation_id"]),
        summary=summary,
        sentiment="positive" if probability >= 70 else "negative" if probability < 40 else "neutral",
        score=score,
        next_step=next_step,
        objections=_string_list(_qa_get(qa_json, "objections")),
        recommendations=recommendations,
        metrics={
            "score_max": score_max,
            "probability_percent": probability,
            "duration_seconds": record.get("duration_seconds"),
            "score_blocks": score_blocks,
            "checklist": checklist,
            "report_metrics": report_metrics,
            "percent_blocks": {
                "\u0423\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 \u043a\u043e\u043d\u0442\u0430\u043a\u0442\u0430": _percent_value(_qa_get(qa_json, "contact_pct")),
                "\u0412\u044b\u044f\u0432\u043b\u0435\u043d\u0438\u0435 \u043f\u043e\u0442\u0440\u0435\u0431\u043d\u043e\u0441\u0442\u0435\u0439": _percent_value(_qa_get(qa_json, "needs_pct")),
                "\u0423\u0441\u0438\u043b\u0435\u043d\u0438\u0435 \u0431\u043e\u043b\u0438": _percent_value(_qa_get(qa_json, "pain_pct")),
                "\u041f\u0440\u0435\u0437\u0435\u043d\u0442\u0430\u0446\u0438\u044f": _percent_value(_qa_get(qa_json, "presentation_pct")),
                "\u041e\u0442\u0440\u0430\u0431\u043e\u0442\u043a\u0430 \u0432\u043e\u0437\u0440\u0430\u0436\u0435\u043d\u0438\u0439": _percent_value(_qa_get(qa_json, "objection_pct")),
            },
            "transcript_chars": len(transcript),
        },
        analysis=analysis_payload,
    )


def _qa_get(qa_json: dict[str, Any], key: str) -> Any:
    return qa_json.get(QA_KEYS[key])


def _call_analysis_v2_to_analysis(
    record: dict[str, Any],
    transcript: str,
    qa_json: dict[str, Any],
    parsed: dict[str, Any],
    checklist_snapshot: dict[str, Any],
    *,
    source: str,
    model: str | None = None,
    redaction: dict[str, Any] | None = None,
) -> ConversationAnalysis:
    outcome = str(parsed.get("outcome") or "не_применимо")
    summary = str(parsed.get("summary") or _compact_summary(transcript))
    next_step = str(parsed.get("next_step") or "")
    coach_tip = str(parsed.get("coach_tip") or "").strip()
    refusal_reason = str(parsed.get("refusal_reason") or "").strip()
    outcome_status = CALL_ANALYSIS_V2_OUTCOME_STATUS.get(
        outcome,
        CALL_ANALYSIS_V2_OUTCOME_STATUS["не_применимо"],
    )
    sentiment = str(outcome_status["sentiment"])
    snapshot_steps = checklist_snapshot.get("steps") if isinstance(checklist_snapshot.get("steps"), list) else []
    quality_slugs = [
        str(step.get("slug") or "").strip()
        for step in snapshot_steps
        if isinstance(step, dict) and str(step.get("slug") or "").strip()
    ]
    parsed_steps = parsed.get("steps") if isinstance(parsed.get("steps"), dict) else {}
    quality_passed = sum(
        1
        for slug in quality_slugs
        if isinstance(parsed_steps.get(slug), dict) and parsed_steps[slug].get("ok") is True
    )
    quality_total = len(quality_slugs)
    quality_display = f"{quality_passed}/{quality_total}" if quality_total else "—"
    recommendations = [coach_tip] if coach_tip else []
    objections = [refusal_reason] if refusal_reason else []

    analysis_payload = {
        "source": source,
        "score_max": 0,
        "qa_json": qa_json,
        "call_analysis_v2": parsed,
        "analysis_prompt": checklist_snapshot.get("prompt"),
        "checklist_snapshot": checklist_snapshot,
        "record": {
            key: record.get(key)
            for key in ("lead_id", "contact_id", "direction", "kind", "recording_url", "occurred_at")
        },
    }
    if model:
        analysis_payload["model"] = model
    if redaction:
        analysis_payload["external_analysis_mode"] = "anonymized_openrouter"
        analysis_payload["redaction_stats"] = redaction.get("stats") or {}
        analysis_payload["anonymized_preview"] = str(redaction.get("text") or "")[:1200]

    return ConversationAnalysis(
        account_key=str(record["account_key"]),
        conversation_id=str(record["conversation_id"]),
        summary=summary,
        sentiment=sentiment,
        score=0,
        next_step=next_step,
        objections=objections,
        recommendations=recommendations,
        metrics={
            "score_max": 0,
            "duration_seconds": record.get("duration_seconds"),
            "outcome": outcome,
            "outcome_color": outcome_status["color"],
            "conversion_excluded": outcome_status["conversion_excluded"],
            "quality_passed": quality_passed,
            "quality_total": quality_total,
            "quality_display": quality_display,
            "call_analysis_v2": parsed,
            "transcript_chars": len(transcript),
        },
        analysis=analysis_payload,
    )


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("analysis response must be a JSON object")
    return value


def anonymize_transcript(transcript: str) -> dict[str, Any]:
    text = str(transcript or "")
    stats: dict[str, int] = {}

    def replace(pattern: str, label: str, value: str, flags: int = 0) -> None:
        nonlocal text
        text, count = re.subn(pattern, value, text, flags=flags)
        stats[label] = stats.get(label, 0) + count

    replace(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-zа-яё]{2,}\b", "emails", "[EMAIL]")
    replace(r"(?i)\b(?:https?://|www\.)\S+\b", "urls", "[URL]")
    replace(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)", "phones", "[PHONE]")
    replace(r"(?i)\b(?:ул\.?|улица|проспект|пр-т|переулок|пер\.?|шоссе|дом|д\.|квартира|кв\.|офис|город|г\.|район|р-н)\s+[^\n,.;]{1,80}", "addresses", "[ADDRESS]")
    replace(r"\b\d{5,}\b", "long_numbers", "[NUMBER]")
    replace(r"(?i)\b(меня зовут|зовут|это|клиент|менеджер)\s+[А-ЯЁ][а-яё]{2,}\b", "introduced_names", r"\1 [NAME]")
    replace(r"\b[А-ЯЁ][а-яё]{2,}\b", "capitalized_names", "[NAME]")
    return {
        "text": " ".join(text.split()),
        "stats": {key: value for key, value in stats.items() if value},
    }


def _safe_analysis_prompt(value: Any) -> str:
    prompt = str(value or "").strip()
    if len(prompt) >= 100 and prompt.count("?") < len(prompt) * 0.25:
        return prompt
    return (
        "РОЛЬ\n"
        "Ты старший специалист контроля качества продаж. Оцени телефонный разговор по продукту: "
        "натяжные потолки, цель звонка - записать клиента на бесплатный замер.\n\n"
        "ВХОДНЫЕ ДАННЫЕ\n"
        "Транскрибация обезличена: имена, телефоны, адреса, ссылки и длинные номера заменены плейсхолдерами.\n"
        "\"\"\"\n{{ТРАНСКРИБАЦИЯ}}\n\"\"\"\n\n"
        "Оценивай только по фактам из транскрибации. Если данных нет, пиши \"не определено\" или 0. "
        "Контекст продаж: компания продает натяжные потолки; ключевая цель первичного звонка - "
        "понять потребность клиента и записать его на бесплатный замер. "
        "Рекомендации менеджеру должны быть прикладными: что сказать/уточнить/сделать в следующем контакте. "
        "Рекомендации РОП должны быть управленческими: что проверить в CRM, что разобрать на коучинге, "
        "какой риск в сделке и какой контроль поставить. "
        "В поле \"Транскрибация\" верни очищенный диалог построчно в формате "
        "\"Менеджер: ...\" и \"Клиент: ...\"; технические JSON-пояснения туда не добавляй. "
        "Верни строго валидный JSON без markdown со всеми ключами:\n"
        "{"
        "\"Итог разговора\":\"\",\"Подробное резюме разговора\":\"\",\"Следующий шаг\":\"\","
        "\"Вероятность продажи\":\"__%\",\"Объяснение оценки (вероятность продажи)\":\"\","
        "\"Как продать?\":\"\",\"Рекомендации менеджеру\":[\"\"],\"Рекомендации РОП\":[\"\"],"
        "\"ЛПР?\":\"да/нет/не определено — с обоснованием\","
        "\"Факты\":[\"\"],\"Потребности\":[\"\"],\"Боли\":[\"\"],\"Возражения\":[\"\"],"
        "\"Предложение соответствует потребностям\":\"да/частично/нет — почему\","
        "\"Потребности закрыли\":[\"\"],\"Потребности НЕ закрыли\":[\"\"],"
        "\"Установление контакта (%)\":\"__%\",\"Объяснение (Установление контакта)\":\"\","
        "\"Выявление потребностей (%)\":\"__%\",\"Объяснение (Выявление потребностей)\":\"\","
        "\"Усиление боли (%)\":\"__%\",\"Объяснение (Усиление болей)\":\"\","
        "\"Как улучшить\":\"\",\"Презентация (%)\":\"__%\",\"Объяснение оценки (Презентация)\":\"\","
        "\"Отработка возражений (%)\":\"__%\",\"Объяснение (Отработка возражений)\":\"\","
        "\"Кто лидер?\":\"менеджер/клиент — кто вёл разговор и держал инициативу\","
        "\"Лидер, почему\":\"\",\"Работа с именем (0-2)\":0,"
        "\"Объяснение оценки (Работа с именем)\":\"\",\"Техника записи на замер (0-7)\":0,"
        "\"Объяснение оценки (Техника записи на замер)\":\"\",\"Динамика разговора (0-23)\":0,"
        "\"Объяснение оценки (Динамика разговора)\":\"\",\"Стратегические техники (0-55)\":0,"
        "\"Объяснение оценки (Стратегические техники)\":\"\",\"Работа с возражениями (0-8)\":0,"
        "\"Объяснение оценки (Работа с возражениями)\":\"\",\"ИТОГО баллов (из 95)\":0,"
        "\"Транскрибация\":\"Очищенная расшифровка с ролями\""
        "}"
    )
