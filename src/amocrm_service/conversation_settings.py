from __future__ import annotations

from typing import Any


DEFAULT_ANALYSIS_PROMPT = """РОЛЬ
Ты старший РОП и специалист контроля качества продаж в компании по натяжным потолкам.

КОНТЕКСТ
Продукт: натяжные потолки.
Главная цель первичного звонка: понять задачу клиента, выявить параметры объекта и записать клиента на бесплатный замер.
Оценивай только по фактам из транскрибации. Если данных нет, пиши "не определено" или 0.

ЧТО НУЖНО ОЦЕНИТЬ
1. Установление контакта: приветствие, тон, работа с именем, понятное представление.
2. Выявление потребностей: площадь, количество потолков/комнат, адрес/район, сроки, тип помещения, бюджет, кто принимает решение.
3. Презентация и ценность: объяснил ли менеджер пользу бесплатного замера, что будет после замера, почему клиенту удобно продолжить.
4. Запись на замер: предложил ли конкретные слоты, подтвердил ли дату/время/контакты, зафиксировал ли следующий шаг.
5. Работа с возражениями: цена, сроки, "подумаю", конкурент, нет ЛПР, не готов назвать параметры.
6. Управление разговором: кто вел инициативу, были ли паузы/потери, довел ли менеджер разговор до результата.

РЕКОМЕНДАЦИИ
Рекомендации менеджеру должны быть прикладными: что сказать, что уточнить, какое возражение закрыть, какой следующий шаг сделать.
Рекомендации РОП должны быть управленческими: что проверить в CRM, что разобрать с менеджером, какой риск по сделке, какой контроль поставить.

ДИАЛОГ
В поле "Транскрибация" верни очищенный диалог построчно в формате:
"Менеджер: ..."
"Клиент: ..."
Если роль неочевидна, определи её по смыслу фразы. Фразы про компанию, заявку, замер, площадь, слоты и уточняющие вопросы обычно говорит менеджер. Фразы согласия, сомнений, "не знаю", "хочу узнать", "мне нужно" обычно говорит клиент.

ФОРМАТ ОТВЕТА
Верни строго валидный JSON без markdown и без технических пояснений.
Обязательные ключи:
{
  "Итог разговора": "",
  "Подробное резюме разговора": "",
  "Следующий шаг": "",
  "Вероятность продажи": "__%",
  "Объяснение оценки (вероятность продажи)": "",
  "Как продать?": "",
  "Рекомендации менеджеру": [""],
  "Рекомендации РОП": [""],
  "ЛПР?": "да/нет/не определено — с обоснованием",
  "Факты": [""],
  "Потребности": [""],
  "Боли": [""],
  "Возражения": [""],
  "Предложение соответствует потребностям": "да/частично/нет — почему",
  "Потребности закрыли": [""],
  "Потребности НЕ закрыли": [""],
  "Установление контакта (%)": "__%",
  "Объяснение (Установление контакта)": "",
  "Выявление потребностей (%)": "__%",
  "Объяснение (Выявление потребностей)": "",
  "Усиление боли (%)": "__%",
  "Объяснение (Усиление болей)": "",
  "Как улучшить": "",
  "Презентация (%)": "__%",
  "Объяснение оценки (Презентация)": "",
  "Отработка возражений (%)": "__%",
  "Объяснение (Отработка возражений)": "",
  "Кто лидер?": "менеджер/клиент — кто вел разговор и держал инициативу",
  "Лидер, почему": "",
  "Работа с именем (0-2)": 0,
  "Объяснение оценки (Работа с именем)": "",
  "Техника записи на замер (0-7)": 0,
  "Объяснение оценки (Техника записи на замер)": "",
  "Динамика разговора (0-23)": 0,
  "Объяснение оценки (Динамика разговора)": "",
  "Стратегические техники (0-55)": 0,
  "Объяснение оценки (Стратегические техники)": "",
  "Работа с возражениями (0-8)": 0,
  "Объяснение оценки (Работа с возражениями)": "",
  "ИТОГО баллов (из 95)": 0,
  "Транскрибация": ""
}"""

DEFAULT_SCORING = [
    {"key": "interest", "label": "Интерес клиента", "max_score": 20},
    {"key": "need_clarity", "label": "Понимание потребности", "max_score": 20},
    {"key": "next_step", "label": "Конкретный следующий шаг", "max_score": 25},
    {"key": "objection_handling", "label": "Работа с возражениями", "max_score": 20},
    {"key": "crm_hygiene", "label": "Данные для CRM", "max_score": 15},
]


DEFAULT_CONVERSATION_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "filters": {
        "pipeline_ids": [],
        "status_ids": [],
        "responsible_user_ids": [],
        "min_duration_seconds": 30,
        "max_duration_seconds": 0,
        "new_calls_only": True,
        "started_at": 0,
    },
    "actions": {
        "import_leads": True,
        "probe_recordings": True,
        "download_recordings": True,
        "transcribe": True,
        "analyze": True,
        "post_note": False,
        "export_google_sheets": False,
    },
    "analysis_prompt": DEFAULT_ANALYSIS_PROMPT,
    "external_analysis": {
        "mode": "openrouter_raw",
        "provider": "openrouter",
        "model": "",
    },
    "scoring": DEFAULT_SCORING,
    "google_sheets": {
        "spreadsheet_id": "",
        "worksheet_name": "amoCRM call analysis",
    },
}


def conversation_settings(account_settings: dict[str, Any] | None) -> dict[str, Any]:
    settings = _deep_merge(DEFAULT_CONVERSATION_SETTINGS, (account_settings or {}).get("conversation_intelligence") or {})
    settings["filters"] = _normalize_filters(settings.get("filters") or {})
    settings["actions"] = _deep_merge(DEFAULT_CONVERSATION_SETTINGS["actions"], settings.get("actions") or {})
    settings["google_sheets"] = _deep_merge(
        DEFAULT_CONVERSATION_SETTINGS["google_sheets"],
        settings.get("google_sheets") or {},
    )
    settings["external_analysis"] = _deep_merge(
        DEFAULT_CONVERSATION_SETTINGS["external_analysis"],
        settings.get("external_analysis") or {},
    )
    mode = str(settings["external_analysis"].get("mode") or "local").strip()
    if mode not in {"local", "anonymized_openrouter", "openrouter_raw"}:
        mode = "openrouter_raw"
    settings["external_analysis"]["mode"] = mode
    settings["external_analysis"]["provider"] = "openrouter"
    settings["external_analysis"]["model"] = str(settings["external_analysis"].get("model") or "").strip()
    scoring = settings.get("scoring")
    settings["scoring"] = scoring if isinstance(scoring, list) and scoring else DEFAULT_SCORING
    if not str(settings.get("analysis_prompt") or "").strip():
        settings["analysis_prompt"] = DEFAULT_ANALYSIS_PROMPT
    settings["enabled"] = bool(settings.get("enabled"))
    return settings


def update_conversation_settings(account_settings: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    next_settings = dict(account_settings or {})
    current = conversation_settings(next_settings)
    next_settings["conversation_intelligence"] = _deep_merge(current, patch)
    return next_settings


def record_matches_filters(record: dict[str, Any], lead: dict[str, Any] | None, filters: dict[str, Any]) -> bool:
    duration = int(record.get("duration_seconds") or 0)
    min_duration = int(filters.get("min_duration_seconds") or 0)
    max_duration = int(filters.get("max_duration_seconds") or 0)
    if min_duration and duration < min_duration:
        return False
    if max_duration and duration > max_duration:
        return False
    if filters.get("new_calls_only"):
        started_at = int(filters.get("started_at") or 0)
        if started_at and int(record.get("occurred_at") or 0) < started_at:
            return False
    metadata = record.get("metadata") or {}
    lead_at_call = metadata.get("lead_at_call") if isinstance(metadata, dict) else None
    lead_scope = lead_at_call if isinstance(lead_at_call, dict) and lead_at_call else lead
    if not lead_scope:
        return not any(filters.get(key) for key in ("pipeline_ids", "status_ids", "responsible_user_ids"))
    if filters.get("pipeline_ids") and int(lead_scope.get("pipeline_id") or 0) not in filters["pipeline_ids"]:
        return False
    if filters.get("status_ids") and int(lead_scope.get("status_id") or 0) not in filters["status_ids"]:
        return False
    if filters.get("responsible_user_ids") and int(lead_scope.get("responsible_user_id") or 0) not in filters["responsible_user_ids"]:
        return False
    return True


def parse_int_list(value: Any) -> list[int]:
    raw_items = value if isinstance(value, list) else str(value or "").replace("\n", ",").split(",")
    result = []
    for item in raw_items:
        text = str(item).strip()
        if not text:
            continue
        result.append(int(text))
    return result


def _normalize_filters(filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "pipeline_ids": parse_int_list(filters.get("pipeline_ids") or []),
        "status_ids": parse_int_list(filters.get("status_ids") or []),
        "responsible_user_ids": parse_int_list(filters.get("responsible_user_ids") or []),
        "min_duration_seconds": int(filters.get("min_duration_seconds") or 0),
        "max_duration_seconds": int(filters.get("max_duration_seconds") or 0),
        "new_calls_only": bool(filters.get("new_calls_only", True)),
        "started_at": int(filters.get("started_at") or 0),
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
