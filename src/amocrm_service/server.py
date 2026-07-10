from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse
from typing import Any

from amocrm_service.amocrm import AmoCRMClient
from amocrm_service.activity import ActivityService
from amocrm_service.ai_formula import AiFormulaError, build_formula_draft
from amocrm_service.analytics import AnalyticsService
from amocrm_service.analytics_query import AnalyticsQuery, FlexibleAnalyticsService
from amocrm_service.auto_sync import (
    default_auto_sync_settings,
    mark_auto_sync_error,
    mark_group_started,
    next_due_group,
    normalize_auto_sync_settings,
)
from amocrm_service.config import load_settings
from amocrm_service.conversations import ConversationPipeline, format_transcript_with_roles, repair_role_transcript
from amocrm_service.conversation_automation import ConversationAutomationService
from amocrm_service.conversation_export import ConversationExportService
from amocrm_service.conversation_notes import build_lead_analysis_note, find_record_and_analysis
from amocrm_service.conversation_settings import (
    conversation_settings,
    parse_int_list,
    record_matches_filters,
    update_conversation_settings,
)
from amocrm_service.dashboard import SYNC_OPTIONS, render_dashboard
from amocrm_service.db import connect, init_db
from amocrm_service.filters import load_analytics_filter, save_analytics_filter
from amocrm_service.formula_engine import FormulaDictionaryService, FormulaEngine
from amocrm_service.freshness import FreshnessService
from amocrm_service.kpi import KpiService
from amocrm_service.quality import QualityService
from amocrm_service.quality_settings import quality_settings, update_quality_settings
from amocrm_service.repository import Repository, utc_now
from amocrm_service.site_forms import parse_site_lead_payload
from amocrm_service.sync import BOOTSTRAP_ENTITIES, SyncService
from amocrm_service.tenancy import (
    admin_connections,
    create_connection,
    list_connections,
    load_account_settings,
    load_user_settings,
    safe_key,
    save_account_settings,
    save_user_settings,
    set_connection_status,
)
from amocrm_service.widgets import (
    add_widget,
    load_dashboard_pages,
    load_widget_results_cache,
    load_widgets,
    load_work_sources,
    save_dashboard_pages,
    save_widget_results_cache,
    save_widgets,
    save_work_sources,
    widget_signature,
)


WEBHOOK_ENTITY_MAP = {
    "lead": "leads",
    "leads": "leads",
    "contact": "contacts",
    "contacts": "contacts",
    "company": "companies",
    "companies": "companies",
    "customer": "customers",
    "customers": "customers",
    "task": "tasks",
    "tasks": "tasks",
    "talks": "talks",
}

LOCAL_TZ = timezone(timedelta(hours=3))

ENTITY_LABELS = {
    "leads": "Сделки",
    "contacts": "Контакты",
    "companies": "Компании",
    "tasks": "Задачи",
    "customers": "Покупатели",
    "events": "События",
    "lead_notes": "Примечания сделок",
    "contact_notes": "Примечания контактов",
    "company_notes": "Примечания компаний",
    "customer_notes": "Примечания покупателей",
    "users": "Пользователи",
    "pipelines": "Воронки и этапы",
    "lead_custom_fields": "Поля сделок",
    "contact_custom_fields": "Поля контактов",
    "company_custom_fields": "Поля компаний",
    "customer_custom_fields": "Поля покупателей",
    "catalogs": "Каталоги",
    "catalog_elements": "Элементы каталогов",
    "salesbots": "Salesbot",
    "talks": "Чаты",
    "messages": "Сообщения",
}

STATUS_LABELS = {
    "active": "Активен",
    "disabled": "Отключен",
    "archived": "Архив",
    "pending": "Ожидает",
    "running": "Выполняется",
    "failed": "Ошибка",
    "done": "Готово",
    "ignored": "Игнорируется",
    "interrupted": "Прервано",
    "success": "Успешно",
    "partial": "Завершено с ошибками",
    "metadata_only": "Только метаданные",
    "recording_found": "Запись найдена",
    "audio_accessible": "Запись доступна",
    "audio_downloaded": "Запись скачана",
    "recording_unavailable": "Запись недоступна",
    "audio_download_failed": "Ошибка скачивания",
    "transcribed": "Расшифрован",
}

JOB_TYPE_LABELS = {
    "bootstrap": "Первичная выгрузка",
    "resync": "Перевыгрузка хаба",
    "source_bootstrap": "Первичная выгрузка источника",
    "source_resync": "Выгрузка источника",
    "queue": "Обработка очереди",
}

SOURCE_LABELS = {
    "sync": "Синхронизация",
    "sync_job": "Фоновая синхронизация",
    "webhook": "Webhook amoCRM",
    "api": "API",
    "queue": "Очередь",
}

ACTION_LABELS = {
    "refresh": "Обновить",
    "delete": "Удалить",
    "create": "Создать",
    "update": "Обновить",
}

_SYNC_THREADS: dict[int, threading.Thread] = {}
_SYNC_THREADS_LOCK = threading.Lock()
_DASHBOARD_REFRESH_THREADS: dict[str, threading.Thread] = {}
_DASHBOARD_REFRESH_LOCK = threading.Lock()
_QUEUE_WORKER_THREAD: threading.Thread | None = None
_QUEUE_WORKER_STOP = threading.Event()
_QUEUE_WORKER_LOCK = threading.Lock()
_RUNTIME_BACKGROUND_ACCOUNTS: dict[str, dict[str, str]] = {}
_RUNTIME_BACKGROUND_ACCOUNTS_LOCK = threading.Lock()
_QUEUE_WORKER_STATE: dict[str, Any] = {
    "enabled": False,
    "started_at": None,
    "last_run_at": None,
    "last_error": None,
    "runs": 0,
    "processed": 0,
    "failed": 0,
    "accounts": {},
}
CONVERSATION_AUTO_INTERVAL_SECONDS = 60


def _repo(settings: Any | None = None) -> Repository:
    settings = settings or load_settings()
    init_db(settings.db_path)
    return Repository(connect(settings.db_path))


def _settings_from_query(query: str = ""):
    params = parse_qs(query)
    settings = load_settings(
        account_key=(params.get("account") or [None])[-1],
        user_key=(params.get("user") or [None])[-1],
    )
    _remember_runtime_background_account(settings)
    return settings


def _remember_runtime_background_account(settings: Any) -> None:
    account_id = f"{settings.user_key}/{settings.account_key}"
    with _RUNTIME_BACKGROUND_ACCOUNTS_LOCK:
        _RUNTIME_BACKGROUND_ACCOUNTS[account_id] = {
            "user_key": settings.user_key,
            "account_key": settings.account_key,
        }


def _background_account_items() -> list[dict[str, Any]]:
    items_by_id = {
        f"{item['user_key']}/{item['account_key']}": dict(item)
        for item in list_connections(include_metrics=False)
    }
    with _RUNTIME_BACKGROUND_ACCOUNTS_LOCK:
        for account_id, item in _RUNTIME_BACKGROUND_ACCOUNTS.items():
            items_by_id.setdefault(account_id, dict(item))
    return list(items_by_id.values())


def _empty_dashboard_summary() -> dict[str, Any]:
    return {
        "totals": {
            "pipelines_count": 0,
            "leads_count": 0,
            "open_count": 0,
            "won_count": 0,
            "lost_count": 0,
            "total_price": 0,
        },
        "pipelines": [],
    }


def _empty_tasks_summary() -> dict[str, int]:
    return {
        "total": 0,
        "completed": 0,
        "open": 0,
        "overdue": 0,
    }


def _dashboard_html(
    sync_result: list[dict[str, Any]] | None = None,
    page: str = "dashboard",
    settings: Any | None = None,
    query_string: str = "",
) -> str:
    settings = settings or load_settings()
    query = parse_qs(query_string)
    raw_source_id = (query.get("source_id") or [""])[-1]
    try:
        selected_source_id = int(raw_source_id) if raw_source_id else None
    except ValueError:
        selected_source_id = None
    repo = _repo(settings)
    analytics_filter = load_analytics_filter(settings.db_path)
    filter_options: list[dict[str, Any]] = []
    if page == "settings":
        filter_options = AnalyticsService(repo).pipeline_filter_options()
    return render_dashboard(
        _empty_dashboard_summary(),
        _empty_tasks_summary(),
        filter_options=filter_options,
        active_filter=analytics_filter.to_json(),
        sync_sources=repo.list_sync_sources(settings.account_key),
        selected_source_id=selected_source_id,
        work_source_ids=load_work_sources(settings.db_path),
        sync_result=sync_result,
        page=page,
        user_key=settings.user_key,
        account_key=settings.account_key,
    )
def _dashboard_data_version(repo: Repository, account_key: str) -> str:
    try:
        row = repo.conn.execute(
            """
            SELECT MAX(value) AS data_version
            FROM (
                SELECT MAX(synced_at) AS value FROM raw_entities
                UNION ALL
                SELECT MAX(synced_at) AS value FROM sync_source_entities
                UNION ALL
                SELECT MAX(updated_at) AS value FROM sync_sources WHERE account_key = ?
                UNION ALL
                SELECT MAX(finished_at) AS value FROM sync_jobs
            )
            """,
            (account_key,),
        ).fetchone()
    except Exception:
        return ""
    return str(row["data_version"] or "") if row else ""


def _dashboard_refresh_key(settings: Any) -> str:
    return f"{settings.user_key}:{settings.account_key}:{settings.db_path}"


def _start_dashboard_background_refresh(settings: Any) -> bool:
    key = _dashboard_refresh_key(settings)
    with _DASHBOARD_REFRESH_LOCK:
        thread = _DASHBOARD_REFRESH_THREADS.get(key)
        if thread and thread.is_alive():
            return False

        def run() -> None:
            try:
                _dashboard_widget_results(settings, force=True, allow_background=False)
            finally:
                with _DASHBOARD_REFRESH_LOCK:
                    current = _DASHBOARD_REFRESH_THREADS.get(key)
                    if current is threading.current_thread():
                        _DASHBOARD_REFRESH_THREADS.pop(key, None)

        thread = threading.Thread(target=run, name=f"dashboard-refresh-{settings.account_key}", daemon=True)
        _DASHBOARD_REFRESH_THREADS[key] = thread
        thread.start()
        return True


def _dashboard_widget_results(settings: Any, force: bool = False, allow_background: bool = True, cache_only: bool = False) -> dict[str, Any]:
    widgets = load_widgets(settings.db_path)
    cache = load_widget_results_cache(settings.db_path)
    next_cache: dict[str, Any] = {}
    results: dict[str, Any] = {}
    if cache_only and not force:
        needs_background_refresh = False
        for widget in widgets:
            widget_id = str(widget["id"])
            signature = widget_signature(widget)
            cached = cache.get(widget_id)
            cache_matches_signature = isinstance(cached, dict) and cached.get("signature") == signature
            if cache_matches_signature and isinstance(cached.get("rows"), list):
                results[widget_id] = {
                    "ok": True,
                    "cached": True,
                    "cache_only": True,
                    "cached_at": cached.get("cached_at"),
                    "data_version": cached.get("data_version") or "",
                    "current_data_version": cached.get("data_version") or "",
                    "auto_refreshed": False,
                    "stale": False,
                    "refresh_pending": False,
                    "rows": cached.get("rows") or [],
                    "row_count": int(cached.get("row_count") or len(cached.get("rows") or [])),
                    "formula_result": cached.get("formula_result"),
                }
            else:
                needs_background_refresh = True
                results[widget_id] = {
                    "ok": False,
                    "cached": False,
                    "cache_only": True,
                    "error": "Сохраненный результат еще не рассчитан",
                    "refresh_pending": True,
                }
        refresh_started = _start_dashboard_background_refresh(settings) if allow_background and needs_background_refresh else False
        return {
            "widgets": widgets,
            "results": results,
            "refresh_pending": needs_background_refresh,
            "refresh_started": refresh_started,
            "cache_only": True,
        }

    repo = _repo(settings)
    service = FlexibleAnalyticsService(repo)
    formula_engine = FormulaEngine(repo)
    data_version = _dashboard_data_version(repo, settings.account_key)
    needs_background_refresh = False

    for widget in widgets:
        widget_id = str(widget["id"])
        signature = widget_signature(widget)
        cached = cache.get(widget_id)
        cache_matches_signature = isinstance(cached, dict) and cached.get("signature") == signature
        cache_matches_data = isinstance(cached, dict) and cached.get("data_version") == data_version
        if not force and isinstance(cached, dict) and cache_matches_signature and isinstance(cached.get("rows"), list):
            is_stale = not cache_matches_data
            if is_stale:
                needs_background_refresh = True
            next_cache[widget_id] = cached
            results[widget_id] = {
                "ok": True,
                "cached": True,
                "cached_at": cached.get("cached_at"),
                "data_version": cached.get("data_version") or data_version,
                "current_data_version": data_version,
                "auto_refreshed": False,
                "stale": is_stale,
                "refresh_pending": is_stale,
                "rows": cached.get("rows") or [],
                "row_count": int(cached.get("row_count") or len(cached.get("rows") or [])),
                "formula_result": cached.get("formula_result"),
            }
            continue

        try:
            auto_refreshed = bool(not force and cache_matches_signature and not cache_matches_data)
            is_formula_widget = widget.get("widget_type") == "formula" or bool(widget.get("formula_spec"))
            if is_formula_widget:
                formula_result = formula_engine.evaluate(widget.get("formula_spec") or {})
                rows = formula_result.get("rows") if isinstance(formula_result.get("rows"), list) else []
                if formula_result.get("kind") == "scalar":
                    rows = [{"value": formula_result.get("value")}]
                entry = {
                    "signature": signature,
                    "data_version": data_version,
                    "cached_at": utc_now(),
                    "rows": rows,
                    "row_count": len(rows),
                    "formula_result": formula_result,
                }
                next_cache[widget_id] = entry
                results[widget_id] = {
                    "ok": True,
                    "cached": False,
                    "cached_at": entry["cached_at"],
                    "data_version": entry["data_version"],
                    "current_data_version": data_version,
                    "auto_refreshed": auto_refreshed,
                    "stale": False,
                    "refresh_pending": False,
                    "rows": entry["rows"],
                    "row_count": entry["row_count"],
                    "formula_result": entry["formula_result"],
                }
                continue

            result = service.run(AnalyticsQuery.from_payload(widget.get("query") or {}))
            entry = {
                "signature": signature,
                "data_version": data_version,
                "cached_at": utc_now(),
                "rows": result.get("rows") or [],
                "row_count": int(result.get("row_count") or 0),
            }
            next_cache[widget_id] = entry
            results[widget_id] = {
                "ok": True,
                "cached": False,
                "cached_at": entry["cached_at"],
                "data_version": entry["data_version"],
                "current_data_version": data_version,
                "auto_refreshed": auto_refreshed,
                "stale": False,
                "refresh_pending": False,
                "rows": entry["rows"],
                "row_count": entry["row_count"],
            }
        except Exception as exc:
            results[widget_id] = {"ok": False, "error": str(exc)}
            if isinstance(cached, dict):
                next_cache[widget_id] = cached

    save_widget_results_cache(settings.db_path, next_cache)
    refresh_started = _start_dashboard_background_refresh(settings) if allow_background and needs_background_refresh else False
    return {"widgets": widgets, "results": results, "refresh_pending": needs_background_refresh, "refresh_started": refresh_started}


def _find_formula_drilldown(formula_result: dict[str, Any], row_key: str, column: str) -> dict[str, Any] | None:
    rows = formula_result.get("rows") if isinstance(formula_result, dict) else []
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        current_key = str(row.get("key") or row.get("label") or "")
        if current_key != row_key:
            continue
        cell_drilldown = row.get("_drilldown")
        if isinstance(cell_drilldown, dict) and isinstance(cell_drilldown.get(column), dict):
            return cell_drilldown[column]
        if column in {"Результат", "value", "result"} and isinstance(row.get("entity_ids"), list):
            return {
                "entity_type": row.get("entity_type") or formula_result.get("meta", {}).get("entity") or "leads",
                "entity_ids": row.get("entity_ids") or [],
                "total": row.get("trace_total") or row.get("value") or len(row.get("entity_ids") or []),
                "truncated": bool(row.get("trace_truncated")),
            }
    return None


def _drilldown_user_labels(repo: Repository) -> dict[str, str]:
    labels: dict[str, str] = {}
    rows = repo.conn.execute(
        """
        SELECT entity_id, name, payload_json
        FROM raw_entities
        WHERE entity_type = 'users'
        """
    ).fetchall()
    for row in rows:
        entity_id = str(row["entity_id"] or "")
        if not entity_id:
            continue
        payload = json.loads(row["payload_json"] or "{}")
        label = str(payload.get("name") or row["name"] or payload.get("login") or payload.get("email") or "").strip()
        labels[entity_id] = label or f"Пользователь {entity_id}"
    return labels


def _drilldown_pipeline_labels(repo: Repository) -> tuple[dict[str, str], dict[str, str]]:
    pipelines: dict[str, str] = {}
    statuses: dict[str, str] = {}
    for pipeline in repo.all_payloads("pipelines"):
        pipeline_id = str(pipeline.get("id") or "")
        pipeline_name = str(pipeline.get("name") or "").strip()
        if pipeline_id:
            pipelines[pipeline_id] = pipeline_name or f"Воронка {pipeline_id}"
        for status in (pipeline.get("_embedded") or {}).get("statuses") or []:
            status_id = str(status.get("id") or "")
            status_name = str(status.get("name") or "").strip()
            if status_id:
                statuses[status_id] = status_name or f"Этап {status_id}"
    return pipelines, statuses


def _format_drilldown_datetime(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        if raw.isdigit():
            return datetime.fromtimestamp(int(raw), timezone.utc).astimezone(timezone(timedelta(hours=3))).strftime("%d.%m.%Y, %H:%M")
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=3))).strftime("%d.%m.%Y, %H:%M")
    except Exception:
        return raw


def _amo_leads_filter_url(settings: Any, entity_ids: list[str]) -> str:
    if not settings.account_base_url or not entity_ids:
        return ""
    query = "&".join(f"filter%5Bid%5D%5B%5D={quote(str(entity_id))}" for entity_id in entity_ids[:250])
    return f"{settings.account_base_url}/leads/list/?{query}"


def _formula_cell_node(widget: dict[str, Any], column: str) -> dict[str, Any] | None:
    spec = widget.get("formula_spec")
    if not isinstance(spec, dict):
        return None
    if spec.get("op") == "table":
        columns = spec.get("columns")
        node = columns.get(column) if isinstance(columns, dict) else None
        return node if isinstance(node, dict) else None
    return spec


def _first_aggregate_node(node: Any) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    if str(node.get("op") or "").lower() in {"count", "sum", "avg", "min", "max"}:
        return node
    for key in ("left", "right"):
        found = _first_aggregate_node(node.get(key))
        if found:
            return found
    return None


def _source_pipeline_ids(repo: Repository, settings: Any, source_id: int | None) -> list[str]:
    if not source_id:
        return []
    row = repo.conn.execute(
        "SELECT pipeline_ids_json FROM sync_sources WHERE id = ? AND account_key = ?",
        (source_id, settings.account_key),
    ).fetchone()
    if not row:
        return []
    try:
        pipeline_ids = [str(item) for item in json.loads(row["pipeline_ids_json"] or "[]") if str(item or "").strip()]
    except (TypeError, ValueError, json.JSONDecodeError):
        pipeline_ids = []
    return pipeline_ids


def _source_pipeline_path(repo: Repository, settings: Any, source_id: int | None) -> str:
    pipeline_ids = _source_pipeline_ids(repo, settings, source_id)
    return f"/leads/pipeline/{quote(pipeline_ids[0])}/" if len(pipeline_ids) == 1 else "/leads/list/"


def _pipeline_status_filter_parts(repo: Repository, pipeline_id: str) -> list[str]:
    statuses: list[str] = []
    for pipeline in repo.all_payloads("pipelines"):
        if str(pipeline.get("id") or "") != str(pipeline_id):
            continue
        for status in (pipeline.get("_embedded") or {}).get("statuses") or []:
            status_id = str(status.get("id") or "").strip()
            if status_id and status_id not in statuses:
                statuses.append(status_id)
        break
    return [f"filter%5Bpipe%5D%5B{quote(str(pipeline_id))}%5D%5B%5D={quote(status_id)}" for status_id in statuses]


def _resolve_cf_enum_id(repo: Repository, field_id: int, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    row = None
    try:
        numeric = float(raw.replace(",", "."))
        row = repo.conn.execute(
            """
            SELECT enum_id
            FROM entity_custom_field_values
            WHERE entity_type = 'leads'
              AND field_id = ?
              AND enum_id IS NOT NULL
              AND value_num = ?
            GROUP BY enum_id
            ORDER BY COUNT(*) DESC
            LIMIT 1
            """,
            (field_id, numeric),
        ).fetchone()
    except ValueError:
        row = None
    if not row:
        row = repo.conn.execute(
            """
            SELECT enum_id
            FROM entity_custom_field_values
            WHERE entity_type = 'leads'
              AND field_id = ?
              AND enum_id IS NOT NULL
              AND lower(value_text) = lower(?)
            GROUP BY enum_id
            ORDER BY COUNT(*) DESC
            LIMIT 1
            """,
            (field_id, raw),
        ).fetchone()
    return str(row["enum_id"]) if row and row["enum_id"] is not None else raw


def _condition_to_amo_filter(repo: Repository, condition: dict[str, Any]) -> list[str]:
    field = str(condition.get("field") or "")
    op = str(condition.get("op") or condition.get("operator") or "").lower()
    if not field.startswith("cf_"):
        return []
    try:
        field_id = int(field.removeprefix("cf_").removeprefix("month_"))
    except ValueError:
        return []
    if op in {"this_month", "current_month"}:
        return [f"filter%5Bcf%5D%5B{field_id}%5D%5Bdate_preset%5D=current_month"]
    if op in {"previous_month"}:
        return [f"filter%5Bcf%5D%5B{field_id}%5D%5Bdate_preset%5D=last_month"]
    if op in {"eq", "in"}:
        values = condition.get("value")
        if op == "eq":
            values = [values]
        if not isinstance(values, list):
            return []
        parts = []
        for value in values:
            enum_id = _resolve_cf_enum_id(repo, field_id, value)
            if enum_id:
                parts.append(f"filter%5Bcf%5D%5B{field_id}%5D%5B%5D={quote(enum_id)}")
        return parts
    return []


def _field_labels_for_formula(repo: Repository) -> dict[str, str]:
    labels = {
        "id": "ID сделки",
        "name": "Название сделки",
        "pipeline_id": "Воронка",
        "status_id": "Этап",
        "responsible_user_id": "Ответственный",
        "price": "Бюджет",
        "created_at": "Дата создания",
        "updated_at": "Дата обновления",
        "closed_at": "Дата закрытия",
    }
    rows = repo.conn.execute(
        """
        SELECT field_id, MAX(field_name) AS field_name
        FROM entity_custom_field_values
        WHERE entity_type = 'leads'
        GROUP BY field_id
        """
    ).fetchall()
    for row in rows:
        field_id = str(row["field_id"] or "").strip()
        if field_id:
            labels[f"cf_{field_id}"] = str(row["field_name"] or f"Поле {field_id}")
    return labels


def _pipeline_status_index(repo: Repository) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    pipelines: dict[str, str] = {}
    statuses: dict[str, dict[str, Any]] = {}
    for pipeline in repo.all_payloads("pipelines"):
        pipeline_id = str(pipeline.get("id") or "").strip()
        if not pipeline_id:
            continue
        pipelines[pipeline_id] = str(pipeline.get("name") or pipeline_id)
        for status in (pipeline.get("_embedded") or {}).get("statuses") or []:
            status_id = str(status.get("id") or "").strip()
            if not status_id:
                continue
            statuses[status_id] = {
                "id": status_id,
                "name": str(status.get("name") or status_id),
                "pipeline_id": pipeline_id,
                "pipeline_name": pipelines[pipeline_id],
            }
    return pipelines, statuses


def _source_for_amo_pipeline(
    repo: Repository,
    settings: Any,
    pipeline_ids: set[str],
    status_ids: set[str],
) -> dict[str, Any] | None:
    if not pipeline_ids and not status_ids:
        return None
    sources = repo.list_sync_sources(settings.account_key)
    best: tuple[int, dict[str, Any]] | None = None
    for source in sources:
        source_pipeline_ids = {str(value) for value in source.get("pipeline_ids", []) if str(value).strip()}
        source_status_ids = {str(value) for value in source.get("status_ids", []) if str(value).strip()}
        score = 0
        if pipeline_ids and source_pipeline_ids and pipeline_ids.issubset(source_pipeline_ids):
            score += 20
            if source_pipeline_ids == pipeline_ids:
                score += 10
        if status_ids and source_status_ids:
            matched = len(status_ids & source_status_ids)
            if matched:
                score += matched
            if status_ids.issubset(source_status_ids):
                score += 10
        if score and (best is None or score > best[0]):
            best = (score, source)
    return best[1] if best else None


def _enum_value_for_formula(repo: Repository, field_id: int, enum_id: str) -> Any:
    row = repo.conn.execute(
        """
        SELECT value_text, value_num, COUNT(*) AS items_count
        FROM entity_custom_field_values
        WHERE entity_type = 'leads'
          AND field_id = ?
          AND CAST(enum_id AS TEXT) = ?
        GROUP BY value_text, value_num
        ORDER BY items_count DESC
        LIMIT 1
        """,
        (field_id, str(enum_id)),
    ).fetchone()
    if not row:
        return str(enum_id)
    value_text = str(row["value_text"] or "").strip()
    value_num = row["value_num"]
    if value_num is not None and (not value_text or value_text.replace(",", ".") == str(value_num)):
        number = float(value_num)
        return int(number) if number.is_integer() else number
    return value_text or str(enum_id)


def _format_import_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if value is True:
        return ""
    return str(value)


def _normalize_amo_range_value(value: str, *, date_like: bool) -> Any:
    raw = str(value or "").strip()
    if not raw:
        return raw
    if date_like and raw.isdigit() and len(raw) >= 9:
        return datetime.fromtimestamp(int(raw), LOCAL_TZ).strftime("%Y-%m-%d")
    if raw.isdigit():
        return int(raw)
    return raw


def _amo_base_filter_field(raw_field: str) -> str | None:
    aliases = {
        "id": "id",
        "name": "name",
        "query": "name",
        "text": "name",
        "pipeline_id": "pipeline_id",
        "pipeline": "pipeline_id",
        "status_id": "status_id",
        "status": "status_id",
        "statuses": "status_id",
        "main_user": "responsible_user_id",
        "responsible_user_id": "responsible_user_id",
        "responsible": "responsible_user_id",
        "price": "price",
        "budget": "price",
        "created_at": "created_at",
        "date_create": "created_at",
        "created": "created_at",
        "updated_at": "updated_at",
        "date_modify": "updated_at",
        "modified_at": "updated_at",
        "closed_at": "closed_at",
        "date_close": "closed_at",
        "closed": "closed_at",
    }
    return aliases.get(str(raw_field or "").strip())


def _amo_field_is_date_like(field: str) -> bool:
    return field in {"created_at", "updated_at", "closed_at"} or field.startswith("cf_")


def _parse_amo_filter_url(repo: Repository, settings: Any, url: str) -> dict[str, Any]:
    parsed = urlparse(str(url or "").strip())
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not parsed.netloc or "amocrm" not in parsed.netloc:
        raise ValueError("Вставь ссылку на фильтр amoCRM")
    field_labels = _field_labels_for_formula(repo)
    pipeline_labels, status_index = _pipeline_status_index(repo)
    pipeline_ids: set[str] = set(re.findall(r"/pipeline/(\d+)", parsed.path or ""))
    status_ids_by_pipeline: dict[str, set[str]] = {}
    conditions: list[dict[str, Any]] = []
    summary: list[str] = []
    ignored: list[str] = []

    date_preset_ops = {
        "current_month": "this_month",
        "this_month": "this_month",
        "last_month": "previous_month",
        "previous_month": "previous_month",
        "current_week": "this_week",
        "this_week": "this_week",
        "last_week": "previous_week",
        "previous_week": "previous_week",
    }
    op_labels = {
        "eq": "равно",
        "in": "в списке",
        "gte": "от",
        "lte": "до",
        "between": "между",
        "date_between": "между датами",
        "this_month": "текущий месяц",
        "previous_month": "прошлый месяц",
        "this_week": "текущая неделя",
        "previous_week": "прошлая неделя",
    }
    processed_range_keys: set[str] = set()
    processed_value_keys: set[str] = set()
    grouped_cf_values: dict[str, list[str]] = {}
    grouped_base_values: dict[str, list[str]] = {}

    def append_condition(field: str, op: str, value: Any) -> None:
        conditions.append({"field": field, "op": op, "value": value})
        field_label = field_labels.get(field, field)
        value_label = _format_import_value(value)
        summary.append(f"{field_label}: {op_labels.get(op, op)}" + (f" {value_label}" if value_label else ""))

    def append_date_preset_condition(field: str, preset: str) -> bool:
        op = date_preset_ops.get(preset)
        if op:
            append_condition(field, op, True)
            return True
        today = datetime.now(LOCAL_TZ).date()
        if preset in {"current_day", "today"}:
            value = today.strftime("%Y-%m-%d")
        elif preset in {"previous_day", "last_day", "yesterday"}:
            value = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            return False
        append_condition(field, "date_between", [value, value])
        return True

    def append_range_condition(field: str, from_value: str, to_value: str) -> None:
        field_label = field_labels.get(field, field).casefold()
        raw_values = [str(item or "").strip() for item in (from_value, to_value)]
        date_like = (
            field in {"created_at", "updated_at", "closed_at"}
            or "дата" in field_label
            or any(re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", item) for item in raw_values if item)
            or any(item.isdigit() and len(item) >= 9 for item in raw_values if item)
        )
        start = _normalize_amo_range_value(from_value, date_like=date_like)
        end = _normalize_amo_range_value(to_value, date_like=date_like)
        if start not in ("", None) and end not in ("", None):
            append_condition(field, "date_between" if date_like else "between", [start, end])
        elif start not in ("", None):
            append_condition(field, "gte", start)
        elif end not in ("", None):
            append_condition(field, "lte", end)

    for key, raw_values in query.items():
        values = [value for value in raw_values if str(value).strip()]
        if not values:
            continue
        cf_values = re.fullmatch(r"filter\[cf\]\[(\d+)\]\[(?:\d*)\]", key)
        if cf_values:
            grouped_cf_values.setdefault(cf_values.group(1), []).extend(values)
            processed_value_keys.add(key)
            continue
        base_values = re.fullmatch(r"filter\[([a-zA-Z0-9_]+)\]\[(?:\d*)\]", key)
        if base_values and _amo_base_filter_field(base_values.group(1)):
            grouped_base_values.setdefault(base_values.group(1), []).extend(values)
            processed_value_keys.add(key)

    for key, values in query.items():
        cf_range = re.fullmatch(r"filter\[cf\]\[(\d+)\]\[(from|to)\]", key)
        base_range = re.fullmatch(r"filter\[([a-zA-Z0-9_]+)\]\[(from|to)\]", key)
        if cf_range:
            field = f"cf_{cf_range.group(1)}"
            from_key = f"filter[cf][{cf_range.group(1)}][from]"
            to_key = f"filter[cf][{cf_range.group(1)}][to]"
            if key == from_key or from_key not in query:
                append_range_condition(field, (query.get(from_key) or [""])[-1], (query.get(to_key) or [""])[-1])
            processed_range_keys.update({from_key, to_key})
        elif base_range:
            field = _amo_base_filter_field(base_range.group(1))
            if field:
                from_key = f"filter[{base_range.group(1)}][from]"
                to_key = f"filter[{base_range.group(1)}][to]"
                if key == from_key or from_key not in query:
                    append_range_condition(field, (query.get(from_key) or [""])[-1], (query.get(to_key) or [""])[-1])
                processed_range_keys.update({from_key, to_key})

    for key, values in query.items():
        values = [value for value in values if str(value).strip()]
        if key in {"useFilter", "filter_date_switch"} or key in processed_range_keys or key in processed_value_keys:
            continue
        cf_date = re.fullmatch(r"filter\[cf\]\[(\d+)\]\[date_preset\]", key)
        if cf_date:
            field = f"cf_{cf_date.group(1)}"
            preset = (values[-1] if values else "").strip()
            if not append_date_preset_condition(field, preset):
                ignored.append(f"{field_labels.get(field, field)}: неизвестный период {preset}")
            continue
        pipe_values = re.fullmatch(r"filter\[pipe\]\[(\d+)\]\[(?:\d*)\]", key)
        if pipe_values:
            pipeline_id = pipe_values.group(1)
            pipeline_ids.add(pipeline_id)
            status_ids_by_pipeline.setdefault(pipeline_id, set()).update(values)
            status_names = [status_index.get(str(value), {}).get("name") or str(value) for value in values]
            summary.append(f"Воронка {pipeline_labels.get(pipeline_id, pipeline_id)}: {', '.join(status_names[:8])}")
            continue
        if key == "filter[date_preset]":
            date_switch = str((query.get("filter_date_switch") or ["created"])[-1] or "created").strip()
            date_field = {
                "created": "created_at",
                "create": "created_at",
                "created_at": "created_at",
                "updated": "updated_at",
                "update": "updated_at",
                "modified": "updated_at",
                "modify": "updated_at",
                "updated_at": "updated_at",
                "closed": "closed_at",
                "close": "closed_at",
                "closed_at": "closed_at",
            }.get(date_switch, "created_at")
            preset = (values[-1] if values else "").strip()
            if not append_date_preset_condition(date_field, preset):
                ignored.append(f"{field_labels.get(date_field, date_field)}: неизвестный период {preset}")
            continue
        base_date = re.fullmatch(r"filter\[([a-zA-Z0-9_]+)\]\[date_preset\]", key)
        if base_date:
            field = _amo_base_filter_field(base_date.group(1))
            preset = (values[-1] if values else "").strip()
            if not field or not append_date_preset_condition(field, preset):
                ignored.append(f"{key}: неизвестный период {preset}")
            continue
        base_plain = re.fullmatch(r"filter\[([a-zA-Z0-9_]+)\]", key)
        if base_plain:
            field = _amo_base_filter_field(base_plain.group(1))
            if not field:
                ignored.append(key)
                continue
            value = _normalize_amo_range_value(values[-1] if values else "", date_like=_amo_field_is_date_like(field))
            append_condition(field, "eq", value)
            continue
        ignored.append(key)

    for field_id_raw, values in grouped_cf_values.items():
        field_id = int(field_id_raw)
        field = f"cf_{field_id}"
        parsed_values = [_enum_value_for_formula(repo, field_id, value) for value in values]
        if not parsed_values:
            continue
        value: Any = parsed_values[0] if len(parsed_values) == 1 else parsed_values
        op = "eq" if len(parsed_values) == 1 else "in"
        append_condition(field, op, value)

    for raw_field, values in grouped_base_values.items():
        field = _amo_base_filter_field(raw_field)
        if not field:
            continue
        parsed_values = [_normalize_amo_range_value(value, date_like=_amo_field_is_date_like(field)) for value in values]
        value = parsed_values[0] if len(parsed_values) == 1 else parsed_values
        op = "eq" if len(parsed_values) == 1 else "in"
        append_condition(field, op, value)

    all_status_ids = {status_id for values in status_ids_by_pipeline.values() for status_id in values}
    source = _source_for_amo_pipeline(repo, settings, pipeline_ids, all_status_ids)
    if source:
        source_status_ids = {str(value) for value in source.get("status_ids", []) if str(value).strip()}
        source_pipeline_ids = {str(value) for value in source.get("pipeline_ids", []) if str(value).strip()}
        if source_pipeline_ids:
            pipeline_ids -= source_pipeline_ids
        if source_status_ids and all_status_ids.issubset(source_status_ids):
            all_status_ids = set()
    if all_status_ids:
        status_values = [int(value) if str(value).isdigit() else value for value in sorted(all_status_ids)]
        conditions.append({"field": "status_id", "op": "in", "value": status_values})
    elif pipeline_ids:
        pipeline_values = [int(value) if str(value).isdigit() else value for value in sorted(pipeline_ids)]
        conditions.append({
            "field": "pipeline_id",
            "op": "in" if len(pipeline_values) > 1 else "eq",
            "value": pipeline_values if len(pipeline_values) > 1 else pipeline_values[0],
        })

    for condition in conditions:
        field = str(condition.get("field") or "")
        op = str(condition.get("op") or "eq")
        value = _format_import_value(condition.get("value"))
        op_label = op_labels.get(op, op)
        condition["label"] = f"{field_labels.get(field, field)}: {op_label}" + (f" {value}" if value else "")

    return {
        "source": {
            "id": int(source["id"]),
            "name": str(source.get("name") or source.get("id")),
        } if source else None,
        "conditions": conditions,
        "summary": summary,
        "ignored": ignored,
        "formula_patch": {
            "source_id": int(source["id"]) if source else None,
            "conditions": conditions,
        },
    }


def _formula_amo_filter_url(
    repo: Repository,
    settings: Any,
    widget: dict[str, Any],
    row_key: str,
    column: str,
) -> str:
    if not settings.account_base_url:
        return ""
    node = _first_aggregate_node(_formula_cell_node(widget, column))
    if not node:
        return ""
    source_id = int(node.get("source_id") or widget.get("query", {}).get("source_id") or 0) or None
    parts: list[str] = []
    seen: set[str] = set()
    for condition in node.get("where") or node.get("filters") or []:
        if not isinstance(condition, dict):
            continue
        for part in _condition_to_amo_filter(repo, condition):
            if part not in seen:
                seen.add(part)
                parts.append(part)
    group_by = node.get("group_by")
    if isinstance(group_by, str) and group_by.startswith("cf_") and row_key:
        part_conditions = _condition_to_amo_filter(repo, {"field": group_by, "op": "eq", "value": row_key})
        field_prefix = f"filter%5Bcf%5D%5B{group_by.removeprefix('cf_')}%5D"
        parts = [part for part in parts if not part.startswith(field_prefix)]
        parts.extend(part for part in part_conditions if part not in parts)
    if not parts:
        return ""
    pipeline_ids = _source_pipeline_ids(repo, settings, source_id)
    status_parts = _pipeline_status_filter_parts(repo, pipeline_ids[0]) if len(pipeline_ids) == 1 else []
    for part in status_parts:
        if part not in parts:
            parts.append(part)
    parts.append("useFilter=y")
    path = "/leads/list/" if status_parts else _source_pipeline_path(repo, settings, source_id)
    return f"{settings.account_base_url}{path}?{'&'.join(parts)}"


def _load_drilldown_payload(settings: Any, widget_id: str, row_key: str, column: str) -> dict[str, Any]:
    widgets = load_widgets(settings.db_path)
    widget = next((item for item in widgets if str(item.get("id") or "") == str(widget_id)), None)
    if not widget:
        return {"ok": False, "error": "Виджет не найден"}
    cache = load_widget_results_cache(settings.db_path)
    cached = cache.get(str(widget_id)) if isinstance(cache, dict) else None
    if not isinstance(cached, dict):
        return {"ok": False, "error": "У виджета еще нет сохраненного результата. Обнови дашборд."}
    formula_result = cached.get("formula_result")
    if not isinstance(formula_result, dict):
        return {"ok": False, "error": "Расшифровка пока доступна только для формульных показателей"}
    trace = _find_formula_drilldown(formula_result, row_key, column)
    if not trace:
        return {"ok": False, "error": "Для этой ячейки пока нет списка сделок. Обнови виджет и попробуй еще раз."}
    entity_type = str(trace.get("entity_type") or "leads")
    entity_ids = [str(item) for item in (trace.get("entity_ids") or []) if str(item or "").strip()]
    if not entity_ids:
        return {"ok": False, "error": "В этой ячейке нет сделок для расшифровки"}

    repo = _repo(settings)
    user_labels = _drilldown_user_labels(repo)
    pipeline_labels, status_labels = _drilldown_pipeline_labels(repo)
    placeholders = ", ".join("?" for _ in entity_ids)
    rows = repo.conn.execute(
        f"""
        SELECT entity_id, name, payload_json, updated_at, synced_at
        FROM raw_entities
        WHERE entity_type = ?
          AND entity_id IN ({placeholders})
        ORDER BY CAST(entity_id AS INTEGER) DESC
        """,
        [entity_type, *entity_ids],
    ).fetchall()
    entities = []
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        entity_id = str(row["entity_id"] or "")
        status_id = str(payload.get("status_id") or "")
        pipeline_id = str(payload.get("pipeline_id") or "")
        responsible_id = str(payload.get("responsible_user_id") or "")
        entities.append(
            {
                "id": entity_id,
                "name": row["name"] or payload.get("name") or f"Сделка {entity_id}",
                "price": payload.get("price") or payload.get("budget") or 0,
                "status_id": status_id,
                "status_name": status_labels.get(status_id, status_id),
                "pipeline_id": pipeline_id,
                "pipeline_name": pipeline_labels.get(pipeline_id, pipeline_id),
                "responsible_user_id": responsible_id,
                "responsible_name": user_labels.get(responsible_id, responsible_id),
                "created_at": payload.get("created_at") or "",
                "updated_at": _format_drilldown_datetime(payload.get("updated_at") or row["updated_at"] or ""),
                "synced_at": row["synced_at"] or "",
                "url": f"{settings.account_base_url}/leads/detail/{quote(entity_id)}" if entity_type == "leads" and settings.account_base_url else "",
            }
        )
    return {
        "ok": True,
        "widget": widget,
        "row_key": row_key,
        "column": column,
        "entity_type": entity_type,
        "total": int(trace.get("total") or len(entity_ids)),
        "shown": len(entities),
        "truncated": bool(trace.get("truncated")),
        "amo_filter_url": (
            _formula_amo_filter_url(repo, settings, widget, row_key, column)
            or _amo_leads_filter_url(settings, entity_ids)
            if entity_type == "leads"
            else ""
        ),
        "entities": entities,
    }


def _drilldown_html(settings: Any, query_string: str) -> str:
    query = parse_qs(query_string)
    widget_id = (query.get("widget_id") or [""])[0]
    row_key = (query.get("row_key") or [""])[0]
    column = (query.get("column") or [""])[0]
    payload = _load_drilldown_payload(settings, widget_id, row_key, column)
    suffix = f"user={quote(settings.user_key)}&account={quote(settings.account_key)}"
    back_url = f"/dashboard?{suffix}"
    if not payload.get("ok"):
        message = html.escape(str(payload.get("error") or "Не удалось открыть расшифровку"))
        return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Расшифровка показателя</title>{_drilldown_css()}</head>
<body><main class="page"><a class="back" href="{back_url}">← Дашборд</a><section class="card"><h1>Расшифровка показателя</h1><p class="error">{message}</p></section></main></body></html>"""

    widget = payload.get("widget") or {}
    filter_url = str(payload.get("amo_filter_url") or "")
    filter_button = (
        f'<a class="primary-button" href="{html.escape(filter_url)}" target="_blank" rel="noreferrer">Открыть все сделки в amoCRM</a>'
        if filter_url
        else ""
    )
    rows_html = ""
    for item in payload.get("entities") or []:
        link = item.get("url") or ""
        open_html = f'<a class="button-link" href="{html.escape(link)}" target="_blank" rel="noreferrer">Открыть в amoCRM</a>' if link else ""
        rows_html += f"""
          <tr>
            <td><strong>{html.escape(str(item.get("id") or ""))}</strong></td>
            <td>{html.escape(str(item.get("name") or ""))}</td>
            <td>{html.escape(str(item.get("price") or 0))}</td>
            <td>{html.escape(str(item.get("status_name") or ""))}</td>
            <td>{html.escape(str(item.get("responsible_name") or ""))}</td>
            <td>{html.escape(str(item.get("updated_at") or ""))}</td>
            <td>{open_html}</td>
          </tr>
        """
    note = ""
    if payload.get("truncated"):
        note = f"<p class=\"note\">Показаны первые {payload.get('shown')} сделок из {payload.get('total')}. В amoCRM-ссылку тоже попадают первые сделки из сохраненной расшифровки.</p>"
    elif int(payload.get("total") or 0) > 250:
        note = "<p class=\"note\">В amoCRM-ссылку добавлены первые 250 сделок, чтобы ссылка открывалась стабильно.</p>"
    return f"""<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>Расшифровка показателя</title>{_drilldown_css()}</head>
<body>
  <main class="page">
    <a class="back" href="{back_url}">← Дашборд</a>
    <section class="card">
      <div class="eyebrow">Проверка цифры</div>
      <h1>{html.escape(str(widget.get("title") or "Показатель"))}</h1>
      <div class="meta">
        <span>Строка: <strong>{html.escape(str(payload.get("row_key") or ""))}</strong></span>
        <span>Колонка: <strong>{html.escape(str(payload.get("column") or ""))}</strong></span>
        <span>Сделок: <strong>{payload.get("total")}</strong></span>
      </div>
      <div class="drilldown-actions">{filter_button}</div>
      {note}
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>Название</th><th>Сумма</th><th>Этап</th><th>Ответственный</th><th>Обновлено</th><th></th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </section>
  </main>
</body>
</html>"""


def _drilldown_css() -> str:
    return """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600&display=swap');
  * { box-sizing: border-box; }
  body { margin: 0; font-family: Montserrat, Arial, sans-serif; background: #f3f7fb; color: #223047; font-weight: 400; }
  .page { max-width: 1440px; margin: 0 auto; padding: 32px; }
  .back { display: inline-flex; margin-bottom: 18px; color: #1677c7; font-weight: 500; text-decoration: none; }
  .card { background: #fff; border: 1px solid #d9e7f5; border-radius: 18px; box-shadow: 0 14px 36px rgba(41,73,112,.06); padding: 26px; }
  .eyebrow { color: #7f91a8; font-size: 12px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; }
  h1 { margin: 8px 0 14px; font-size: 28px; font-weight: 600; }
  .meta { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 18px; color: #59708c; }
  .meta span { padding: 8px 10px; border: 1px solid #d8e8ff; border-radius: 12px; background: #f6faff; }
  .drilldown-actions { display: flex; flex-wrap: wrap; gap: 10px; margin: 0 0 18px; }
  .primary-button { display: inline-flex; align-items: center; justify-content: center; min-height: 42px; padding: 10px 14px; border: 1px solid #acd6f8; border-radius: 12px; background: #dff0ff; color: #155f9d; font-weight: 600; text-decoration: none; }
  .primary-button:hover { background: #d2eaff; }
  .note { padding: 12px 14px; border-radius: 14px; background: #fff7df; color: #7a4c00; font-weight: 500; }
  .error { color: #dc2626; font-weight: 600; }
  .table-wrap { overflow: auto; border: 1px solid #d9e7f5; border-radius: 16px; }
  table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 14px; }
  th, td { padding: 10px 12px; border-bottom: 1px solid #edf2f8; border-right: 1px solid #eef4fb; text-align: left; white-space: nowrap; }
  th { position: sticky; top: 0; background: #f4f8fd; color: #7f91a8; font-size: 11px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; }
  tr:nth-child(even) td { background: #fbfdff; }
  .button-link { display: inline-flex; padding: 8px 10px; border: 1px solid #c8e4fb; border-radius: 10px; background: #eef7ff; color: #2577b8; font-weight: 500; text-decoration: none; }
</style>
"""


def _analytics_freshness(repo: Repository, account_key: str, query: AnalyticsQuery) -> dict[str, Any]:
    if query.source_id:
        source = repo.get_sync_source(query.source_id, account_key)
        if not source:
            return {
                "scope": "source",
                "source_id": query.source_id,
                "source_name": "",
                "fresh_at": None,
                "count": 0,
            }
        row = repo.conn.execute(
            """
            SELECT COUNT(*) AS items_count, MAX(synced_at) AS fresh_at
            FROM sync_source_entities
            WHERE source_id = ? AND entity_type = ?
            """,
            (query.source_id, query.entity),
        ).fetchone()
        fresh_at = row["fresh_at"] if row else None
        return {
            "scope": "source",
            "source_id": int(source["id"]),
            "source_name": str(source.get("name") or ""),
            "fresh_at": fresh_at
            or source.get("linked_synced_at")
            or source.get("last_job_finished_at")
            or source.get("last_job_started_at")
            or source.get("updated_at"),
            "checked_at": source.get("source_checked_at") or source.get("updated_at"),
            "hub_fresh_at": source.get("hub_leads_synced_at"),
            "count": int(row["items_count"] or 0) if row else 0,
            "last_job_status": source.get("last_job_status"),
        }

    row = repo.conn.execute(
        """
        SELECT COUNT(*) AS items_count, MAX(synced_at) AS fresh_at
        FROM raw_entities
        WHERE entity_type = ?
        """,
        (query.entity,),
    ).fetchone()
    return {
        "scope": "hub",
        "source_id": None,
        "source_name": "Весь хаб",
        "fresh_at": row["fresh_at"] if row else None,
        "count": int(row["items_count"] or 0) if row else 0,
    }


def _sync_entities(entities: list[str], settings: Any | None = None) -> list[dict[str, Any]]:
    allowed = {entity for entity, _label, _checked in SYNC_OPTIONS}
    unknown = [entity for entity in entities if entity not in allowed]
    if unknown:
        raise ValueError(f"Unknown entities: {', '.join(unknown)}")

    settings = settings or load_settings()
    repo = _repo(settings)
    repo.upsert_account(
        settings.account_key,
        subdomain=settings.subdomain or None,
        base_domain=settings.base_domain,
    )
    client = AmoCRMClient(settings)
    service = SyncService(client, repo)
    try:
        return service.sync_entities(entities)
    finally:
        client.close()


def _run_sync_job_background(
    user_key: str,
    account_key: str,
    job_id: int,
    job_type: str,
    entity_types: list[str],
    filters: dict[str, list[int]] | None = None,
    source_id: int | None = None,
) -> None:
    settings = load_settings(user_key=user_key, account_key=account_key)
    repo = _repo(settings)
    repo.upsert_account(
        settings.account_key,
        subdomain=settings.subdomain or None,
        base_domain=settings.base_domain,
    )
    client = AmoCRMClient(settings)
    try:
        service = SyncService(client, repo)
        service.run_existing_sync_job(
            job_id,
            settings.account_key,
            job_type,
            entity_types,
            filters=filters,
            source_id=source_id,
        )
    except Exception as exc:
        repo.finish_sync_job(job_id, "failed", error=str(exc))
    finally:
        client.close()
        with _SYNC_THREADS_LOCK:
            _SYNC_THREADS.pop(job_id, None)


def _start_sync_job_thread(
    user_key: str,
    account_key: str,
    job_id: int,
    job_type: str,
    entity_types: list[str],
    filters: dict[str, list[int]] | None = None,
    source_id: int | None = None,
) -> None:
    thread = threading.Thread(
        target=_run_sync_job_background,
        args=(user_key, account_key, job_id, job_type, entity_types, filters, source_id),
        name=f"sync-job-{job_id}",
        daemon=True,
    )
    with _SYNC_THREADS_LOCK:
        _SYNC_THREADS[job_id] = thread
    thread.start()


def _active_sync_job_ids() -> set[int]:
    with _SYNC_THREADS_LOCK:
        return set(_SYNC_THREADS)


def _active_account_sync_job(repo: Repository, account_key: str) -> dict[str, Any] | None:
    active_job_ids = _active_sync_job_ids()
    for job in repo.latest_sync_jobs(account_key, limit=20):
        if job["status"] in {"pending", "running"} and (
            not active_job_ids or int(job["id"]) in active_job_ids
        ):
            return job
    return None


def _cleanup_stale_runtime_state() -> dict[str, int]:
    result = {"jobs_interrupted": 0, "runs_interrupted": 0, "queue_reset": 0}
    for item in _background_account_items():
        settings = load_settings(user_key=item["user_key"], account_key=item["account_key"])
        repo = _repo(settings)
        result["jobs_interrupted"] += repo.interrupt_stale_sync_jobs(
            settings.account_key,
            active_job_ids=_active_sync_job_ids(),
            stale_after_minutes=1,
        )
        result["runs_interrupted"] += repo.interrupt_stale_sync_runs(stale_after_minutes=1)
        result["queue_reset"] += repo.reset_stale_sync_queue(settings.account_key, stale_after_minutes=10)
    return result


def _background_worker_snapshot(user_key: str | None = None, account_key: str | None = None) -> dict[str, Any]:
    with _QUEUE_WORKER_LOCK:
        snapshot = json.loads(json.dumps(_QUEUE_WORKER_STATE, ensure_ascii=False))
    thread = _QUEUE_WORKER_THREAD
    snapshot["thread_alive"] = bool(thread and thread.is_alive())
    if user_key and account_key:
        account_id = f"{user_key}/{account_key}"
        snapshot["account"] = snapshot.get("accounts", {}).get(account_id)
    return snapshot


def _conversation_run_summary(result: dict[str, Any], *, dry_run: bool) -> str:
    if result.get("baseline_set"):
        return "точка старта поставлена; старые звонки пропущены, дальше будут обрабатываться только новые"
    polled = result.get("polled") or {}
    call_events = int(polled.get("call_events") or 0)
    would_be = int(polled.get("would_be_eligible_conversations") or 0)
    eligible = int(result.get("eligible_conversations") or 0)
    visible_eligible = max(eligible, would_be) if dry_run else eligible
    if dry_run:
        return f"проверка: найдено свежих звонков {call_events}; по текущим фильтрам подходит {visible_eligible}"
    steps = ", ".join((result.get("steps") or {}).keys()) or "нет"
    return f"автообработка: найдено свежих звонков {call_events}; обработано новых разговоров {eligible}; шаги: {steps}"


def _record_background_account(account_id: str, patch: dict[str, Any]) -> None:
    with _QUEUE_WORKER_LOCK:
        accounts = _QUEUE_WORKER_STATE.setdefault("accounts", {})
        current = dict(accounts.get(account_id) or {})
        current.update(patch)
        accounts[account_id] = current


def _conversation_auto_due(account_id: str, now_iso: str) -> bool:
    with _QUEUE_WORKER_LOCK:
        account = (_QUEUE_WORKER_STATE.get("accounts") or {}).get(account_id) or {}
        conversation_auto = account.get("conversation_auto") or {}
        last_run_at = conversation_auto.get("last_run_at")
    if not last_run_at:
        return True
    try:
        last = datetime.fromisoformat(str(last_run_at))
        now = datetime.fromisoformat(now_iso)
    except ValueError:
        return True
    return (now - last).total_seconds() >= CONVERSATION_AUTO_INTERVAL_SECONDS


def _run_conversation_auto_background(
    settings: Any,
    repo: Repository,
    account_id: str,
    account_patch: dict[str, Any],
    run_result: dict[str, Any],
) -> None:
    if not _conversation_auto_due(account_id, str(run_result["ran_at"])):
        return
    raw_settings = load_account_settings(
        user_key=settings.user_key,
        account_key=settings.account_key,
        data_root=settings.data_root,
    )
    raw_ci = raw_settings.get("conversation_intelligence")
    if isinstance(raw_ci, dict) and raw_ci.get("enabled") is False:
        account_patch["conversation_auto"] = {
            "enabled": False,
            "last_checked_at": run_result["ran_at"],
            "summary": "Автообработка не включена в настройках аккаунта.",
        }
        return
    ci_settings = conversation_settings(raw_settings)
    if not ci_settings.get("enabled"):
        account_patch["conversation_auto"] = {
            "enabled": False,
            "last_checked_at": run_result["ran_at"],
        }
        return

    result = ConversationAutomationService(settings, repo).run(limit=25, dry_run=False)
    polled = result.get("polled") or {}
    steps = result.get("steps") or {}
    account_patch["conversation_auto"] = {
        "enabled": True,
        "last_run_at": run_result["ran_at"],
        "ok": bool(result.get("ok")),
        "baseline_set": bool(result.get("baseline_set")),
        "call_events": int(polled.get("call_events") or 0),
        "eligible_conversations": int(result.get("eligible_conversations") or 0),
        "conversation_ids": result.get("conversation_ids") or [],
        "steps": {key: value for key, value in steps.items()},
        "summary": _conversation_run_summary(result, dry_run=False),
    }
    run_result["conversation_auto"]["accounts"] += 1
    run_result["conversation_auto"]["call_events"] += int(polled.get("call_events") or 0)
    run_result["conversation_auto"]["eligible_conversations"] += int(result.get("eligible_conversations") or 0)


def _run_auto_sync_scheduler(
    settings: Any,
    repo: Repository,
    account_id: str,
    account_patch: dict[str, Any],
    run_result: dict[str, Any],
) -> None:
    raw_settings = load_account_settings(
        user_key=settings.user_key,
        account_key=settings.account_key,
        data_root=settings.data_root,
    )
    config = normalize_auto_sync_settings(raw_settings.get("auto_sync"))
    account_patch["auto_sync"] = {
        "enabled": bool(config.get("enabled")),
        "last_error": config.get("last_error"),
    }
    run_result.setdefault("auto_sync", {"checked": 0, "started": 0, "skipped_active": 0, "errors": 0})
    run_result["auto_sync"]["checked"] += 1
    if not bool(config.get("enabled")):
        return

    active_job = _active_account_sync_job(repo, settings.account_key)
    if active_job:
        run_result["auto_sync"]["skipped_active"] += 1
        account_patch["auto_sync"].update({
            "skipped": "active_job",
            "active_job_id": active_job["id"],
            "active_job_type": active_job["job_type"],
        })
        return

    group = next_due_group(config)
    if not group:
        account_patch["auto_sync"]["skipped"] = "not_due"
        return

    try:
        entities = [str(entity) for entity in group.get("entities") or [] if str(entity).strip()]
        if not entities:
            account_patch["auto_sync"]["skipped"] = "empty_group"
            return
        repo.upsert_account(
            settings.account_key,
            subdomain=settings.subdomain or None,
            base_domain=settings.base_domain,
        )
        job_id = repo.start_sync_job(
            settings.account_key,
            f"auto_{group.get('name') or 'sync'}",
            entities,
            status="pending",
        )
        next_config = mark_group_started(config, str(group.get("name") or "sync"), job_id)
        raw_settings["auto_sync"] = next_config
        save_account_settings(
            user_key=settings.user_key,
            account_key=settings.account_key,
            settings=raw_settings,
            data_root=settings.data_root,
        )
        _start_sync_job_thread(
            settings.user_key,
            settings.account_key,
            job_id,
            f"auto_{group.get('name') or 'sync'}",
            entities,
        )
        run_result["auto_sync"]["started"] += 1
        account_patch["auto_sync"].update({
            "started": True,
            "job_id": job_id,
            "group": group.get("name"),
            "label": group.get("label"),
            "entities": entities,
            "status_url": f"/api/sync/jobs/{job_id}?user={settings.user_key}&account={settings.account_key}",
        })
    except Exception as exc:
        message = str(exc)
        run_result["auto_sync"]["errors"] += 1
        run_result["errors"].append({"account": account_id, "error": f"auto sync: {message}"})
        raw_settings["auto_sync"] = mark_auto_sync_error(config, message)
        save_account_settings(
            user_key=settings.user_key,
            account_key=settings.account_key,
            settings=raw_settings,
            data_root=settings.data_root,
        )
        account_patch["auto_sync"].update({"error": message})


def _run_queue_worker_once(limit_per_account: int = 50) -> dict[str, Any]:
    with _QUEUE_WORKER_LOCK:
        cleanup_due = int(_QUEUE_WORKER_STATE.get("runs") or 0) % 120 == 0
    run_result: dict[str, Any] = {
        "ran_at": utc_now(),
        "accounts": 0,
        "processed": 0,
        "failed": 0,
        "reset": 0,
        "cleanup": {},
        "conversation_auto": {
            "accounts": 0,
            "call_events": 0,
            "eligible_conversations": 0,
        },
        "auto_sync": {
            "checked": 0,
            "started": 0,
            "skipped_active": 0,
            "errors": 0,
        },
        "errors": [],
    }
    for item in list_connections(include_metrics=False):
        user_key = item["user_key"]
        account_key = item["account_key"]
        account_id = f"{user_key}/{account_key}"
        settings = load_settings(user_key=user_key, account_key=account_key)
        repo = _repo(settings)
        reset_count = repo.reset_stale_sync_queue(settings.account_key, stale_after_minutes=10)
        run_result["reset"] += reset_count
        cleanup_result = repo.cleanup_operational_rows(settings.account_key) if cleanup_due else None
        if cleanup_result:
            run_result["cleanup"][account_id] = cleanup_result
        queue_counts = repo.queue_status_counts(settings.account_key)
        pending_count = int(queue_counts.get("pending", 0))
        account_patch: dict[str, Any] = {
            "last_run_at": run_result["ran_at"],
            "queue": queue_counts,
            "reset": reset_count,
            "cleanup": cleanup_result,
            "processed": 0,
            "failed": 0,
            "error": None,
        }
        if pending_count > 0:
            run_result["accounts"] += 1
            client: AmoCRMClient | None = None
            try:
                client = AmoCRMClient(settings)
                service = SyncService(client, repo)
                result = service.process_queue(settings.account_key, limit=limit_per_account)
                processed = int(result.get("processed") or 0)
                failed = int(result.get("failed") or 0)
                mart_result = None
                kpi_result = None
                if processed > 0:
                    mart_result = ActivityService(repo).rebuild_marts_for_day(slot_minutes=15)
                    kpi_result = KpiService(repo).rebuild_daily()
                    dashboard_refresh_started = _start_dashboard_background_refresh(settings)
                else:
                    dashboard_refresh_started = False
                run_result["processed"] += processed
                run_result["failed"] += failed
                account_patch.update({
                    "processed": processed,
                    "failed": failed,
                    "result": result,
                    "activity_mart": mart_result,
                    "lead_kpi": kpi_result,
                    "dashboard_refresh_started": dashboard_refresh_started,
                })
            except Exception as exc:
                message = str(exc)
                run_result["failed"] += 1
                run_result["errors"].append({"account": account_id, "error": message})
                account_patch.update({"failed": 1, "error": message})
            finally:
                if client:
                    client.close()
        try:
            _run_auto_sync_scheduler(settings, repo, account_id, account_patch, run_result)
        except Exception as exc:
            message = str(exc)
            run_result["failed"] += 1
            run_result["errors"].append({"account": account_id, "error": f"auto sync scheduler: {message}"})
            account_patch["auto_sync"] = {
                "enabled": True,
                "error": message,
            }
        try:
            _run_conversation_auto_background(settings, repo, account_id, account_patch, run_result)
        except Exception as exc:
            message = str(exc)
            run_result["failed"] += 1
            run_result["errors"].append({"account": account_id, "error": f"conversation auto: {message}"})
            account_patch["conversation_auto"] = {
                "enabled": True,
                "last_run_at": run_result["ran_at"],
                "ok": False,
                "error": message,
            }
        _record_background_account(account_id, account_patch)
    with _QUEUE_WORKER_LOCK:
        _QUEUE_WORKER_STATE["last_run_at"] = run_result["ran_at"]
        _QUEUE_WORKER_STATE["runs"] = int(_QUEUE_WORKER_STATE.get("runs") or 0) + 1
        _QUEUE_WORKER_STATE["processed"] = int(_QUEUE_WORKER_STATE.get("processed") or 0) + int(run_result["processed"])
        _QUEUE_WORKER_STATE["failed"] = int(_QUEUE_WORKER_STATE.get("failed") or 0) + int(run_result["failed"])
        _QUEUE_WORKER_STATE["last_error"] = run_result["errors"][-1]["error"] if run_result["errors"] else None
    return run_result


def _queue_worker_loop(interval_seconds: int = 30, limit_per_account: int = 50) -> None:
    while not _QUEUE_WORKER_STOP.is_set():
        try:
            _run_queue_worker_once(limit_per_account=limit_per_account)
        except Exception as exc:
            with _QUEUE_WORKER_LOCK:
                _QUEUE_WORKER_STATE["last_error"] = str(exc)
        _QUEUE_WORKER_STOP.wait(interval_seconds)


def _start_queue_worker() -> None:
    global _QUEUE_WORKER_THREAD
    if _QUEUE_WORKER_THREAD and _QUEUE_WORKER_THREAD.is_alive():
        return
    _QUEUE_WORKER_STOP.clear()
    with _QUEUE_WORKER_LOCK:
        _QUEUE_WORKER_STATE["enabled"] = True
        _QUEUE_WORKER_STATE["started_at"] = utc_now()
        _QUEUE_WORKER_STATE["last_error"] = None
    _QUEUE_WORKER_THREAD = threading.Thread(
        target=_queue_worker_loop,
        name="sync-queue-worker",
        daemon=True,
    )
    _QUEUE_WORKER_THREAD.start()


def _save_oauth_callback(query: dict[str, list[str]], settings: Any | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    path = settings.db_path.parent / "oauth_callback.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: values[-1] if values else "" for key, values in query.items()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(path), "payload": payload}


def _flatten_form(parsed: dict[str, list[str]]) -> dict[str, Any]:
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _int_list(value: Any) -> list[int]:
    if value is None or value == "":
        return []
    raw_items = value if isinstance(value, list) else [value]
    result: list[int] = []
    for item in raw_items:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number not in result:
            result.append(number)
    return result


def _extract_webhook_items(payload: dict[str, Any]) -> list[dict[str, str]]:
    items: dict[tuple[str, str, str], dict[str, str]] = {}
    pattern = re.compile(r"^(?P<group>[a-z_]+)\[(?P<event>[a-z_]+)]\[(?P<index>\d+)]\[id]$")
    for key, value in payload.items():
        match = pattern.match(key)
        if not match:
            continue
        entity_type = WEBHOOK_ENTITY_MAP.get(match.group("group"), match.group("group"))
        entity_id = str(value)
        event_type = f"{match.group('event')}_{entity_type.rstrip('s')}"
        items[(event_type, entity_type, entity_id)] = {
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
        }
    return list(items.values())


def _delete_event(event_type: str) -> bool:
    return event_type.startswith("delete_")


def _page_shell(title: str, body: str) -> str:
    return f"""
    <!doctype html>
    <html lang="ru">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{html.escape(title)}</title>
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600&display=swap" rel="stylesheet">
      <style>
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          background: #f3f7fb;
          color: #223047;
          font: 14px/1.48 Montserrat, Arial, sans-serif;
          font-weight: 400;
        }}
        .shell {{ width: min(1440px, calc(100% - 48px)); margin: 0 auto; padding: 28px 0 56px; }}
        .top-nav {{ display: inline-flex; gap: 8px; padding: 6px; margin-bottom: 18px; border: 1px solid #d9e7f5; border-radius: 16px; background: #fff; }}
        .top-nav a {{ min-height: 38px; padding: 9px 16px; border: 1px solid transparent; border-radius: 12px; color: #607089; font-weight: 500; text-decoration: none; }}
        .top-nav a.active {{ background: #e8f4ff; border-color: #b9defb; color: #1677c7; }}
        .panel {{ padding: 22px; border: 1px solid #d9e7f5; border-radius: 18px; background: #fff; box-shadow: 0 14px 36px rgba(41,73,112,.06); margin-bottom: 16px; }}
        .panel-header {{ display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 12px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }}
        h1 {{ margin: 0 0 6px; font-size: 34px; font-weight: 600; color: #1e2b3f; }}
        h2 {{ margin: 0 0 12px; font-size: 20px; font-weight: 600; color: #1e2b3f; }}
        h3, h4, strong {{ font-weight: 600; }}
        p {{ color: #607089; }}
        .table-wrap {{ width: 100%; overflow-x: auto; padding-bottom: 2px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        .admin-table {{ min-width: 1180px; table-layout: fixed; }}
        th, td {{ padding: 12px 10px; border-bottom: 1px solid #edf3f8; text-align: left; vertical-align: top; }}
        th {{ color: #7f91a8; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .08em; }}
        td {{ overflow-wrap: anywhere; }}
        code {{ padding: 3px 6px; border-radius: 8px; background: #f1f6fb; }}
        .db-code {{ display: block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .queue-text {{ line-height: 1.65; white-space: nowrap; }}
        .error-cell {{ color: #52637a; }}
        input, select, textarea {{ width: 100%; min-height: 38px; padding: 8px 10px; border: 1px solid #d5e4f2; border-radius: 10px; font: inherit; color: #223047; background: #fff; }}
        textarea {{ min-height: 180px; line-height: 1.45; resize: vertical; }}
        label {{ display: grid; gap: 6px; color: #607089; font-weight: 500; }}
        button, .button {{ display: inline-flex; align-items: center; justify-content: center; min-height: 40px; padding: 0 14px; border: 1px solid #6bbff2; border-radius: 12px; background: #2f9fe5; color: #fff; font-weight: 600; font-family: inherit; text-decoration: none; cursor: pointer; white-space: nowrap; box-shadow: 0 6px 14px rgba(47,159,229,.16); }}
        button:hover, .button:hover {{ background: #258fd2; border-color: #55aee6; }}
        button:disabled, .button:disabled {{ opacity: .62; cursor: default; box-shadow: none; }}
        .muted {{ color: #607089; }}
        .actions {{ display: flex; flex-wrap: wrap; gap: 8px; }}
        .account-hero {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 18px; align-items: center; }}
        .hero-kicker {{ margin: 0 0 8px; color: #7f91a8; font-size: 12px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; }}
        .hero-actions {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }}
        .status-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 16px; }}
        .status-card {{ min-height: 148px; padding: 18px; border: 1px solid #d9e7f5; border-radius: 16px; background: #fff; box-shadow: 0 10px 28px rgba(41,73,112,.045); }}
        .status-card span {{ display: block; margin-bottom: 8px; color: #7f91a8; font-size: 11px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; }}
        .status-card strong {{ display: block; margin-bottom: 10px; font-size: 28px; line-height: 1; }}
        .status-card p {{ margin: 0; }}
        .flow-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
        .flow-card {{ padding: 14px; border: 1px solid #edf2f7; border-radius: 14px; background: #fbfdff; }}
        .flow-card strong {{ display: block; margin-bottom: 6px; font-size: 16px; }}
        .flow-card p {{ margin: 0; }}
        .webhook-box {{ margin-top: 14px; padding: 12px 14px; border: 1px solid #dbe4ee; border-radius: 14px; background: #f8fafc; color: #52637a; }}
        .webhook-box code {{ display: inline-block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; vertical-align: bottom; }}
        .technical-panel summary {{ cursor: pointer; font-size: 20px; font-weight: 600; }}
        .technical-panel summary + * {{ margin-top: 14px; }}
        .job-status {{ margin-top: 14px; padding: 12px 14px; border: 1px solid #dbe4ee; border-radius: 14px; background: #f8fafc; color: #52637a; }}
        .job-status strong {{ color: #223047; }}
        .job-progress {{ height: 8px; margin-top: 10px; overflow: hidden; border-radius: 999px; background: #e5edf5; }}
        .job-progress span {{ display: block; height: 100%; width: 0%; border-radius: inherit; background: #0f8f72; transition: width .2s ease; }}
        .sync-head {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }}
        .sync-percent {{ font-size: 18px; font-weight: 600; color: #223047; }}
        .sync-meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
        .sync-pill {{ display: inline-flex; align-items: center; min-height: 28px; padding: 0 10px; border-radius: 999px; background: #fff; border: 1px solid #dbe4ee; color: #52637a; font-weight: 500; }}
        .sync-log {{ margin-top: 12px; display: grid; gap: 6px; }}
        .sync-log-row {{ display: grid; grid-template-columns: 118px minmax(160px, 1fr) minmax(120px, .8fr) 90px; gap: 10px; align-items: center; padding: 8px 10px; border-radius: 10px; background: #fff; border: 1px solid #edf2f7; }}
        .sync-log-row strong {{ color: #223047; }}
        .sync-log-row .muted {{ color: #66758a; }}
        .sync-log-row.error {{ border-color: #ffd6de; background: #fff8fa; }}
        .sync-log-row.waiting {{ color: #66758a; }}
        .source-filters {{ display: grid; gap: 10px; margin: 14px 0; }}
        .source-filter-block {{ border: 1px solid #dbe4ee; border-radius: 14px; background: #f8fafc; }}
        .source-filter-block summary {{ cursor: pointer; padding: 12px 14px; font-weight: 600; }}
        .source-statuses {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; padding: 0 14px 14px; }}
        .source-statuses label, .source-filter-block summary label {{ display: flex; grid-template-columns: none; align-items: center; gap: 8px; color: #223047; }}
        .source-statuses input, .source-filter-block input {{ width: auto; min-height: auto; }}
        .source-summary {{ margin: 12px 0 4px; padding: 12px 14px; border: 1px solid #dbe4ee; border-radius: 14px; background: #f8fafc; color: #52637a; font-weight: 500; }}
        .source-card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; margin-top: 16px; }}
        .source-card {{ display: grid; gap: 14px; padding: 16px; border: 1px solid #dbe4ee; border-radius: 16px; background: #fbfdff; }}
        .source-card-head {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }}
        .source-card h3 {{ margin: 0; font-size: 18px; }}
        .source-pill {{ display: inline-flex; align-items: center; min-height: 28px; padding: 0 10px; border: 1px solid #dbe4ee; border-radius: 999px; background: #fff; color: #52637a; font-weight: 500; white-space: nowrap; }}
        .source-meta {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
        .source-meta div {{ padding: 10px; border-radius: 12px; background: #fff; border: 1px solid #edf2f7; }}
        .source-meta span {{ display: block; color: #7f91a8; font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; }}
        .source-list {{ display: grid; gap: 6px; color: #52637a; }}
        .source-actions {{ display: flex; flex-wrap: wrap; gap: 8px; }}
        .secondary-button {{ background: #eef7ff; border-color: #c8e4fb; color: #2577b8; box-shadow: none; }}
        .secondary-button:hover {{ background: #e2f1ff; border-color: #add8f7; color: #1d6ca9; }}
        .admin-actions {{ display: grid; gap: 8px; justify-items: stretch; min-width: 112px; }}
        .admin-actions .button {{ min-height: 36px; padding: 0 12px; border-radius: 11px; font-size: 13px; }}
        .inline-form {{ margin: 0; }}
        .status-badge {{ display: inline-flex; align-items: center; min-height: 28px; padding: 0 10px; border-radius: 999px; font-size: 12px; font-weight: 500; background: #eef7ff; color: #2577b8; }}
        .status-badge.active {{ background: #e8f8f1; color: #0b684a; }}
        .status-badge.disabled {{ background: #fff4de; color: #895300; }}
        .status-badge.archived {{ background: #eef1f5; color: #52637a; }}
        .danger-button {{ background: #fff1f2; border-color: #fecdd3; color: #b42337; box-shadow: none; }}
        .danger-button:hover {{ background: #ffe4e6; border-color: #fda4af; color: #9f1239; }}
        @media (max-width: 1100px) {{
          .account-hero, .flow-grid {{ grid-template-columns: 1fr; }}
          .status-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
          .hero-actions {{ justify-content: flex-start; }}
        }}
        @media (max-width: 720px) {{
          .shell {{ width: min(100% - 24px, 1440px); padding-top: 16px; }}
          .panel {{ padding: 18px; border-radius: 18px; }}
          h1 {{ font-size: 28px; }}
          .top-nav {{ width: 100%; }}
          .top-nav a {{ flex: 1; text-align: center; }}
          .account-hero, .status-grid, .flow-grid {{ grid-template-columns: 1fr; }}
          .sync-log-row {{ grid-template-columns: 1fr; }}
          .source-meta {{ grid-template-columns: 1fr; }}
        }}
      </style>
    </head>
    <body><main class="shell">{body}</main></body>
    </html>
    """


def _format_latest_job(job: dict[str, Any] | None) -> str:
    if not job:
        return '<span class="muted">нет</span>'
    return (
        f"<strong>{html.escape(_label_status(job['status']))}</strong><br>"
        f"{html.escape(_label_job_type(job['job_type']))}<br>"
        f"{job['items_count']} элементов / ошибок {job['failed_count']}"
    )


def _format_latest_error(error: dict[str, Any] | None) -> str:
    if not error:
        return '<span class="muted">нет</span>'
    text = str(error.get("error") or "")
    if len(text) > 160:
        text = text[:157] + "..."
    return (
        f"<strong>{html.escape(_label_source(error.get('source') or 'error'))}</strong><br>"
        f"{html.escape(_label_entity(error.get('entity_type') or ''))}<br>"
        f"<span class=\"muted\">{html.escape(text)}</span>"
    )


def _label_entity(value: Any) -> str:
    text = str(value or "")
    return ENTITY_LABELS.get(text, text.replace("_", " ").strip() or "Не указано")


def _label_status(value: Any) -> str:
    text = str(value or "")
    return STATUS_LABELS.get(text, text.replace("_", " ").strip() or "Не указано")


def _label_job_type(value: Any) -> str:
    text = str(value or "")
    return JOB_TYPE_LABELS.get(text, text.replace("_", " ").strip() or "Не указано")


def _label_source(value: Any) -> str:
    text = str(value or "")
    return SOURCE_LABELS.get(text, text.replace("_", " ").strip() or "Не указано")


def _label_action(value: Any) -> str:
    text = str(value or "")
    return ACTION_LABELS.get(text, text.replace("_", " ").strip() or "Не указано")


def _format_datetime(value: Any) -> str:
    if value in {None, ""}:
        return "нет"
    text = str(value).strip()
    if not text:
        return "нет"
    if re.fullmatch(r"\d{10,13}", text):
        try:
            timestamp = int(text)
            if timestamp > 9_999_999_999:
                timestamp = timestamp // 1000
            return datetime.fromtimestamp(timestamp, LOCAL_TZ).strftime("%d.%m.%Y %H:%M:%S")
        except (OverflowError, OSError, ValueError):
            return text
    try:
        normalized = text.replace("Z", "+00:00")
        moment = datetime.fromisoformat(normalized)
        if moment.tzinfo is not None:
            moment = moment.astimezone(LOCAL_TZ)
        return moment.strftime("%d.%m.%Y %H:%M:%S")
    except ValueError:
        return text.replace("T", " ").split("+", 1)[0]


def _render_sync_job_status(job: dict[str, Any]) -> str:
    percent = int(job.get("progress_percent") or 0)
    entities = [str(item) for item in (job.get("entity_types") or [])]
    results = list(job.get("result") or [])
    result_by_type = {
        str(item.get("entity_type")): item
        for item in results
        if item.get("entity_type")
    }
    running_item = next((item for item in results if item.get("status") == "running"), None)
    next_entity = str(running_item.get("entity_type")) if running_item else (
        entities[len(results)] if len(results) < len(entities) else ""
    )
    if str(job.get("status")) in {"pending", "running"} and next_entity:
        current_count = int((running_item or {}).get("items_count") or 0)
        current_text = f"Сейчас: {_label_entity(next_entity)} · принято {current_count} элементов"
    else:
        current_text = f"Итог: {_label_status(job.get('status'))}"

    rows = []
    for index, entity in enumerate(entities):
        item = result_by_type.get(entity)
        row_class = "waiting"
        state = "Ожидает"
        details = "еще не запускалось"
        if item:
            if item.get("status") == "running":
                row_class = "running"
                state = "Выполняется сейчас"
                details = (
                    f"принято {int(item.get('items_count') or 0)} элементов"
                    f" · страниц {int(item.get('pages_count') or 0)}"
                )
            else:
                failed = item.get("status") == "failed" or bool(item.get("error"))
                row_class = "error" if failed else "done"
                state = "Ошибка" if failed else "Готово"
                details = str(item.get("error") or f"{int(item.get('items_count') or 0)} элементов")
        elif str(job.get("status")) in {"pending", "running"} and index == len(results):
            row_class = "running"
            state = "Ожидает запуска" if job.get("status") == "pending" else "Выполняется сейчас"
            details = "сервер обрабатывает этот шаг"
        rows.append(
            f"""
            <div class="sync-log-row {row_class}">
              <span class="muted">{index + 1}/{len(entities)}</span>
              <strong>{html.escape(_label_entity(entity))}</strong>
              <span>{html.escape(state)}</span>
              <span class="muted">{html.escape(details)}</span>
            </div>
            """
        )
    log_html = "".join(rows) or '<span class="muted">Журнал появится после старта обработки.</span>'
    error_html = (
        f"<p><strong>Ошибка:</strong> {html.escape(str(job.get('error')))}</p>"
        if job.get("error")
        else ""
    )
    return f"""
      <div class="sync-head">
        <div>
          <strong>Задача #{int(job['id'])}</strong>: {html.escape(_label_status(job.get('status')))}<br>
          <span class="muted">{html.escape(current_text)}</span>
        </div>
        <div class="sync-percent">{percent}%</div>
      </div>
      <div class="job-progress"><span style="width: {percent}%"></span></div>
      <div class="sync-meta">
        <span class="sync-pill">Сущности: {int(job.get('done_entities') or 0)}/{int(job.get('total_entities') or 0)}</span>
        <span class="sync-pill">Элементов: {int(job.get('items_count') or 0)}</span>
        <span class="sync-pill">Ошибок: {int(job.get('failed_count') or 0)}</span>
        <span class="sync-pill">Старт: {html.escape(_format_datetime(job.get('started_at')))}</span>
      </div>
      <div class="sync-log">{log_html}</div>
      {error_html}
    """


def _account_suffix(settings: Any) -> str:
    return f"user={html.escape(settings.user_key)}&account={html.escape(settings.account_key)}"


def _account_nav(settings: Any, active: str) -> str:
    suffix = _account_suffix(settings)
    items = [
        ("admin", "/admin", "Админка"),
        ("account", f"/app?{suffix}", "Аккаунт и выгрузки"),
        ("freshness", f"/freshness?{suffix}", "Актуальность"),
        ("dashboard", f"/dashboard?{suffix}", "Дашборд"),
        ("quality", f"/quality?{suffix}", "Контроль"),
        ("quality-settings", f"/quality-settings?{suffix}", "Фильтры контроля"),
        ("activity", f"/activity?{suffix}", "Активность"),
        ("conversations", f"/conversations?{suffix}", "Разговоры"),
        ("modules", f"/account-settings?{suffix}", "Модули"),
        ("settings", f"/settings?{suffix}", "Массив данных"),
        ("constructor", f"/constructor?{suffix}", "Конструктор"),
        ("queue", f"/queue?{suffix}", "Очередь"),
    ]
    links = "".join(
        f'<a class="{"active" if key == active else ""}" href="{href}">{label}</a>'
        for key, href, label in items
    )
    return f'<nav class="top-nav">{links}</nav>'



def _pipeline_options_from_payloads(pipelines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    options = []
    for pipeline in pipelines:
        try:
            pipeline_id = int(pipeline["id"])
        except (KeyError, TypeError, ValueError):
            continue
        statuses = []
        for status in (pipeline.get("_embedded") or {}).get("statuses") or []:
            try:
                status_id = int(status["id"])
            except (KeyError, TypeError, ValueError):
                continue
            statuses.append({
                "status_id": status_id,
                "status_name": status.get("name") or f"Status {status_id}",
                "status_sort": int(status.get("sort") or 0),
            })
        statuses.sort(key=lambda item: (item["status_sort"], item["status_name"]))
        options.append({
            "pipeline_id": pipeline_id,
            "pipeline_name": pipeline.get("name") or f"Pipeline {pipeline_id}",
            "pipeline_sort": int(pipeline.get("sort") or 0),
            "statuses": statuses,
        })
    options.sort(key=lambda item: (item["pipeline_sort"], item["pipeline_name"]))
    return options


def _render_admin_page() -> str:
    active_connections = {
        (item["user_key"], item["account_key"]): item
        for item in list_connections(include_metrics=True)
    }
    connections = []
    for item in admin_connections():
        merged = dict(item)
        metrics = active_connections.get((item["user_key"], item["account_key"]))
        if metrics:
            merged.update(metrics)
        else:
            merged.update({
                "entities_count": 0,
                "entity_types": 0,
                "queue_pending": 0,
                "queue_running": 0,
                "queue_failed": 0,
                "latest_job": None,
                "latest_error": None,
            })
        connections.append(merged)

    def status_form(item: dict[str, Any], status: str, label: str, button_class: str = "secondary-button") -> str:
        return f"""
          <form class="inline-form" method="post" action="/admin/connections/status">
            <input type="hidden" name="user_key" value="{html.escape(str(item['user_key']))}">
            <input type="hidden" name="account_key" value="{html.escape(str(item['account_key']))}">
            <input type="hidden" name="status" value="{html.escape(status)}">
            <button class="{html.escape(button_class)}" type="submit">{html.escape(label)}</button>
          </form>
        """

    def action_links(item: dict[str, Any]) -> str:
        user = html.escape(str(item["user_key"]))
        account = html.escape(str(item["account_key"]))
        status = str(item.get("status") or "active")
        links = []
        if status == "active":
            links.extend([
                f'<a class="button" href="/app?user={user}&account={account}">Открыть аккаунт</a>',
                f'<a class="button" href="/dashboard?user={user}&account={account}">Дашборд</a>',
                f'<a class="button" href="/quality?user={user}&account={account}">Контроль</a>',
                f'<a class="button" href="/activity?user={user}&account={account}">Активность</a>',
                f'<a class="button" href="/conversations?user={user}&account={account}">Разговоры</a>',
                f'<a class="button" href="/settings?user={user}&account={account}">Настройки</a>',
                status_form(item, "disabled", "Отключить", "secondary-button"),
                status_form(item, "archived", "Архив", "danger-button"),
            ])
        else:
            links.append(status_form(item, "active", "Включить", "secondary-button"))
            if status != "archived":
                links.append(status_form(item, "archived", "Архив", "danger-button"))
        return f'<div class="admin-actions">{"".join(links)}</div>'

    rows = "".join(
        f"""
        <tr>
          <td><code>{html.escape(str(item['user_key']))}</code></td>
          <td><code>{html.escape(str(item['account_key']))}</code><br><span class="muted">{html.escape(str(item.get('subdomain') or ''))}</span></td>
          <td><span class="status-badge {html.escape(str(item.get('status') or 'active'))}">{html.escape(_label_status(item.get('status') or 'active'))}</span></td>
          <td>{int(item.get('entities_count') or 0)}</td>
          <td>{int(item.get('entity_types') or 0)}</td>
          <td class="queue-text">Ожидает {int(item.get('queue_pending') or 0)}<br>В работе {int(item.get('queue_running') or 0)}<br>Ошибок {int(item.get('queue_failed') or 0)}</td>
          <td>{_format_latest_job(item.get('latest_job'))}</td>
          <td class="error-cell">{_format_latest_error(item.get('latest_error'))}</td>
          <td><code class="db-code" title="{html.escape(str(item.get('db_path') or ''))}">{html.escape(str(item.get('db_path') or ''))}</code></td>
          <td>{action_links(item)}</td>
        </tr>
        """
        for item in connections
    ) or '<tr><td colspan="10" class="muted">Подключений пока нет</td></tr>'
    body = f"""
      <nav class="top-nav"><a class="active" href="/admin">Админка</a></nav>
      <section class="panel">
        <h1>Админка сервиса</h1>
        <p>Единое место для подключения amoCRM-аккаунтов, проверки состояния хаба, очереди, ошибок и локальных баз.</p>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>Подключения</h2>
          <span class="muted">{len(connections)} аккаунта</span>
        </div>
        <div class="table-wrap">
          <table class="admin-table">
            <colgroup>
              <col style="width: 8%">
              <col style="width: 9%">
              <col style="width: 7%">
              <col style="width: 8%">
              <col style="width: 6%">
              <col style="width: 8%">
              <col style="width: 9%">
              <col style="width: 18%">
              <col style="width: 18%">
              <col style="width: 10%">
            </colgroup>
            <thead><tr><th>Владелец</th><th>Аккаунт amoCRM</th><th>Статус</th><th>Сущностей</th><th>Типов</th><th>Очередь</th><th>Последняя задача</th><th>Последняя ошибка</th><th>База</th><th>Действия</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </section>
      <section class="panel">
        <h2>Добавить подключение</h2>
        <form method="post" action="/admin/connections">
          <div class="grid">
            <label>Ключ владельца<input name="user_key" placeholder="client_ivan"></label>
            <label>Ключ аккаунта<input name="account_key" placeholder="donpotolok"></label>
            <label>Поддомен amoCRM<input name="subdomain" placeholder="donpotolok"></label>
            <label>Токен доступа<input name="access_token" placeholder="token"></label>
          </div>
          <p><button type="submit">Создать подключение</button></p>
        </form>
      </section>
    """
    return _page_shell("Админка amoCRM сервиса", body)


def _render_app_page(settings: Any) -> str:
    repo = _repo(settings)
    entities = repo.hub_entity_overview()
    jobs = repo.latest_sync_jobs(settings.account_key)
    queue_counts = repo.queue_status_counts(settings.account_key)
    background = _background_worker_snapshot(settings.user_key, settings.account_key)
    account_background = background.get("account") or {}
    runs = repo.latest_sync_runs(10)
    errors = repo.latest_errors(10)
    sync_sources = repo.list_sync_sources(settings.account_key)
    source_filter_options = AnalyticsService(repo).pipeline_filter_options()
    suffix = _account_suffix(settings)
    pipeline_names = {
        int(pipeline["pipeline_id"]): str(pipeline["pipeline_name"])
        for pipeline in source_filter_options
    }
    status_names = {
        int(status["status_id"]): str(status["status_name"])
        for pipeline in source_filter_options
        for status in pipeline["statuses"]
    }

    def source_pipeline_label(pipeline_id: Any) -> str:
        value = int(pipeline_id)
        name = pipeline_names.get(value)
        return f"{html.escape(name)} <span class=\"muted\">#{value}</span>" if name else f"<span class=\"muted\">#{value}</span>"

    def source_status_label(status_id: Any) -> str:
        value = int(status_id)
        name = status_names.get(value)
        return f"{html.escape(name)} <span class=\"muted\">#{value}</span>" if name else f"<span class=\"muted\">#{value}</span>"

    total_entities = sum(int(row["items_count"] or 0) for row in entities)
    entity_rows = "".join(
        f"<tr><td>{html.escape(_label_entity(row['entity_type']))}</td><td>{row['items_count']}</td><td>{html.escape(_format_datetime(row['last_synced_at']))}</td></tr>"
        for row in entities
    ) or '<tr><td colspan="3" class="muted">Данные еще не синхронизированы</td></tr>'
    job_rows = "".join(
        f"<tr><td>{job['id']}</td><td>{html.escape(_label_job_type(job['job_type']))}</td><td>{html.escape(_label_status(job['status']))}</td><td>{job['items_count']}</td><td>{job['failed_count']}</td><td>{html.escape(_format_datetime(job['finished_at'] or job['started_at']))}</td></tr>"
        for job in jobs
    ) or '<tr><td colspan="6" class="muted">Задач синхронизации еще не было</td></tr>'
    run_rows = "".join(
        f"<tr><td>{run['id']}</td><td>{html.escape(_label_entity(run['entity_type']))}</td><td>{html.escape(_label_status(run['status']))}</td><td>{run['items_count']}</td><td>{html.escape(_format_datetime(run['finished_at'] or run['started_at']))}</td></tr>"
        for run in runs
    ) or '<tr><td colspan="5" class="muted">Запусков пока нет</td></tr>'
    error_rows = "".join(
        f"<tr><td>{html.escape(_label_source(error['source']))}</td><td>{html.escape(_label_entity(error['entity_type']))}</td><td>{html.escape(_label_status(error['status']))}</td><td>{html.escape(str(error['error']))}</td><td>{html.escape(_format_datetime(error['happened_at']))}</td></tr>"
        for error in errors
    ) or '<tr><td colspan="5" class="muted">Ошибок нет</td></tr>'

    def compact_label_list(labels: list[str], empty_text: str) -> str:
        if not labels:
            return html.escape(empty_text)
        visible = labels[:5]
        extra = len(labels) - len(visible)
        suffix_text = f' <span class="muted">+ еще {extra}</span>' if extra > 0 else ""
        return ", ".join(visible) + suffix_text

    def source_filter_label(source: dict[str, Any], key: str, formatter: Any, empty_text: str) -> str:
        return compact_label_list([formatter(item) for item in source.get(key, [])], empty_text)

    source_cards = "".join(
        f"""
        <article class="source-card">
          <div class="source-card-head">
            <div>
              <h3>{html.escape(str(source['name']))}</h3>
              <span class="muted">Источник #{int(source['id'])}</span>
            </div>
            <span class="source-pill">{html.escape(_label_status(source.get('last_job_status') or 'pending'))}</span>
          </div>
          <div class="source-meta">
            <div><span>Сделок в источнике</span><strong>{int(source['linked_count'] or 0)}</strong></div>
            <div><span>Последняя выгрузка</span><strong>{html.escape(_format_datetime(source.get('last_job_finished_at') or source.get('last_job_started_at') or source['updated_at']))}</strong></div>
          </div>
          <div class="source-list">
            <div><strong>Воронки:</strong> {source_filter_label(source, 'pipeline_ids', source_pipeline_label, 'Все воронки')}</div>
            <div><strong>Этапы:</strong> {source_filter_label(source, 'status_ids', source_status_label, 'Все этапы выбранных воронок')}</div>
            <div><strong>Обновление:</strong> ручная перевыгрузка источника. Webhook обновляет общий хаб и очередь текущих изменений.</div>
            {f"<div><strong>Ошибка:</strong> {html.escape(str(source.get('last_job_error') or ''))}</div>" if source.get('last_job_error') else ""}
          </div>
          <div class="source-actions">
            <form method="post" action="/api/sync-sources/{int(source['id'])}/resync?{suffix}" data-async-sync>
              <button type="submit">Перевыгрузить источник</button>
            </form>
            <a class="button secondary-button" href="/settings?{suffix}&source_id={int(source['id'])}">Открыть в массиве данных</a>
          </div>
        </article>
        """
        for source in sync_sources
    ) or '<div class="source-card"><strong>Источников пока нет</strong><p>Задай название, выбери воронку и этапы, потом запусти выгрузку. После этого источник появится здесь отдельной карточкой.</p></div>'
    source_filter_blocks = "".join(
        f"""
        <details class="source-filter-block">
          <summary>
            <label><input type="checkbox" name="pipeline_ids" value="{int(pipeline['pipeline_id'])}" data-source-pipeline> {html.escape(str(pipeline['pipeline_name']))}</label>
          </summary>
          <div class="source-statuses">
            {''.join(
                f'<label><input type="checkbox" name="status_ids" value="{int(status["status_id"])}" data-source-status data-pipeline-id="{int(pipeline["pipeline_id"])}"> {html.escape(str(status["status_name"]))}</label>'
                for status in pipeline["statuses"]
            )}
          </div>
        </details>
        """
        for pipeline in source_filter_options
    ) or '<p class="muted">Воронки появятся после выгрузки сущности “Воронки и этапы”.</p>'
    active_job = next((job for job in jobs if job["status"] in {"pending", "running"}), None)
    if active_job:
        with _SYNC_THREADS_LOCK:
            active_in_process = int(active_job["id"]) in _SYNC_THREADS
        active_job["active_in_process"] = active_in_process
        if active_job["status"] in {"pending", "running"} and not active_in_process:
            active_job["status"] = "interrupted"
            active_job["error"] = active_job.get("error") or "Сервис перезапускался, фоновая задача остановлена. Запустите выгрузку еще раз."
    active_job_url = f"/api/sync/jobs/{active_job['id']}?{suffix}" if active_job else ""
    job_status_attrs = (
        f'data-job-status data-current-job-url="{html.escape(active_job_url)}"'
        if active_job
        else "data-job-status hidden"
    )
    job_status_text = _render_sync_job_status(active_job) if active_job else ""
    webhook_url = f"https://www.panel-amo.linksider-ai.ru/api/amo/webhook?{suffix}"
    connection_title = settings.subdomain or settings.account_key
    body = f"""
      {_account_nav(settings, "account")}
      <section class="panel account-hero">
        <div>
          <p class="hero-kicker">Аккаунт amoCRM</p>
          <h1>{html.escape(settings.account_key)}</h1>
          <p>Здесь подключаем аккаунт, создаем источники выгрузки и отправляем их в отчеты. Технические операции спрятаны ниже, чтобы не мешали основной работе.</p>
        </div>
        <div class="hero-actions">
          <a class="button" href="#sources">Создать источник</a>
          <a class="button secondary-button" href="/settings?{suffix}">Массив данных</a>
          <a class="button secondary-button" href="/constructor?{suffix}">Конструктор</a>
          <a class="button secondary-button" href="/dashboard?{suffix}">Дашборд</a>
        </div>
      </section>
      <section class="status-grid" aria-label="Состояние аккаунта">
        <article class="status-card"><span>Подключение</span><strong>Активно</strong><p>amoCRM: <code>{html.escape(connection_title)}</code><br>Зона: <code>{html.escape(settings.user_key)}</code></p></article>
        <article class="status-card"><span>Данные в хабе</span><strong>{total_entities}</strong><p>{len(entities)} типов данных уже лежат в локальной базе.</p></article>
        <article class="status-card"><span>Обновления</span><strong>{'Включены' if background.get('thread_alive') else 'Выключены'}</strong><p>Webhook кладет изменения в очередь, фоновый процесс забирает их пачками.</p></article>
        <article class="status-card"><span>Очередь</span><strong>{queue_counts.get('pending', 0)}</strong><p>Ждут обработки · в работе {queue_counts.get('running', 0)} · ошибок {queue_counts.get('failed', 0)}</p></article>
      </section>
      <section class="panel">
        <h2>Как пользоваться этим экраном</h2>
        <div class="flow-grid">
          <article class="flow-card"><strong>1. Создай источник</strong><p>Выбери воронку и этапы amoCRM, назови выгрузку понятным именем.</p></article>
          <article class="flow-card"><strong>2. Обнови данные</strong><p>Ручная выгрузка подтянет выбранный набор. Webhook будет ловить текущие изменения.</p></article>
          <article class="flow-card"><strong>3. Строй отчеты</strong><p>Открой конструктор и выбери этот источник как базу для виджета.</p></article>
        </div>
        <div class="webhook-box"><strong>Webhook для amoCRM:</strong> <code>{html.escape(webhook_url)}</code></div>
        <div class="job-status" {job_status_attrs}>{job_status_text}</div>
      </section>
      <section class="panel" id="sources">
        <h2>Источники для отчетов</h2>
        <p>Источник - это отдельная выгрузка: например одна воронка, несколько этапов или отдельный рабочий набор. Каждый источник потом выбирается в конструкторе отчетов.</p>
        <form method="post" action="/api/sync/resync?{suffix}" data-async-sync data-source-sync>
          <div class="grid">
            <label>Название источника<input name="source_name" placeholder="Например: Don Потолок · Розыгрыш" required></label>
          </div>
          <div class="source-summary" data-source-summary>Выбери воронку: этапы внутри нее отметятся автоматически.</div>
          <div class="source-filters">{source_filter_blocks}</div>
          <p><button type="submit">Создать источник и выгрузить</button></p>
        </form>
        <div class="source-card-grid">{source_cards}</div>
      </section>
      <details class="panel technical-panel" id="sync">
        <summary>Обслуживание хаба</summary>
        <p>Эти кнопки нужны редко: для первого подключения, полной перевыгрузки хаба или ручной обработки очереди webhook.</p>
        <div class="actions">
          <form method="post" action="/api/sync/bootstrap?{suffix}" data-async-sync><button type="submit">Первичная выгрузка хаба</button></form>
          <form method="post" action="/api/sync/resync?{suffix}" data-async-sync><button type="submit">Полностью перевыгрузить хаб</button></form>
          <form method="post" action="/api/sync-queue/process?{suffix}"><button type="submit">Обработать очередь webhook</button></form>
          <a class="button" href="/api/hub/overview?{suffix}">JSON хаба</a>
        </div>
      </details>
      <details class="panel technical-panel">
        <summary>Технические детали и журналы</summary>
        <h2>Данные в хабе</h2>
        <table><thead><tr><th>Тип</th><th>Кол-во</th><th>Последняя синхронизация</th></tr></thead><tbody>{entity_rows}</tbody></table>
        <h2>Последние задачи синхронизации</h2>
        <table><thead><tr><th>ID</th><th>Тип</th><th>Статус</th><th>Элементов</th><th>Ошибок</th><th>Время</th></tr></thead><tbody>{job_rows}</tbody></table>
        <h2>Последние запуски по сущностям</h2>
        <table><thead><tr><th>ID</th><th>Тип</th><th>Статус</th><th>Кол-во</th><th>Время</th></tr></thead><tbody>{run_rows}</tbody></table>
        <h2>Последние ошибки</h2>
        <table><thead><tr><th>Источник</th><th>Тип</th><th>Статус</th><th>Ошибка</th><th>Время</th></tr></thead><tbody>{error_rows}</tbody></table>
      </details>
      <script>
        (() => {{
          const statusEl = document.querySelector('[data-job-status]');
          const forms = [...document.querySelectorAll('[data-async-sync]')];
          const sourcePipelines = [...document.querySelectorAll('[data-source-pipeline]')];
          const sourceStatuses = [...document.querySelectorAll('[data-source-status]')];
          const sourceSummary = document.querySelector('[data-source-summary]');
          const entityLabels = {{
            leads: 'Сделки',
            contacts: 'Контакты',
            companies: 'Компании',
            tasks: 'Задачи',
            customers: 'Покупатели',
            events: 'События',
            lead_notes: 'Примечания сделок',
            contact_notes: 'Примечания контактов',
            company_notes: 'Примечания компаний',
            customer_notes: 'Примечания покупателей',
            users: 'Пользователи',
            pipelines: 'Воронки и этапы',
            lead_custom_fields: 'Поля сделок',
            contact_custom_fields: 'Поля контактов',
            company_custom_fields: 'Поля компаний',
            customer_custom_fields: 'Поля покупателей',
            catalogs: 'Каталоги',
            catalog_elements: 'Элементы каталогов',
            salesbots: 'Salesbot'
          }};
          const statusLabels = {{
            pending: 'Ожидает',
            running: 'Выполняется',
            failed: 'Ошибка',
            done: 'Готово',
            ignored: 'Игнорируется',
            interrupted: 'Прервано',
            success: 'Успешно',
            partial: 'Завершено с ошибками'
          }};
          const finalStatuses = ['success', 'partial', 'failed', 'interrupted', 'done'];
          const escapeHtml = (value) => String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
          const labelStatus = (value) => statusLabels[value] || value || 'Не указано';
          const labelEntity = (value) => entityLabels[value] || String(value || 'Не указано').replaceAll('_', ' ');
          const formatDate = (value) => {{
            if (!value) return 'нет';
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) return String(value).replace('T', ' ').split('+')[0];
            return new Intl.DateTimeFormat('ru-RU', {{
              day: '2-digit',
              month: '2-digit',
              year: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
              second: '2-digit'
            }}).format(date);
          }};
          const readJson = async (response) => {{
            const text = await response.text();
            try {{
              return JSON.parse(text);
            }} catch (error) {{
              const preview = text.replace(/<[^>]*>/g, ' ').replace(/\\s+/g, ' ').trim().slice(0, 220);
              throw new Error(`Сервер вернул не JSON: HTTP ${{response.status}} ${{response.url}}${{preview ? ' · ' + preview : ''}}`);
            }}
          }};
          const buildStepRows = (job) => {{
            const entities = job.entity_types || [];
            const results = job.result || [];
            const byType = new Map(results.map((item) => [item.entity_type, item]));
            const runningItem = results.find((item) => item.status === 'running');
            const runningIndex = runningItem
              ? entities.indexOf(runningItem.entity_type)
              : Math.min(results.length, Math.max(entities.length - 1, 0));
            return entities.map((entity, index) => {{
              const item = byType.get(entity);
              let state = 'Ожидает';
              let rowClass = 'waiting';
              let details = 'еще не запускалось';
              if (item) {{
                if (item.status === 'running') {{
                  state = 'Выполняется сейчас';
                  rowClass = 'running';
                  details = `принято ${{Number(item.items_count || 0)}} элементов · страниц ${{Number(item.pages_count || 0)}}`;
                }} else {{
                  const failed = item.status === 'failed' || item.error;
                  state = failed ? 'Ошибка' : 'Готово';
                  rowClass = failed ? 'error' : 'done';
                  details = failed
                    ? escapeHtml(item.error || 'ошибка без текста')
                    : `${{Number(item.items_count || 0)}} элементов`;
                }}
              }} else if (['pending', 'running'].includes(job.status) && index === runningIndex) {{
                state = job.status === 'pending' ? 'Ожидает запуска' : 'Выполняется сейчас';
                rowClass = 'running';
                details = 'сервер обрабатывает этот шаг';
              }}
              return `
                <div class="sync-log-row ${{rowClass}}">
                  <span class="muted">${{index + 1}}/${{entities.length}}</span>
                  <strong>${{escapeHtml(labelEntity(entity))}}</strong>
                  <span>${{state}}</span>
                  <span class="muted">${{details}}</span>
                </div>
              `;
            }}).join('');
          }};
          const renderJob = (job) => {{
            const percent = job.progress_percent || 0;
            const entities = job.entity_types || [];
            const results = job.result || [];
            const runningItem = results.find((item) => item.status === 'running');
            const nextEntity = runningItem?.entity_type || entities[results.length];
            const currentText = ['pending', 'running'].includes(job.status) && nextEntity
              ? `Сейчас: ${{labelEntity(nextEntity)}} · принято ${{Number(runningItem?.items_count || 0)}} элементов`
              : `Итог: ${{labelStatus(job.status)}}`;
            statusEl.hidden = false;
            statusEl.dataset.currentJobUrl = statusEl.dataset.currentJobUrl || `/api/sync/jobs/${{job.id}}?{suffix}`;
            statusEl.innerHTML = `
              <div class="sync-head">
                <div>
                  <strong>Задача #${{job.id}}</strong>: ${{labelStatus(job.status)}}<br>
                  <span class="muted">${{currentText}}</span>
                </div>
                <div class="sync-percent">${{percent}}%</div>
              </div>
              <div class="job-progress"><span style="width: ${{percent}}%"></span></div>
              <div class="sync-meta">
                <span class="sync-pill">Сущности: ${{job.done_entities}}/${{job.total_entities}}</span>
                <span class="sync-pill">Элементов: ${{job.items_count}}</span>
                <span class="sync-pill">Ошибок: ${{job.failed_count}}</span>
                <span class="sync-pill">Старт: ${{formatDate(job.started_at)}}</span>
              </div>
              <div class="sync-log">${{buildStepRows(job) || '<span class="muted">Журнал появится после старта обработки.</span>'}}</div>
              ${{job.error ? `<p><strong>Ошибка:</strong> ${{escapeHtml(job.error)}}</p>` : ''}}
            `;
          }};
          const pollJob = async (statusUrl) => {{
            const response = await fetch(statusUrl);
            const data = await readJson(response);
            if (!response.ok || !data.ok) throw new Error(data.error || 'не удалось получить статус фоновой задачи');
            renderJob(data.job);
            if (['pending', 'running'].includes(data.job.status)) {{
              window.setTimeout(() => pollJob(statusUrl).catch(showError), 2500);
            }} else if (finalStatuses.includes(data.job.status)) {{
              forms.forEach((form) => {{
                const button = form.querySelector('button');
                if (button) button.disabled = false;
              }});
            }}
          }};
          const showError = (error) => {{
            statusEl.hidden = false;
            statusEl.innerHTML = `<strong>Ошибка запуска</strong>: ${{String(error.message || error)}}`;
          }};
          const plural = (count, one, few, many) => {{
            const mod10 = count % 10;
            const mod100 = count % 100;
            if (mod10 === 1 && mod100 !== 11) return one;
            if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
            return many;
          }};
          const updateSourceSummary = () => {{
            if (!sourceSummary) return;
            const pipelines = sourcePipelines.filter((item) => item.checked);
            const statuses = sourceStatuses.filter((item) => item.checked);
            if (!pipelines.length) {{
              sourceSummary.textContent = 'Выбери воронку: этапы внутри нее отметятся автоматически.';
              return;
            }}
            sourceSummary.textContent = `В источник уйдет ${{pipelines.length}} ${{plural(pipelines.length, 'воронка', 'воронки', 'воронок')}} и ${{statuses.length}} ${{plural(statuses.length, 'этап', 'этапа', 'этапов')}}. Название источника потом будет видно в конструкторе отчетов.`;
          }};
          sourcePipelines.forEach((pipeline) => {{
            pipeline.addEventListener('change', () => {{
              sourceStatuses
                .filter((status) => status.dataset.pipelineId === pipeline.value)
                .forEach((status) => status.checked = pipeline.checked);
              updateSourceSummary();
            }});
          }});
          sourceStatuses.forEach((status) => {{
            status.addEventListener('change', updateSourceSummary);
          }});
          updateSourceSummary();
          const sourcePayload = (form) => {{
            if (!form.matches('[data-source-sync]')) return {{}};
            const data = new FormData(form);
            const pipelineIds = data.getAll('pipeline_ids').map(Number).filter(Boolean);
            const statusIds = data.getAll('status_ids').map(Number).filter(Boolean);
            if (!pipelineIds.length) {{
              throw new Error('Выбери хотя бы одну воронку для источника');
            }}
            return {{
              source_name: String(data.get('source_name') || '').trim(),
              pipeline_ids: pipelineIds,
              status_ids: statusIds
            }};
          }};
          forms.forEach((form) => {{
            form.addEventListener('submit', async (event) => {{
              event.preventDefault();
              const button = form.querySelector('button');
              button.disabled = true;
              statusEl.hidden = false;
              statusEl.textContent = 'Создаю фоновую задачу...';
              try {{
                const payload = sourcePayload(form);
                const response = await fetch(form.action, {{
                  method: 'POST',
                  headers: {{ 'Content-Type': 'application/json' }},
                  body: JSON.stringify(payload)
                }});
              const data = await readJson(response);
              if (!response.ok || !data.ok) throw new Error(data.error || 'не удалось запустить синхронизацию');
                statusEl.hidden = false;
                statusEl.dataset.currentJobUrl = data.status_url;
                statusEl.innerHTML = `<strong>Задача #${{data.job_id}}</strong> создана. Запрашиваю первый статус...`;
                await pollJob(data.status_url);
              }} catch (error) {{
                showError(error);
              }} finally {{
                button.disabled = false;
              }}
            }});
          }});
          if (statusEl?.dataset.currentJobUrl) {{
            pollJob(statusEl.dataset.currentJobUrl).catch(showError);
          }}
        }})();
      </script>
    """
    return _page_shell("Обзор аккаунта amoCRM", body)


def _render_account_settings_page(settings: Any, query_string: str = "") -> str:
    repo = _repo(settings)
    query = parse_qs(query_string)
    selected_user_id = str((query.get("crm_user_id") or [""])[-1]).strip()
    users = sorted(
        repo.all_payloads("users"),
        key=lambda item: str(item.get("name") or item.get("id") or "").lower(),
    )
    if not selected_user_id and users:
        selected_user_id = str(users[0].get("id") or "")
    account_config = load_account_settings(
        user_key=settings.user_key,
        account_key=settings.account_key,
        data_root=settings.data_root,
    )
    user_config = (
        load_user_settings(
            user_key=settings.user_key,
            account_key=settings.account_key,
            crm_user_id=selected_user_id,
            data_root=settings.data_root,
        )
        if selected_user_id
        else {}
    )
    suffix = _account_suffix(settings)
    user_options = "".join(
        f"""
        <option value="{html.escape(str(user.get('id') or ''))}" {"selected" if str(user.get('id') or '') == selected_user_id else ""}>
          {html.escape(str(user.get('name') or user.get('id') or 'Без имени'))}
        </option>
        """
        for user in users
    )
    selected_user_label = next(
        (str(user.get("name") or user.get("id")) for user in users if str(user.get("id") or "") == selected_user_id),
        selected_user_id or "Пользователь не выбран",
    )
    default_account_config = {
        "conversation_ai": {
            "enabled": False,
            "write_note_to_amo": False,
            "export_to_google_sheets": False,
            "analysis_language": "ru",
        },
        "dashboards": {
            "enabled": True,
        },
    }
    account_json = json.dumps(account_config or default_account_config, ensure_ascii=False, indent=2)
    user_json = json.dumps(user_config or {"dashboard": {}, "conversation_ai": {}}, ensure_ascii=False, indent=2)
    disabled = "disabled" if not selected_user_id else ""
    body = f"""
      {_account_nav(settings, "modules")}
      <section class="panel account-hero">
        <div>
          <p class="hero-kicker">Настройки модулей</p>
          <h1>{html.escape(settings.subdomain or settings.account_key)}</h1>
          <p>Конфигурация аккаунта и персональные настройки пользователей amoCRM.</p>
        </div>
        <div class="hero-actions">
          <a class="button secondary-button" href="/app?{suffix}">К аккаунту</a>
          <a class="button secondary-button" href="/admin">В админку</a>
        </div>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>Настройки компании</h2>
          <span class="muted">account_settings.json</span>
        </div>
        <form method="post" action="/account-settings/save?{suffix}">
          <label>JSON настроек аккаунта
            <textarea name="settings_json" rows="15">{html.escape(account_json)}</textarea>
          </label>
          <p><button type="submit">Сохранить настройки компании</button></p>
        </form>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>Настройки пользователя</h2>
          <span class="muted">{html.escape(selected_user_label)}</span>
        </div>
        <form method="get" action="/account-settings">
          <input type="hidden" name="user" value="{html.escape(settings.user_key)}">
          <input type="hidden" name="account" value="{html.escape(settings.account_key)}">
          <label>Пользователь amoCRM
            <select name="crm_user_id" onchange="this.form.submit()">
              {user_options or '<option value="">Сначала синхронизируйте users</option>'}
            </select>
          </label>
        </form>
        <form method="post" action="/account-settings/user/save?{suffix}">
          <input type="hidden" name="crm_user_id" value="{html.escape(selected_user_id)}">
          <label>JSON настроек пользователя
            <textarea name="settings_json" rows="12">{html.escape(user_json)}</textarea>
          </label>
          <p><button type="submit" {disabled}>Сохранить настройки пользователя</button></p>
        </form>
      </section>
    """
    return _page_shell(f"Модули {settings.account_key}", body)


def _render_queue_page(settings: Any, query_string: str = "") -> str:
    query = parse_qs(query_string)
    status = (query.get("status") or ["failed"])[0] or "failed"
    if status not in {"failed", "pending", "running", "done", "ignored"}:
        status = "failed"
    repo = _repo(settings)
    queue_counts = repo.queue_status_counts(settings.account_key)
    items = repo.list_sync_queue_items(settings.account_key, status=status, limit=200)
    suffix = _account_suffix(settings)
    tabs = "".join(
        f'<a class="button" href="/queue?{suffix}&status={name}">{label} {queue_counts.get(name, 0)}</a>'
        for name, label in [
            ("failed", "Ошибки"),
            ("pending", "Ожидают"),
            ("running", "В работе"),
            ("ignored", "Игнор"),
            ("done", "Готово"),
        ]
    )
    rows = "".join(
        f"""
        <tr>
          <td><input type="checkbox" name="ids" value="{int(item['id'])}" form="queue-bulk"></td>
          <td><code>{int(item['id'])}</code></td>
          <td>{html.escape(_label_status(item['status']))}</td>
          <td>{html.escape(_label_entity(item['entity_type']))}<br><code>{html.escape(str(item['entity_id']))}</code></td>
          <td>{html.escape(_label_action(item['action']))}<br><span class="muted">{html.escape(str(item.get('reason') or ''))}</span></td>
          <td>{int(item.get('attempts') or 0)}</td>
          <td>{html.escape(_format_datetime(item.get('updated_at')))}</td>
          <td class="error-cell">{html.escape(str(item.get('last_error') or ''))}</td>
          <td>
            <form method="post" action="/api/sync-queue/retry?{suffix}&redirect=/queue&status={html.escape(status)}">
              <input type="hidden" name="ids" value="{int(item['id'])}">
              <button type="submit">Повторить</button>
            </form>
          </td>
        </tr>
        """
        for item in items
    ) or '<tr><td colspan="9" class="muted">Элементов нет</td></tr>'
    ignore_button = ""
    if status == "failed":
        ignore_button = (
            f'<button type="submit" formaction="/api/sync-queue/ignore?{suffix}&redirect=/queue&status={status}">'
            "Игнорировать выбранные</button>"
        )
    body = f"""
      {_account_nav(settings, "queue")}
      <section class="panel">
        <h1>Очередь синхронизации</h1>
        <p>Ошибки вебхуков и точечной синхронизации по аккаунту <code>{html.escape(settings.account_key)}</code>.</p>
        <div class="actions">{tabs}</div>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>{html.escape(_label_status(status))}</h2>
          <span class="muted">{len(items)} элементов</span>
        </div>
        <form id="queue-bulk" method="post" action="/api/sync-queue/retry?{suffix}&redirect=/queue&status={html.escape(status)}">
          <div class="actions">
            <button type="submit">Повторить выбранные</button>
            {ignore_button}
          </div>
        </form>
        <div class="table-wrap">
          <table>
            <thead><tr><th></th><th>ID</th><th>Статус</th><th>Сущность</th><th>Действие</th><th>Попытки</th><th>Обновлено</th><th>Ошибка</th><th></th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </section>
    """
    return _page_shell("Очередь синхронизации", body)


def _render_conversations_page(settings: Any, query_string: str = "") -> str:
    query = parse_qs(query_string)
    posted_note = (query.get("posted_note") or [""])[0]
    error = (query.get("error") or [""])[0]
    action_result = (query.get("result") or [""])[0]
    view = (query.get("view") or ["work"])[0]
    if view not in {"work", "analyzed", "posted", "errors", "all"}:
        view = "work"
    search_text = (query.get("q") or [""])[0].strip().lower()
    repo = _repo(settings)
    raw_account_settings = load_account_settings(
        user_key=settings.user_key,
        account_key=settings.account_key,
        data_root=settings.data_root,
    )
    ci_settings = conversation_settings(raw_account_settings)
    background = _background_worker_snapshot(settings.user_key, settings.account_key)
    conversation_auto = (background.get("account") or {}).get("conversation_auto") or {}
    records = repo.list_conversation_records(settings.account_key, limit=300)
    analyses = repo.list_conversation_analyses(settings.account_key, limit=300)
    analysis_by_conversation = {
        str(item.get("conversation_id")): item
        for item in analyses
    }
    suffix = _account_suffix(settings)
    lead_cache: dict[str, dict[str, Any] | None] = {}

    def lead_payload(record: dict[str, Any]) -> dict[str, Any] | None:
        lead_id = str(record.get("lead_id") or "")
        if not lead_id:
            return None
        if lead_id not in lead_cache:
            entity = repo.get_raw_entity("leads", lead_id)
            lead_cache[lead_id] = (entity or {}).get("payload") if entity else None
        return lead_cache[lead_id]

    def is_relevant(record: dict[str, Any]) -> bool:
        return record_matches_filters(record, lead_payload(record), ci_settings["filters"])

    def matches_search(record: dict[str, Any], analysis: dict[str, Any] | None) -> bool:
        if not search_text:
            return True
        metadata = record.get("metadata") or {}
        haystack = " ".join(
            str(value or "")
            for value in [
                record.get("conversation_id"),
                record.get("lead_id"),
                record.get("contact_id"),
                metadata.get("phone"),
                metadata.get("source"),
                analysis.get("summary") if analysis else "",
                record.get("transcript_text"),
            ]
        ).lower()
        return search_text in haystack

    def record_bucket(record: dict[str, Any], analysis: dict[str, Any] | None) -> str:
        metadata = record.get("metadata") or {}
        status = str(record.get("status") or "")
        if metadata.get("last_posted_note_id"):
            return "posted"
        if analysis:
            return "analyzed"
        if status in {"recording_unavailable", "audio_download_failed", "transcription_failed"}:
            return "errors"
        if status not in {"metadata_only"} and int(record.get("duration_seconds") or 0) > 0:
            return "work"
        return "all"

    relevant_records = [record for record in records if is_relevant(record)]
    visible_source = records if view == "all" else relevant_records
    filtered_records = []
    for record in visible_source:
        analysis = analysis_by_conversation.get(str(record.get("conversation_id")))
        bucket = record_bucket(record, analysis)
        if view != "all" and view != "work" and bucket != view:
            continue
        if view == "work" and bucket not in {"work", "analyzed", "posted", "errors"}:
            continue
        if matches_search(record, analysis):
            filtered_records.append(record)

    view_counts = {
        "work": sum(1 for record in relevant_records if record_bucket(record, analysis_by_conversation.get(str(record.get("conversation_id")))) in {"work", "analyzed", "posted", "errors"}),
        "analyzed": sum(1 for record in relevant_records if record_bucket(record, analysis_by_conversation.get(str(record.get("conversation_id")))) == "analyzed"),
        "posted": sum(1 for record in relevant_records if record_bucket(record, analysis_by_conversation.get(str(record.get("conversation_id")))) == "posted"),
        "errors": sum(1 for record in relevant_records if record_bucket(record, analysis_by_conversation.get(str(record.get("conversation_id")))) == "errors"),
        "all": len(records),
    }

    stats = {
        "total": len(relevant_records),
        "transcribed": sum(1 for item in relevant_records if item.get("status") == "transcribed"),
        "analyzed": sum(1 for item in relevant_records if str(item.get("conversation_id")) in analysis_by_conversation),
        "linked": sum(1 for item in relevant_records if item.get("lead_id")),
    }

    notice = ""
    if posted_note:
        notice = (
            '<section class="panel notice-panel">'
            f'<strong>Заметка записана в сделку.</strong> ID примечания: <code>{html.escape(posted_note)}</code>'
            "</section>"
        )
    elif error:
        notice = (
            '<section class="panel notice-panel error-notice">'
            f'<strong>Не удалось выполнить действие.</strong> {html.escape(error)}'
            "</section>"
        )
    elif action_result:
        notice = (
            '<section class="panel notice-panel">'
            f'<strong>Готово.</strong> {html.escape(action_result)}'
            "</section>"
        )

    def checked(value: Any) -> str:
        return " checked" if value else ""

    scoring_json = json.dumps(ci_settings.get("scoring") or [], ensure_ascii=False, indent=2)
    external_analysis = ci_settings.get("external_analysis") or {}
    external_mode = str(external_analysis.get("mode") or "local")
    external_model = str(external_analysis.get("model") or "")
    pipeline_options = AnalyticsService(repo).pipeline_filter_options()
    if not pipeline_options:
        try:
            client = AmoCRMClient(settings)
            try:
                live_pipelines = client.get_pipelines()
            finally:
                client.close()
            pipeline_options = _pipeline_options_from_payloads(live_pipelines)
            if live_pipelines:
                repo.upsert_entities("pipelines", live_pipelines)
        except Exception:
            pipeline_options = []
    users = sorted(
        repo.all_payloads("users"),
        key=lambda item: str(item.get("name") or item.get("id") or "").lower(),
    )
    pipeline_labels = {
        str(pipeline.get("pipeline_id") or ""): str(pipeline.get("pipeline_name") or "").strip()
        for pipeline in pipeline_options
    }
    status_labels = {
        str(status.get("status_id") or ""): str(status.get("status_name") or "").strip()
        for pipeline in pipeline_options
        for status in pipeline.get("statuses") or []
    }
    user_labels = {
        str(user.get("id") or ""): str(user.get("name") or user.get("login") or user.get("email") or "").strip()
        for user in users
    }
    selected_pipeline_ids = set(ci_settings["filters"]["pipeline_ids"])
    selected_status_ids = set(ci_settings["filters"]["status_ids"])
    selected_responsible_ids = set(ci_settings["filters"]["responsible_user_ids"])
    selected_summary = (
        f"Выбрано: воронок {len(selected_pipeline_ids)}, "
        f"этапов {len(selected_status_ids)}, "
        f"ответственных {len(selected_responsible_ids)}."
    )
    auto_status = "Включена" if conversation_auto.get("enabled", ci_settings.get("enabled")) else "Выключена"
    auto_last_run = _format_datetime(conversation_auto.get("last_run_at"))
    auto_summary = conversation_auto.get("summary") or (
        "Фоновый worker еще не сделал прогон после запуска сервера."
        if background.get("thread_alive")
        else "Фоновый worker не запущен."
    )

    def pipeline_selects() -> str:
        if not pipeline_options:
            return """
            <div class="empty-selector">
              <strong>Воронки пока не загрузились</strong>
              <span>Нажми “Первичная выгрузка хаба” на экране аккаунта или обнови страницу: сервис попробует подтянуть справочник из amoCRM.</span>
            </div>
            """
        pipeline_options_html = "".join(
            f'<option value="{int(pipeline["pipeline_id"])}"{(" selected" if int(pipeline["pipeline_id"]) in selected_pipeline_ids else "")}>{html.escape(str(pipeline["pipeline_name"]))}</option>'
            for pipeline in pipeline_options
        )
        status_groups = []
        for pipeline in pipeline_options:
            pipeline_id = int(pipeline["pipeline_id"])
            options = "".join(
                f'<option value="{int(status["status_id"])}"{(" selected" if int(status["status_id"]) in selected_status_ids else "")}>{html.escape(str(status["status_name"]))}</option>'
                for status in pipeline["statuses"]
            )
            if options:
                status_groups.append(f'<optgroup label="{html.escape(str(pipeline["pipeline_name"]))}">{options}</optgroup>')
        return f"""
        <div class="selector-pair">
          <label>Воронки
            <select name="pipeline_ids" multiple size="8">{pipeline_options_html}</select>
            <small>Пусто = все воронки.</small>
          </label>
          <label>Этапы
            <select name="status_ids" multiple size="8">{"".join(status_groups)}</select>
            <small>Пусто = все этапы выбранных воронок.</small>
          </label>
        </div>
        """

    def responsible_select() -> str:
        if not users:
            return '<p class="muted">Ответственные появятся после синхронизации users.</p>'
        options = "".join(
            f'<option value="{int(user["id"])}"{(" selected" if int(user["id"]) in selected_responsible_ids else "")}>{html.escape(str(user.get("name") or user["id"]))}</option>'
            for user in users
            if user.get("id") is not None
        )
        return f"""
        <label>Ответственные
          <select name="responsible_user_ids" multiple size="8">{options}</select>
          <small>Пусто = все ответственные.</small>
        </label>
        """

    action_descriptions = {
        "enabled": ("Модуль включен", "Главный переключатель. Если выключен, автообработка не трогает звонки аккаунта."),
        "import_leads": ("Импорт сделок", "Подтягивает карточки сделок и связанные примечания/звонки из amoCRM."),
        "probe_recordings": ("Проверка записей", "Проверяет, доступна ли ссылка на аудио до скачивания."),
        "download_recordings": ("Скачивание аудио", "Сохраняет доступные записи в локальный hub аккаунта."),
        "transcribe": ("Транскрибация", "Отправляет скачанное аудио в выбранный STT-провайдер и сохраняет текст."),
        "analyze": ("AI-анализ", "Строит итог, оценку, возражения, следующий шаг и рекомендации по промпту аккаунта."),
        "post_note": ("Заметка в amoCRM", "Пишет итог анализа в карточку сделки. Боевой модуль, включать после проверки."),
        "export_google_sheets": ("Экспорт в Sheets", "Готовит таблицу анализов для загрузки в Google Sheets."),
    }

    def action_card(key: str) -> str:
        title, description = action_descriptions[key]
        value = ci_settings["enabled"] if key == "enabled" else ci_settings["actions"].get(key)
        return f"""
        <label class="module-card">
          <input type="checkbox" name="{html.escape(key)}" value="1"{checked(value)}>
          <span>
            <strong>{html.escape(title)}</strong>
            <small>{html.escape(description)}</small>
          </span>
        </label>
        """

    scoring_rows = "".join(
        f"""
        <tr>
          <td><strong>{html.escape(str(item.get('label') or item.get('key') or 'Показатель'))}</strong><br><code>{html.escape(str(item.get('key') or ''))}</code></td>
          <td>{int(item.get('max_score') or 0)}</td>
        </tr>
        """
        for item in ci_settings.get("scoring") or []
        if isinstance(item, dict)
    )

    def compact(text: Any, limit: int = 320) -> str:
        value = " ".join(str(text or "").split())
        if len(value) <= limit:
            return value
        return value[:limit].rstrip() + "..."

    def qa_payload(analysis: dict[str, Any] | None) -> dict[str, Any]:
        payload = (analysis or {}).get("analysis") or {}
        qa_json = payload.get("qa_json") or {}
        return qa_json if isinstance(qa_json, dict) else {}

    def is_v2_analysis(analysis: dict[str, Any] | None) -> bool:
        if not analysis:
            return False
        payload = analysis.get("analysis") or {}
        metrics = analysis.get("metrics") or {}
        if not isinstance(payload, dict):
            payload = {}
        if not isinstance(metrics, dict):
            metrics = {}
        return (
            payload.get("source") == "openrouter_v2_qa"
            or isinstance(payload.get("call_analysis_v2"), dict)
            or isinstance(metrics.get("call_analysis_v2"), dict)
        )

    def v2_data(analysis: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        payload = (analysis or {}).get("analysis") or {}
        metrics = (analysis or {}).get("metrics") or {}
        if not isinstance(payload, dict):
            payload = {}
        if not isinstance(metrics, dict):
            metrics = {}
        call_analysis = payload.get("call_analysis_v2") or metrics.get("call_analysis_v2") or {}
        if not isinstance(call_analysis, dict):
            call_analysis = {}
        return payload, metrics, call_analysis

    def v2_color_class(value: Any) -> str:
        color = str(value or "gray").strip().lower()
        return color if color in {"green", "yellow", "red", "gray"} else "gray"

    def score_label(analysis: dict[str, Any] | None) -> str:
        if not analysis:
            return "-"
        metrics = analysis.get("metrics") or {}
        payload = analysis.get("analysis") or {}
        score_max = int(metrics.get("score_max") or payload.get("score_max") or 100)
        return f"{int(analysis.get('score') or 0)}/{score_max}"

    def direction_label(value: Any) -> str:
        labels = {
            "incoming": "Входящий звонок",
            "outgoing": "Исходящий звонок",
            "inbound": "Входящий звонок",
            "outbound": "Исходящий звонок",
            "unknown": "Направление не указано",
        }
        text = str(value or "unknown").strip().lower()
        return labels.get(text, text.replace("_", " ").strip() or "Направление не указано")

    def sentiment_label(value: Any) -> str:
        labels = {
            "positive": "Позитивный",
            "neutral": "Нейтральный",
            "negative": "Негативный",
            "error": "Ошибка анализа",
            "unknown": "Не определено",
        }
        text = str(value or "unknown").strip().lower()
        return labels.get(text, text.replace("_", " ").strip() or "Не определено")

    def duration_label(value: Any) -> str:
        seconds = max(0, int(value or 0))
        minutes, rest = divmod(seconds, 60)
        if minutes and rest:
            return f"{minutes} мин {rest} сек"
        if minutes:
            return f"{minutes} мин"
        return f"{rest} сек"

    def qa_value(qa_json: dict[str, Any], key: str, default: str = "не определено") -> str:
        value = qa_json.get(key)
        if isinstance(value, list):
            text = ", ".join(str(item) for item in value if str(item or "").strip())
        else:
            text = str(value or "").strip()
        return text or default

    def qa_list(qa_json: dict[str, Any], key: str) -> str:
        value = qa_json.get(key)
        items = value if isinstance(value, list) else ([value] if value else [])
        cleaned = [str(item).strip() for item in items if str(item or "").strip()]
        if not cleaned:
            return '<span class="muted">не определено</span>'
        return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in cleaned) + "</ul>"

    def v2_score_cell(analysis: dict[str, Any] | None, fallback_score: str, fallback_sentiment: str) -> str:
        if not is_v2_analysis(analysis):
            return f'<strong>{html.escape(fallback_score)}</strong><br><span class="muted">{html.escape(fallback_sentiment)}</span>'
        _payload, metrics, call_analysis = v2_data(analysis)
        outcome = str(metrics.get("outcome") or call_analysis.get("outcome") or "не_применимо").strip()
        quality = str(metrics.get("quality_display") or "—").strip() or "—"
        color = v2_color_class(metrics.get("outcome_color"))
        return (
            f'<span class="v2-score-pill v2-outcome-{color}">'
            f'<span class="v2-dot"></span>{html.escape(outcome)} · {html.escape(quality)}</span>'
        )

    def v2_outcome_badge(outcome: Any, color: Any) -> str:
        outcome_text = str(outcome or "не_применимо").strip() or "не_применимо"
        color_class = v2_color_class(color)
        return f'<span class="v2-badge v2-outcome-{color_class}">{html.escape(outcome_text)}</span>'

    def dialog_html(record: dict[str, Any], qa_json: dict[str, Any]) -> str:
        text = str(qa_json.get("Транскрибация") or "").strip()
        if not text:
            text = format_transcript_with_roles(
                str(record.get("transcript_text") or ""),
                direction=str(record.get("direction") or ""),
            )
        else:
            text = repair_role_transcript(text, direction=str(record.get("direction") or ""))
        text = re.sub(r"^\s*Очищенная расшифровка с ролями\s*:\s*", "", text, flags=re.IGNORECASE)
        turns = dialog_turns(text)
        if not turns:
            return '<p class="muted">Транскрипта пока нет.</p>'
        bubbles = []
        for role, label, message in turns:
            bubbles.append(
                f"""
                <div class="dialog-line {role}">
                  <span>{html.escape(label)}</span>
                  <p>{html.escape(message)}</p>
                </div>
                """
            )
        return "".join(bubbles)

    def dialog_turns(text: str) -> list[tuple[str, str, str]]:
        turns: list[tuple[str, str, str]] = []
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            matches = list(re.finditer(r"(?i)(Менеджер|Клиент)\s*:", line))
            if not matches:
                turns.append(("unknown", "Реплика", line))
                continue
            prefix = line[: matches[0].start()].strip()
            if prefix:
                turns.append(("unknown", "Реплика", prefix))
            for index, match in enumerate(matches):
                start = match.end()
                end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
                message = line[start:end].strip()
                if not message:
                    continue
                role = "manager" if match.group(1).casefold() == "менеджер" else "client"
                label = "Менеджер" if role == "manager" else "Клиент"
                turns.append((role, label, message))
        return turns

    def qa_score_cards(analysis: dict[str, Any] | None) -> str:
        metrics = (analysis or {}).get("metrics") or {}
        score_blocks = metrics.get("score_blocks") or {}
        if not isinstance(score_blocks, dict) or not score_blocks:
            return '<p class="muted">Детальная оценка появится после нового анализа.</p>'
        cards = []
        for label, item in score_blocks.items():
            if not isinstance(item, dict):
                continue
            cards.append(
                f"""
                <div class="qa-metric-card">
                  <span>{html.escape(str(label))}</span>
                  <strong>{int(item.get('score') or 0)}/{int(item.get('max_score') or 0)}</strong>
                </div>
                """
            )
        return "".join(cards)

    def qa_percent_cards(analysis: dict[str, Any] | None) -> str:
        metrics = (analysis or {}).get("metrics") or {}
        percent_blocks = metrics.get("percent_blocks") or {}
        if not isinstance(percent_blocks, dict) or not percent_blocks:
            return ""
        cards = []
        for label, value in percent_blocks.items():
            cards.append(
                f"""
                <div class="qa-percent-row">
                  <span>{html.escape(str(label))}</span>
                  <strong>{int(value or 0)}%</strong>
                </div>
                """
            )
        return "".join(cards)

    def qa_report_metrics_html(analysis: dict[str, Any] | None) -> str:
        report = ((analysis or {}).get("metrics") or {}).get("report_metrics") or {}
        if not isinstance(report, dict) or not report:
            qa = (((analysis or {}).get("analysis") or {}).get("qa_json") or {})
            if isinstance(qa, dict) and qa:
                report = {
                    "outcome": {
                        "booking": qa.get("Запись на замер") or "не определено",
                        "refusal_reason": qa.get("Причина отказа") or "не определено",
                        "next_step": qa.get("Следующий шаг") or "не определено",
                        "probability": qa.get("Вероятность продажи") or "не определено",
                        "probability_explanation": qa.get("Объяснение оценки (вероятность продажи)") or qa.get("Объяснение оценки") or "",
                    },
                    "client_profile": {
                        "decision_maker": qa.get("ЛПР?") or "не определено",
                    },
                    "offer_fit": {
                        "fit": qa.get("Предложение соответствует потребностям") or "не определено",
                        "closed_needs": qa.get("Потребности закрыли") or [],
                        "unclosed_needs": qa.get("Потребности НЕ закрыли") or [],
                    },
                    "manager_skills": {
                        "Установление контакта": {"percent": qa.get("Установление контакта (%)"), "explanation": qa.get("Объяснение (Установление контакта)")},
                        "Выявление потребностей": {"percent": qa.get("Выявление потребностей (%)"), "explanation": qa.get("Объяснение (Выявление потребностей)")},
                        "Усиление боли": {"percent": qa.get("Усиление боли (%)"), "explanation": qa.get("Объяснение (Усиление болей)")},
                        "Презентация": {"percent": qa.get("Презентация (%)"), "explanation": qa.get("Объяснение оценки (Презентация)")},
                        "Отработка возражений": {"percent": qa.get("Отработка возражений (%)"), "explanation": qa.get("Объяснение (Отработка возражений)")},
                    },
                    "leadership": {
                        "leader": qa.get("Кто лидер?") or "не определено",
                        "reason": qa.get("Лидер, почему") or "",
                    },
                }
        if not isinstance(report, dict) or not report:
            return '<p class="muted">Показатели появятся после нового LLM-анализа.</p>'

        def value(section: str, key: str, default: str = "не определено") -> str:
            block = report.get(section) or {}
            if not isinstance(block, dict):
                return default
            text = str(block.get(key) or "").strip()
            return text or default

        def list_html(section: str, key: str) -> str:
            block = report.get(section) or {}
            items = block.get(key) if isinstance(block, dict) else []
            if not isinstance(items, list):
                items = [items] if str(items or "").strip() else []
            cleaned = [str(item).strip() for item in items if str(item or "").strip()]
            if not cleaned:
                return '<p class="muted">не определено</p>'
            return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in cleaned[:8]) + "</ul>"

        def pct(value: Any) -> int:
            match = re.search(r"-?\d+(?:[.,]\d+)?", str(value or ""))
            if not match:
                return 0
            return max(0, min(100, round(float(match.group(0).replace(",", ".")))))

        skills = report.get("manager_skills") or {}
        skill_rows = []
        if isinstance(skills, dict):
            for label, item in skills.items():
                if not isinstance(item, dict):
                    continue
                percent = pct(item.get("percent"))
                explanation = str(item.get("explanation") or "").strip()
                skill_rows.append(f"""
                  <div class="report-skill-row">
                    <div><strong>{html.escape(str(label))}</strong><p>{html.escape(explanation or 'не определено')}</p></div>
                    <b>{percent}%</b>
                  </div>
                """)
        return f"""
        <div class="report-metrics">
          <div class="report-card">
            <h4>Исход звонка</h4>
            <dl class="qa-dl">
              <dt>Запись на замер</dt><dd>{html.escape(value('outcome', 'booking'))}</dd>
              <dt>Причина отказа</dt><dd>{html.escape(value('outcome', 'refusal_reason'))}</dd>
              <dt>Вероятность</dt><dd>{html.escape(value('outcome', 'probability'))}</dd>
              <dt>Почему</dt><dd>{html.escape(value('outcome', 'probability_explanation', ''))}</dd>
            </dl>
          </div>
          <div class="report-card">
            <h4>Попадание в потребности</h4>
            <dl class="qa-dl">
              <dt>Соответствие</dt><dd>{html.escape(value('offer_fit', 'fit'))}</dd>
              <dt>Закрыли</dt><dd>{list_html('offer_fit', 'closed_needs')}</dd>
              <dt>Не закрыли</dt><dd>{list_html('offer_fit', 'unclosed_needs')}</dd>
            </dl>
          </div>
          <div class="report-card">
            <h4>Лидерство</h4>
            <dl class="qa-dl">
              <dt>Кто лидер</dt><dd>{html.escape(value('leadership', 'leader'))}</dd>
              <dt>Почему</dt><dd>{html.escape(value('leadership', 'reason', ''))}</dd>
              <dt>ЛПР</dt><dd>{html.escape(value('client_profile', 'decision_maker'))}</dd>
            </dl>
          </div>
          <div class="report-card report-skills">
            <h4>Навыки менеджера</h4>
            {''.join(skill_rows) or '<p class="muted">не определено</p>'}
          </div>
        </div>
        """

    def qa_checklist_html(analysis: dict[str, Any] | None) -> str:
        checklist = ((analysis or {}).get("metrics") or {}).get("checklist")
        if not isinstance(checklist, dict):
            return '<p class="muted">Чек-лист появится после нового LLM-анализа.</p>'
        items = checklist.get("items") or []
        if not isinstance(items, list) or not items:
            return '<p class="muted">Чек-лист появится после нового LLM-анализа.</p>'
        raw_score = int(checklist.get("raw_score") or 0)
        max_score = int(checklist.get("max_score") or 147)
        normalized = int(checklist.get("normalized_score") or 0)
        errors = [str(item).strip() for item in checklist.get("critical_errors") or [] if str(item or "").strip()]
        error_html = (
            "".join(f'<span class="checklist-tag">{html.escape(error)}</span>' for error in errors[:8])
            if errors
            else '<span class="muted">критичных тегов нет</span>'
        )
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append(
                f"""
                <tr>
                  <td>{int(item.get('id') or 0)}</td>
                  <td>
                    <strong>{html.escape(str(item.get('criterion') or 'Критерий'))}</strong><br>
                    <span class="muted">{html.escape(str(item.get('comment') or ''))}</span>
                    {f'<br><span class="muted">Фрагмент: {html.escape(str(item.get("evidence") or ""))}</span>' if str(item.get('evidence') or '').strip() else ''}
                  </td>
                  <td><strong>{int(item.get('score') or 0)}/{int(item.get('max_score') or 0)}</strong></td>
                  <td>{html.escape(str(item.get('status') or '-'))}<br><span class="muted">{html.escape(str(item.get('critical_tag') or ''))}</span></td>
                </tr>
                """
            )
        return f"""
        <div class="checklist-summary">
          <div><span>Чек-лист</span><strong>{raw_score}/{max_score}</strong></div>
          <div><span>Итог</span><strong>{normalized}/100</strong></div>
          <div><span>Ошибки</span><div class="checklist-tags">{error_html}</div></div>
        </div>
        <div class="table-wrap checklist-wrap">
          <table class="checklist-table">
            <thead><tr><th>№</th><th>Критерий</th><th>Балл</th><th>Статус</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>
        """

    def lead_link(record: dict[str, Any]) -> str:
        lead_id = record.get("lead_id")
        if not lead_id:
            return '<span class="muted">не привязана</span>'
        subdomain = settings.subdomain or settings.account_key
        return (
            f'<a href="https://{html.escape(subdomain)}.{html.escape(settings.base_domain)}/leads/detail/'
            f'{html.escape(str(lead_id))}" target="_blank" rel="noreferrer">{html.escape(str(lead_id))}</a>'
        )

    def action_form(record: dict[str, Any], analysis: dict[str, Any] | None) -> str:
        if not analysis:
            metadata = record.get("metadata") or {}
            raw_params = metadata.get("raw_params") if isinstance(metadata, dict) else {}
            call_result = str((raw_params or {}).get("call_result") or "").strip()
            has_recording = bool(record.get("recording_url"))
            duration = int(record.get("duration_seconds") or 0)
            if not has_recording and duration <= 0:
                hint = call_result or "нет аудиозаписи"
                return f'<span class="muted">нет записи<br>{html.escape(hint)}</span>'
            return '<span class="muted">нужен анализ</span>'
        if not record.get("lead_id"):
            return '<span class="muted">нет сделки</span>'
        posted_note_id = record.get("metadata", {}).get("last_posted_note_id")
        disabled = " disabled" if posted_note_id else ""
        label = "Записано" if disabled else "Записать в сделку"
        posted_hint = f'<br><span class="muted">заметка {html.escape(str(posted_note_id))}</span>' if posted_note_id else ""
        return f"""
        <form class="conversation-note-form" method="post" action="/api/conversations/post-note?{suffix}">
          <input type="hidden" name="conversation_id" value="{html.escape(str(record.get('conversation_id')))}">
          <button type="submit"{disabled}>{html.escape(label)}</button>
          {posted_hint}
        </form>
        """

    def note_preview(record: dict[str, Any], analysis: dict[str, Any] | None) -> str:
        if not analysis:
            return '<span class="muted">Анализ еще не готов.</span>'
        return html.escape(build_lead_analysis_note(record, analysis))

    def view_link(name: str, label: str) -> str:
        active = " active" if view == name else ""
        q_part = f"&q={quote(search_text)}" if search_text else ""
        return (
            f'<a class="filter-tab{active}" href="/conversations?{suffix}&view={name}{q_part}">'
            f'{html.escape(label)} <strong>{int(view_counts[name])}</strong></a>'
        )

    tabs_html = "".join([
        view_link("work", "Рабочая лента"),
        view_link("analyzed", "С анализом"),
        view_link("posted", "Записано"),
        view_link("errors", "Ошибки"),
        view_link("all", "Все"),
    ])

    def source_label(record: dict[str, Any]) -> str:
        metadata = record.get("metadata") or {}
        phone = str(metadata.get("phone") or "").strip()
        source = str(metadata.get("source") or "").strip()
        parts = [item for item in [source, phone] if item]
        return " · ".join(parts) or "-"

    def stage_snapshot(record: dict[str, Any]) -> str:
        metadata = record.get("metadata") or {}
        lead_at_call = metadata.get("lead_at_call") if isinstance(metadata, dict) else None
        if not isinstance(lead_at_call, dict) or not lead_at_call:
            return '<span class="muted">этап на момент звонка не сохранен</span>'
        pipeline_id = str(lead_at_call.get("pipeline_id") or "")
        status_id = str(lead_at_call.get("status_id") or "")
        responsible_id = str(lead_at_call.get("responsible_user_id") or "")
        pipeline_name = pipeline_labels.get(pipeline_id) or "Воронка не найдена"
        status_name = status_labels.get(status_id) or "Этап не найден"
        responsible_name = user_labels.get(responsible_id) or "Ответственный не найден"
        return (
            f"воронка {html.escape(pipeline_name)}, "
            f"этап {html.escape(status_name)}, "
            f"ответственный {html.escape(responsible_name)}"
        )

    def v2_manager_call_card(record: dict[str, Any], analysis: dict[str, Any] | None) -> str:
        payload, metrics, call_analysis = v2_data(analysis)
        snapshot = payload.get("checklist_snapshot") if isinstance(payload.get("checklist_snapshot"), dict) else {}
        snapshot_steps = snapshot.get("steps") if isinstance(snapshot.get("steps"), list) else []
        raw_steps = call_analysis.get("steps") if isinstance(call_analysis.get("steps"), dict) else {}
        outcome = metrics.get("outcome") or call_analysis.get("outcome") or "не_применимо"
        color = metrics.get("outcome_color") or "gray"
        quality = str(metrics.get("quality_display") or "—").strip() or "—"
        coach_tip = str(call_analysis.get("coach_tip") or "").strip()

        metadata = record.get("metadata") or {}
        lead_at_call = metadata.get("lead_at_call") if isinstance(metadata, dict) else None
        responsible_id = str((lead_at_call or {}).get("responsible_user_id") or "").strip() if isinstance(lead_at_call, dict) else ""
        manager = user_labels.get(responsible_id) or "Ответственный не найден"

        rows = []
        for step in snapshot_steps:
            if not isinstance(step, dict):
                continue
            slug = str(step.get("slug") or "").strip()
            if not slug:
                continue
            step_result = raw_steps.get(slug) if isinstance(raw_steps.get(slug), dict) else {}
            ok = step_result.get("ok") is True
            label = str(step.get("label") or slug).strip()
            quote = str(step_result.get("quote") or "").strip()
            quote_html = (
                f'<p class="v2-check-quote">{html.escape(quote)}</p>'
                if quote and not ok
                else ""
            )
            rows.append(
                f"""
                <li class="v2-check-item {'passed' if ok else 'failed'}">
                  <span class="v2-check-icon">{'✓' if ok else '×'}</span>
                  <div>
                    <strong>{html.escape(label)}</strong>
                    {quote_html}
                  </div>
                </li>
                """
            )

        checklist_html = (
            '<ul class="v2-check-list">' + "".join(rows) + "</ul>"
            if rows
            else '<p class="muted">Чек-лист в снапшоте пуст.</p>'
        )
        coach_html = (
            f'<div class="v2-coach-tip"><strong>Подтянуть:</strong> {html.escape(coach_tip)}</div>'
            if coach_tip
            else ""
        )
        excluded_html = (
            '<span class="v2-conversion-note">исключён из конверсии</span>'
            if metrics.get("conversion_excluded")
            else ""
        )
        return f"""
        <section class="wide-section v2-call-card">
          <div class="v2-card-head">
            <div class="v2-card-meta">
              <div><span>Менеджер</span><strong>{html.escape(manager)}</strong></div>
              <div><span>Сделка</span><strong>{lead_link(record)}</strong></div>
              <div><span>Дата</span><strong>{html.escape(_format_datetime(record.get('occurred_at')))}</strong></div>
              <div><span>Направление</span><strong>{html.escape(direction_label(record.get('direction')))}</strong></div>
              <div><span>Длительность</span><strong>{html.escape(duration_label(record.get('duration_seconds')))}</strong></div>
            </div>
            <div class="v2-card-badges">
              {v2_outcome_badge(outcome, color)}
              <span class="v2-quality-badge">Качество {html.escape(quality)}</span>
              {excluded_html}
            </div>
          </div>
          <div class="v2-check-block">
            <h3>Чек-лист разговора</h3>
            {checklist_html}
          </div>
          {coach_html}
        </section>
        """

    def conversation_status_label(record: dict[str, Any]) -> str:
        metadata = record.get("metadata") or {}
        raw_params = metadata.get("raw_params") if isinstance(metadata, dict) else {}
        call_result = str((raw_params or {}).get("call_result") or "").strip()
        if str(record.get("status") or "") == "metadata_only" and call_result:
            return call_result
        return _label_status(record.get("status"))

    rows = []
    for index, record in enumerate(filtered_records):
        analysis = analysis_by_conversation.get(str(record.get("conversation_id")))
        qa_json = qa_payload(analysis)
        transcript = str(record.get("transcript_text") or "").strip()
        summary = analysis.get("summary") if analysis else transcript
        score = score_label(analysis)
        sentiment = sentiment_label(analysis.get("sentiment") if analysis else None)
        score_html = v2_score_cell(analysis, score, sentiment)
        recommendations = analysis.get("recommendations") if analysis else []
        recommendation_text = "<br>".join(html.escape(str(item)) for item in recommendations[:3])
        if not recommendation_text:
            recommendation_text = '<span class="muted">пока нет</span>'
        detail_id = f"conversation-detail-{index}"
        dialog_id = f"conversation-dialog-{index}"
        posted_note_id = (record.get("metadata") or {}).get("last_posted_note_id")
        open_button = f'<button type="button" class="secondary-button mini-button" data-toggle-detail="{detail_id}">Открыть</button>'
        posted_badge = f'<span class="status-badge active">заметка {html.escape(str(posted_note_id))}</span>' if posted_note_id else ""
        actions_html = posted_badge or action_form(record, analysis)
        if is_v2_analysis(analysis):
            detail_sections = f"""
                {v2_manager_call_card(record, analysis)}
                <section>
                  <h3>Заметка в сделку</h3>
                  <pre>{note_preview(record, analysis)}</pre>
                </section>
                <section class="dialog-section">
                  <div class="dialog-section-head">
                    <h3>Диалог</h3>
                    <button type="button" class="secondary-button mini-button" data-toggle-dialog="{dialog_id}">Показать диалог</button>
                  </div>
                  <div id="{dialog_id}" class="dialog-transcript" hidden>{dialog_html(record, qa_json)}</div>
                </section>
            """
        else:
            detail_sections = f"""
                <section>
                  <h3>Оценка и вывод</h3>
                  <p class="score-line"><strong>{html.escape(score)}</strong> <span>{html.escape(sentiment)}</span></p>
                  <p>{html.escape(str(analysis.get('summary') if analysis else 'Анализ еще не готов.'))}</p>
                  <div class="qa-metric-grid">{qa_score_cards(analysis)}</div>
                  <div class="qa-percent-list">{qa_percent_cards(analysis)}</div>
                </section>
                <section>
                  <h3>Что делать дальше</h3>
                  <dl class="qa-dl">
                    <dt>Следующий шаг</dt><dd>{html.escape(qa_value(qa_json, 'Следующий шаг', str(analysis.get('next_step') if analysis else 'не определено')))}</dd>
                    <dt>Вероятность продажи</dt><dd>{html.escape(qa_value(qa_json, 'Вероятность продажи'))}</dd>
                    <dt>Как продать</dt><dd>{html.escape(qa_value(qa_json, 'Как продать?'))}</dd>
                    <dt>Как улучшить</dt><dd>{html.escape(qa_value(qa_json, 'Как улучшить'))}</dd>
                  </dl>
                  <div class="qa-columns qa-recommendations">
                    <div><h4>Менеджеру</h4>{qa_list(qa_json, 'Рекомендации менеджеру')}</div>
                    <div><h4>РОПу</h4>{qa_list(qa_json, 'Рекомендации РОП')}</div>
                  </div>
                </section>
                <section class="wide-section">
                  <h3>Чек-лист AI</h3>
                  {qa_checklist_html(analysis)}
                </section>
                <section class="wide-section">
                  <h3>Показатели отчета</h3>
                  {qa_report_metrics_html(analysis)}
                </section>
                <section>
                  <h3>Факты и потребности</h3>
                  <div class="qa-columns">
                    <div><h4>Факты</h4>{qa_list(qa_json, 'Факты')}</div>
                    <div><h4>Потребности</h4>{qa_list(qa_json, 'Потребности')}</div>
                    <div><h4>Боли</h4>{qa_list(qa_json, 'Боли')}</div>
                    <div><h4>Возражения</h4>{qa_list(qa_json, 'Возражения')}</div>
                  </div>
                </section>
                <section>
                  <h3>Заметка в сделку</h3>
                  <pre>{note_preview(record, analysis)}</pre>
                </section>
                <section class="dialog-section">
                  <div class="dialog-section-head">
                    <h3>Диалог</h3>
                    <button type="button" class="secondary-button mini-button" data-toggle-dialog="{dialog_id}">Показать диалог</button>
                  </div>
                  <div id="{dialog_id}" class="dialog-transcript" hidden>{dialog_html(record, qa_json)}</div>
                </section>
            """
        rows.append(f"""
        <tr>
          <td>
            <strong>{html.escape(str(record.get('conversation_id')))}</strong><br>
            <span class="muted">ID звонка</span>
          </td>
          <td><strong>{html.escape(_format_datetime(record.get('occurred_at')))}</strong></td>
          <td>{lead_link(record)}<br><span class="muted">контакт {html.escape(str(record.get('contact_id') or '-'))}</span></td>
          <td>
            <span class="status-badge {html.escape(str(record.get('status') or 'pending'))}">{html.escape(conversation_status_label(record))}</span><br>
            <span class="muted">{html.escape(str(record.get('metadata', {}).get('source') or ''))}</span>
          </td>
          <td>{html.escape(direction_label(record.get('direction')))}<br><span class="muted">Длительность: {html.escape(duration_label(record.get('duration_seconds')))}</span></td>
          <td>{score_html}</td>
          <td>{html.escape(compact(summary, 220))}</td>
          <td>{recommendation_text}</td>
          <td><div class="conversation-actions">{open_button}{actions_html}</div></td>
        </tr>
        <tr id="{detail_id}" class="conversation-details-row" hidden>
          <td colspan="9">
            <div class="conversation-drawer">
              <div class="drawer-head">
                <div>
                  <strong>{html.escape(str(record.get('conversation_id')))}</strong>
                  <span class="muted">{html.escape(source_label(record))}</span>
                </div>
                <div>{lead_link(record)}</div>
              </div>
              <div class="drawer-meta">
                <span>Звонок был: {html.escape(_format_datetime(record.get('occurred_at')))}</span>
                <span>На момент звонка: {stage_snapshot(record)}</span>
                <span>Длительность: {html.escape(duration_label(record.get('duration_seconds')))}</span>
                <span>Статус: {html.escape(_label_status(record.get('status')))}</span>
              </div>
              <div class="conversation-details">
                {detail_sections}
              </div>
            </div>
          </td>
        </tr>
        """)

    rows_html = "".join(rows) or '<tr><td colspan="8" class="muted">В этой вкладке пока нет разговоров.</td></tr>'
    body = f"""
      <style>
        .conversation-stats {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
        .conversation-stat {{ padding: 14px; border: 1px solid #d9e7f5; border-radius: 8px; background: #fff; }}
        .conversation-stat span {{ display: block; color: #607089; font-size: 12px; }}
        .conversation-stat strong {{ display: block; margin-top: 4px; font-size: 24px; font-weight: 600; color: #223047; }}
        .conversation-table {{ min-width: 1420px; table-layout: fixed; }}
        .conversation-actions {{ display: grid; gap: 7px; align-items: stretch; min-width: 0; }}
        .conversation-actions form {{ margin: 0; min-width: 0; }}
        .conversation-actions button {{ width: 100%; min-width: 0; min-height: 36px; padding: 6px 10px; white-space: normal; line-height: 1.25; text-align: center; }}
        .conversation-actions .status-badge {{ justify-content: center; white-space: normal; text-align: center; }}
        .conversation-table-wrap {{ position: relative; }}
        .conversation-details-row td {{ position: sticky; left: 0; z-index: 2; width: min(1420px, calc(100vw - 96px)); background: #f6f9fc; padding-top: 0; }}
        .conversation-details {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 12px; }}
        .conversation-details .wide-section {{ grid-column: 1 / -1; }}
        .conversation-details pre {{ white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; padding: 12px; border: 1px solid #d9e7f5; border-radius: 8px; background: #f7fbff; color: #334155; font-family: inherit; }}
        .conversation-drawer {{ padding: 16px; border: 1px solid #d9e7f5; border-radius: 8px; background: #fff; }}
        .drawer-head, .drawer-meta {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px; }}
        .drawer-meta {{ margin-top: 10px; color: #607089; }}
        .score-line strong {{ font-size: 26px; font-weight: 600; color: #223047; }}
        .score-line span {{ margin-left: 8px; color: #607089; }}
        .v2-score-pill {{ display: inline-flex; align-items: center; gap: 7px; max-width: 100%; padding: 6px 9px; border: 1px solid #d9e7f5; border-radius: 999px; background: #f7fbff; color: #334155; font-weight: 600; line-height: 1.25; white-space: normal; }}
        .v2-dot {{ width: 8px; height: 8px; border-radius: 999px; background: currentColor; flex: 0 0 auto; }}
        .v2-call-card {{ padding: 14px; border: 1px solid #cfe4f8; border-radius: 8px; background: #f7fbff; }}
        .v2-card-head {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 14px; align-items: start; }}
        .v2-card-meta {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }}
        .v2-card-meta > div {{ min-width: 0; padding: 10px; border: 1px solid #d9e7f5; border-radius: 8px; background: #fff; }}
        .v2-card-meta span {{ display: block; color: #607089; font-size: 12px; }}
        .v2-card-meta strong {{ display: block; margin-top: 4px; color: #223047; font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }}
        .v2-card-badges {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; max-width: 280px; }}
        .v2-badge, .v2-quality-badge, .v2-conversion-note {{ display: inline-flex; align-items: center; min-height: 30px; padding: 0 10px; border-radius: 999px; border: 1px solid #d9e7f5; background: #fff; font-weight: 600; }}
        .v2-quality-badge {{ background: #eef7ff; color: #155f9d; border-color: #b9defb; }}
        .v2-conversion-note {{ background: #f8fafc; color: #607089; font-weight: 500; }}
        .v2-outcome-green {{ background: #ecfdf5; border-color: #bbf7d0; color: #0f766e; }}
        .v2-outcome-yellow {{ background: #fffbeb; border-color: #fde68a; color: #a16207; }}
        .v2-outcome-red {{ background: #fff1f2; border-color: #fecdd3; color: #be123c; }}
        .v2-outcome-gray {{ background: #f8fafc; border-color: #dbe4ee; color: #607089; }}
        .v2-check-block {{ margin-top: 14px; }}
        .v2-check-block h3 {{ margin: 0 0 8px; }}
        .v2-check-list {{ display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }}
        .v2-check-item {{ display: grid; grid-template-columns: 26px minmax(0, 1fr); gap: 9px; padding: 10px; border: 1px solid #d9e7f5; border-radius: 8px; background: #fff; }}
        .v2-check-icon {{ display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 999px; font-weight: 600; }}
        .v2-check-item.passed .v2-check-icon {{ background: #dcfce7; color: #0f8b5f; }}
        .v2-check-item.failed .v2-check-icon {{ background: #ffe4e6; color: #be123c; }}
        .v2-check-item strong {{ font-weight: 600; color: #223047; }}
        .v2-check-quote {{ margin: 4px 0 0; color: #607089; font-size: 13px; line-height: 1.35; }}
        .v2-coach-tip {{ margin-top: 12px; padding: 11px 12px; border: 1px solid #fde68a; border-radius: 8px; background: #fffbeb; color: #744f00; }}
        .v2-coach-tip strong {{ font-weight: 600; }}
        .qa-metric-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }}
        .qa-metric-card {{ padding: 10px; border: 1px solid #d9e7f5; border-radius: 8px; background: #f7fbff; }}
        .qa-metric-card span {{ display: block; color: #607089; font-size: 12px; }}
        .qa-metric-card strong {{ display: block; margin-top: 4px; font-size: 18px; font-weight: 600; }}
        .qa-percent-list {{ margin-top: 12px; border-top: 1px solid #edf3f8; }}
        .qa-percent-row {{ display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; border-bottom: 1px solid #edf3f8; }}
        .qa-dl {{ margin: 0; }}
        .qa-dl dt {{ margin-top: 10px; color: #607089; font-weight: 600; }}
        .qa-dl dd {{ margin: 3px 0 0; }}
        .qa-columns {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
        .qa-columns h4 {{ margin-bottom: 6px; }}
        .qa-columns ul {{ margin: 0; padding-left: 18px; }}
        .qa-recommendations {{ margin-top: 14px; }}
        .qa-recommendations > div {{ padding: 10px; border: 1px solid #d9e7f5; border-radius: 8px; background: #f7fbff; }}
        .checklist-summary {{ display: grid; grid-template-columns: 160px 140px minmax(0, 1fr); gap: 10px; margin-bottom: 10px; }}
        .checklist-summary > div {{ padding: 10px; border: 1px solid #d9e7f5; border-radius: 8px; background: #f7fbff; }}
        .checklist-summary span {{ display: block; color: #607089; font-size: 12px; }}
        .checklist-summary strong {{ display: block; margin-top: 4px; font-size: 18px; }}
        .checklist-tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }}
        .checklist-tag {{ display: inline-flex; align-items: center; min-height: 24px; padding: 0 8px; border: 1px solid #fecdd3; border-radius: 999px; background: #fff1f2; color: #b42337; font-size: 12px; }}
        .checklist-wrap {{ border: 1px solid #d9e7f5; border-radius: 8px; }}
        .checklist-table {{ min-width: 980px; table-layout: fixed; }}
        .checklist-table th:nth-child(1), .checklist-table td:nth-child(1) {{ width: 48px; text-align: center; }}
        .checklist-table th:nth-child(3), .checklist-table td:nth-child(3) {{ width: 92px; text-align: center; }}
        .checklist-table th:nth-child(4), .checklist-table td:nth-child(4) {{ width: 190px; }}
        .checklist-table td {{ white-space: normal; }}
        .report-metrics {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
        .report-card {{ padding: 12px; border: 1px solid #d9e7f5; border-radius: 8px; background: #f7fbff; }}
        .report-card h4 {{ margin: 0 0 8px; font-size: 15px; font-weight: 600; }}
        .report-card ul {{ margin: 0; padding-left: 18px; }}
        .report-skill-row {{ display: grid; grid-template-columns: minmax(0, 1fr) 58px; gap: 10px; padding: 8px 0; border-bottom: 1px solid #dbeafe; }}
        .report-skill-row:last-child {{ border-bottom: 0; }}
        .report-skill-row strong {{ font-weight: 600; }}
        .report-skill-row p {{ margin: 3px 0 0; color: #607089; }}
        .report-skill-row b {{ align-self: start; text-align: right; font-weight: 600; color: #155f9d; }}
        .dialog-section-head {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; }}
        .dialog-section-head h3 {{ margin: 0; }}
        .dialog-transcript {{ display: grid; gap: 6px; }}
        .dialog-transcript[hidden] {{ display: none; }}
        .dialog-line {{ display: grid; grid-template-columns: 86px 1fr; gap: 8px; width: 100%; padding: 7px 9px; border: 1px solid #d9e7f5; border-radius: 6px; background: #f7fbff; }}
        .dialog-line span {{ color: #607089; font-size: 12px; font-weight: 600; line-height: 1.35; }}
        .dialog-line p {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.35; }}
        .dialog-line.manager {{ border-left: 3px solid #2f9fe5; }}
        .dialog-line.client {{ border-left: 3px solid #43b581; background: #f2fbf7; }}
        .conversation-settings-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
        .settings-section {{ margin-top: 18px; padding-top: 16px; border-top: 1px solid #edf3f8; }}
        .settings-section > p {{ color: #607089; margin-top: 4px; max-width: 860px; }}
        .filter-grid {{ display: grid; grid-template-columns: 1fr 320px; gap: 18px; align-items: start; }}
        .selector-pair {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
        .selector-pair select, .responsible-select select {{ min-height: 190px; }}
        label small {{ display: block; margin-top: 6px; color: #607089; font-weight: 400; }}
        .empty-selector {{ padding: 14px; border: 1px solid #d9e7f5; border-radius: 8px; background: #f7fbff; color: #607089; }}
        .empty-selector strong, .empty-selector span {{ display: block; }}
        .module-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }}
        .module-card {{ display: grid; grid-template-columns: 22px 1fr; gap: 10px; min-height: 68px; padding: 10px 12px; border: 1px solid #d9e7f5; border-radius: 8px; background: #fff; }}
        .module-card small {{ display: block; margin-top: 3px; color: #607089; line-height: 1.3; }}
        .scoring-table {{ max-width: 760px; }}
        .advanced-settings summary {{ cursor: pointer; color: #334155; }}
        .conversation-toolbar {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }}
        .list-toolbar {{ display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }}
        .filter-tabs {{ display: flex; flex-wrap: wrap; gap: 8px; }}
        .filter-tab {{ display: inline-flex; align-items: center; gap: 6px; min-height: 34px; padding: 0 10px; border: 1px solid #d9e7f5; border-radius: 8px; color: #476075; text-decoration: none; font-weight: 500; background: #fff; }}
        .filter-tab.active {{ background: #e8f4ff; color: #1677c7; border-color: #b9defb; }}
        .list-search {{ display: flex; gap: 8px; align-items: center; }}
        .list-search input {{ width: 260px; min-height: 36px; }}
        .mini-button {{ min-height: 34px; padding: 0 10px; border-radius: 8px; }}
        .selection-summary {{ display: inline-flex; align-items: center; min-height: 34px; padding: 0 10px; border-radius: 8px; background: #eef7ff; color: #476075; font-weight: 500; }}
        .notice-panel {{ border-color: #bbf7d0; background: #f0fdf4; }}
        .error-notice {{ border-color: #fecaca; background: #fff1f2; }}
        .auto-status {{ margin-top: 14px; padding: 12px 14px; border: 1px solid #dbe4ee; border-radius: 8px; background: #f8fafc; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
        .auto-status span {{ display: block; color: #64748b; font-size: 12px; }}
        .auto-status strong {{ display: block; margin-top: 3px; }}
        @media (max-width: 900px) {{
          .conversation-stats, .conversation-details, .conversation-settings-grid, .filter-grid, .selector-pair, .module-grid, .auto-status, .checklist-summary, .report-metrics, .v2-card-head, .v2-card-meta {{ grid-template-columns: 1fr; }}
          .v2-card-badges {{ justify-content: flex-start; max-width: none; }}
          .list-search input {{ width: 100%; }}
        }}
      </style>
      {_account_nav(settings, "conversations")}
      {notice}
      <section class="panel">
        <div class="panel-header">
          <div>
            <h1>Разговоры</h1>
            <p>Звонки, расшифровки, анализ и рекомендации по аккаунту <code>{html.escape(settings.account_key)}</code>.</p>
          </div>
          <a class="button secondary-button" href="/api/conversations?{suffix}">JSON</a>
        </div>
        <div class="conversation-stats">
          <div class="conversation-stat"><span>Всего разговоров</span><strong>{stats['total']}</strong></div>
          <div class="conversation-stat"><span>Расшифровано</span><strong>{stats['transcribed']}</strong></div>
          <div class="conversation-stat"><span>С анализом</span><strong>{stats['analyzed']}</strong></div>
          <div class="conversation-stat"><span>Привязано к сделкам</span><strong>{stats['linked']}</strong></div>
        </div>
        <div class="auto-status">
          <div><span>Автообработка</span><strong>{html.escape(auto_status)}</strong></div>
          <div><span>Фоновый worker</span><strong>{'работает' if background.get('thread_alive') else 'остановлен'}</strong></div>
          <div><span>Последний прогон</span><strong>{html.escape(auto_last_run)}</strong></div>
          <div><span>Результат</span><strong>{html.escape(str(auto_summary))}</strong></div>
        </div>
      </section>
      <section class="panel">
        <details open>
          <summary><strong>Настройки автообработки</strong></summary>
          <form method="post" action="/api/conversations/settings?{suffix}">
            <div class="settings-section">
              <h3>Кого обрабатывать</h3>
              <p>Фильтр определяет, какие новые звонки попадут в автообработку. Если ничего не выбрать, модуль берет все новые звонки аккаунта с учетом длительности.</p>
              <div class="filter-grid">
                <div>
                  <h4>Воронки и этапы amoCRM</h4>
                  {pipeline_selects()}
                </div>
                <div class="responsible-select">
                  <h4>Ответственные</h4>
                  {responsible_select()}
                </div>
              </div>
              <input type="hidden" name="new_calls_only" value="1">
              <p class="selection-summary" data-selection-summary>{html.escape(selected_summary)}</p>
              <p class="muted">Исторические звонки не берутся: первый боевой запуск ставит точку старта, дальше обрабатываются только новые звонки после нее.</p>
              <div class="grid">
                <label>Минимальная длительность, сек.
                  <input name="min_duration_seconds" type="number" min="0" value="{int(ci_settings['filters']['min_duration_seconds'])}">
                </label>
                <label>Максимальная длительность, сек.
                  <input name="max_duration_seconds" type="number" min="0" value="{int(ci_settings['filters']['max_duration_seconds'])}">
                </label>
              </div>
            </div>
            <div class="settings-section">
              <h3>Что делать с найденными звонками</h3>
              <p>Каждый модуль можно включать отдельно. Вместе они образуют цепочку: найти звонок, скачать запись, расшифровать, проанализировать, выгрузить и при необходимости записать рекомендацию в сделку.</p>
              <div class="module-grid">
                {action_card("enabled")}
                {action_card("import_leads")}
                {action_card("probe_recordings")}
                {action_card("download_recordings")}
                {action_card("transcribe")}
                {action_card("analyze")}
                {action_card("post_note")}
                {action_card("export_google_sheets")}
              </div>
            </div>
            <div class="settings-section">
              <h3>Как анализировать</h3>
              <p>Промпт описывает роль аналитика и ожидаемый результат. Система оценок задает критерии, по которым потом можно строить отчеты и сравнивать менеджеров/воронки.</p>
              <div class="conversation-settings-grid">
                <label>Режим анализа
                  <select name="external_analysis_mode">
                    <option value="openrouter_raw"{' selected' if external_mode == 'openrouter_raw' else ''}>OpenRouter LLM без обезличивания</option>
                    <option value="anonymized_openrouter"{' selected' if external_mode == 'anonymized_openrouter' else ''}>OpenRouter LLM после обезличивания</option>
                    <option value="local"{' selected' if external_mode == 'local' else ''}>Локальный fallback</option>
                  </select>
                  <small>Основной режим: LLM без обезличивания. Локальный fallback нужен только для диагностики без API.</small>
                </label>
                <label>Модель OpenRouter
                  <input name="external_analysis_model" value="{html.escape(external_model)}" placeholder="openai/gpt-4o-mini">
                  <small>Пусто = модель из OPENROUTER_ANALYSIS_MODEL или openai/gpt-4o-mini.</small>
                </label>
              </div>
              <label>Промпт анализа
                <textarea name="analysis_prompt" rows="7">{html.escape(str(ci_settings.get('analysis_prompt') or ''))}</textarea>
              </label>
              <div class="table-wrap scoring-table">
                <table>
                  <thead><tr><th>Показатель</th><th>Максимум</th></tr></thead>
                  <tbody>{scoring_rows or '<tr><td colspan="2" class="muted">Показатели не заданы</td></tr>'}</tbody>
                </table>
              </div>
              <details class="advanced-settings">
                <summary>Расширенная настройка системы оценок</summary>
                <label>JSON критериев
                  <textarea name="scoring_json" rows="8">{html.escape(scoring_json)}</textarea>
                </label>
              </details>
            </div>
            <div class="settings-section">
              <h3>Куда выгружать</h3>
              <p>CSV уже готовится локально в формате, который открывает Google Sheets. Когда подключим доступ к конкретной таблице, эти же поля будут использоваться для прямой записи.</p>
              <div class="conversation-settings-grid">
                <label>Google spreadsheet id
                  <input name="spreadsheet_id" value="{html.escape(str(ci_settings['google_sheets'].get('spreadsheet_id') or ''))}">
                </label>
                <label>Лист Google Sheets
                  <input name="worksheet_name" value="{html.escape(str(ci_settings['google_sheets'].get('worksheet_name') or ''))}">
                </label>
              </div>
            </div>
            <div class="conversation-toolbar">
              <button type="submit" name="next_action" value="save">Сохранить настройки</button>
              <button type="submit" name="next_action" value="dry_run" class="secondary-button">Сохранить и проверить фильтры</button>
              <button type="submit" name="next_action" value="auto_run">Сохранить и запустить автообработку</button>
              <button type="submit" name="next_action" value="reanalyze" class="secondary-button">Пересчитать анализ через LLM</button>
              <button type="submit" name="next_action" value="export" class="secondary-button">Сохранить и экспортировать CSV</button>
            </div>
          </form>
          <script>
            (() => {{
              const form = document.querySelector('form[action^="/api/conversations/settings"]');
              const summary = document.querySelector('[data-selection-summary]');
              if (!form || !summary) return;
              const count = (name) => Array.from(form.querySelectorAll(`[name="${{name}}"] option:checked`)).length;
              const render = () => {{
                summary.textContent = `Выбрано: воронок ${{count('pipeline_ids')}}, этапов ${{count('status_ids')}}, ответственных ${{count('responsible_user_ids')}}.`;
              }};
              form.querySelectorAll('select[multiple]').forEach((select) => select.addEventListener('change', render));
              render();
            }})();
          </script>
        </details>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>Последние звонки</h2>
          <span class="muted">{len(filtered_records)} строк</span>
        </div>
        <div class="list-toolbar">
          <nav class="filter-tabs">{tabs_html}</nav>
          <form class="list-search" method="get" action="/conversations">
            <input type="hidden" name="user" value="{html.escape(settings.user_key)}">
            <input type="hidden" name="account" value="{html.escape(settings.account_key)}">
            <input type="hidden" name="view" value="{html.escape(view)}">
            <input name="q" value="{html.escape(search_text)}" placeholder="Сделка, телефон, текст">
            <button type="submit" class="secondary-button">Найти</button>
          </form>
        </div>
        <div class="table-wrap conversation-table-wrap">
          <table class="conversation-table">
            <colgroup>
              <col style="width: 14%">
              <col style="width: 9%">
              <col style="width: 9%">
              <col style="width: 10%">
              <col style="width: 10%">
              <col style="width: 8%">
              <col style="width: 18%">
              <col style="width: 12%">
              <col style="width: 10%">
            </colgroup>
            <thead><tr><th>Разговор</th><th>Дата звонка</th><th>Сделка</th><th>Статус</th><th>Направление</th><th>Оценка</th><th>Кратко</th><th>Рекомендации</th><th>Действие</th></tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>
        <script>
          (() => {{
            document.querySelectorAll('[data-toggle-detail]').forEach((button) => {{
              button.addEventListener('click', () => {{
                const row = document.getElementById(button.dataset.toggleDetail);
                if (!row) return;
                row.hidden = !row.hidden;
                button.textContent = row.hidden ? 'Открыть' : 'Скрыть';
              }});
            }});
            document.querySelectorAll('[data-toggle-dialog]').forEach((button) => {{
              button.addEventListener('click', () => {{
                const dialog = document.getElementById(button.dataset.toggleDialog);
                if (!dialog) return;
                dialog.hidden = !dialog.hidden;
                button.textContent = dialog.hidden ? 'Показать диалог' : 'Скрыть диалог';
              }});
            }});
          }})();
        </script>
      </section>
    """
    return _page_shell(f"Разговоры {settings.account_key}", body)


def _render_freshness_page(settings: Any) -> str:
    repo = _repo(settings)
    freshness = FreshnessService(repo).dashboard(settings.account_key)
    suffix = _account_suffix(settings)
    status_labels = {
        "fresh": "Свежие",
        "stale": "Устаревают",
        "critical": "Критично",
        "missing": "Нет данных",
    }

    def age_label(minutes: Any) -> str:
        if minutes is None:
            return "нет"
        minutes = int(minutes)
        if minutes < 60:
            return f"{minutes} мин"
        hours = minutes // 60
        rest = minutes % 60
        if hours < 24:
            return f"{hours} ч {rest} мин" if rest else f"{hours} ч"
        days = hours // 24
        return f"{days} дн {hours % 24} ч" if hours % 24 else f"{days} дн"

    entity_cards = "".join(
        f"""
        <article class="fresh-card {html.escape(row['status'])}">
          <div class="fresh-card-head">
            <strong>{html.escape(str(row['label']))}</strong>
            <span>{html.escape(status_labels.get(row['status'], row['status']))}</span>
          </div>
          <div class="fresh-count">{int(row['items_count'])}</div>
          <div class="fresh-meta">
            <span>Обновлено: {html.escape(_format_datetime(row.get('last_synced_at')))}</span>
            <span>Возраст: {html.escape(age_label(row.get('age_minutes')))}</span>
            <span>Норма: до {html.escape(age_label(row.get('max_age_minutes')))}</span>
          </div>
        </article>
        """
        for row in freshness["entities"]
    )
    job_rows = "".join(
        f"""
        <tr>
          <td>#{int(job['id'])}</td>
          <td>{html.escape(_label_job_type(job.get('job_type')))}</td>
          <td>{html.escape(_label_status(job.get('status')))}</td>
          <td>{int(job.get('items_count') or 0)}</td>
          <td>{int(job.get('failed_count') or 0)}</td>
          <td>{html.escape(_format_datetime(job.get('started_at')))}</td>
          <td>{html.escape(_format_datetime(job.get('finished_at')))}</td>
        </tr>
        """
        for job in freshness["latest_jobs"]
    ) or '<tr><td colspan="7" class="muted">Задач синхронизации пока нет</td></tr>'
    active_jobs = freshness["active_jobs"]
    active_text = (
        f"идет задача #{int(active_jobs[0]['id'])}: {_label_job_type(active_jobs[0].get('job_type'))}"
        if active_jobs
        else "нет активных задач"
    )
    body = f"""
      <style>
        .fresh-hero {{ display: grid; grid-template-columns: 260px 1fr; gap: 16px; }}
        .fresh-health {{ display: grid; place-items: center; min-height: 190px; border: 1px solid #dbe4ee; border-radius: 8px; background: #f8fafc; text-align: center; }}
        .fresh-health strong {{ display: block; font-size: 36px; font-weight: 600; }}
        .fresh-grid {{ display: grid; grid-template-columns: repeat(3, minmax(220px, 1fr)); gap: 12px; }}
        .fresh-card {{ padding: 15px; border: 1px solid #dbe4ee; border-left: 5px solid #64748b; border-radius: 8px; background: #fff; }}
        .fresh-card.fresh {{ border-left-color: #0f8b5f; }}
        .fresh-card.stale {{ border-left-color: #d97706; }}
        .fresh-card.critical, .fresh-card.missing {{ border-left-color: #dc2626; }}
        .fresh-card-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; }}
        .fresh-card-head span {{ color: #66758a; font-weight: 500; }}
        .fresh-count {{ margin: 12px 0; font-size: 30px; font-weight: 600; }}
        .fresh-meta {{ display: grid; gap: 5px; color: #66758a; }}
        .fresh-actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
        @media (max-width: 980px) {{ .fresh-hero, .fresh-grid {{ grid-template-columns: 1fr; }} }}
      </style>
      {_account_nav(settings, "freshness")}
      <section class="panel fresh-hero">
        <div class="fresh-health">
          <div>
            <strong>{html.escape(status_labels.get(freshness['health'], freshness['health']))}</strong>
            <span class="muted">состояние хаба</span>
          </div>
        </div>
        <div>
          <h1>Актуальность данных</h1>
          <p>Здесь видно, когда локальный хаб подхватил сделки, задачи, события, примечания и справочники. Если источник устарел, рабочий стол надо читать осторожно.</p>
          <p><strong>Сейчас:</strong> {html.escape(active_text)}</p>
          <div class="fresh-actions">
            <a class="button" href="/api/freshness?{suffix}">JSON</a>
            <a class="button secondary-button" href="/app?{suffix}">Выгрузки аккаунта</a>
          </div>
        </div>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>Сущности хаба</h2>
          <span class="muted">{html.escape(_format_datetime(freshness['generated_at']))}</span>
        </div>
        <div class="fresh-grid">{entity_cards}</div>
      </section>
      <section class="panel">
        <h2>Последние sync job</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>ID</th><th>Тип</th><th>Статус</th><th>Элементов</th><th>Ошибок</th><th>Старт</th><th>Финиш</th></tr></thead>
            <tbody>{job_rows}</tbody>
          </table>
        </div>
      </section>
    """
    return _page_shell("Актуальность данных", body)


def _render_quality_settings_page(settings: Any) -> str:
    repo = _repo(settings)
    raw_settings = load_account_settings(
        user_key=settings.user_key,
        account_key=settings.account_key,
        data_root=settings.data_root,
    )
    current = quality_settings(raw_settings)
    filters = current["filters"]
    rules = current["rules"]
    suffix = _account_suffix(settings)
    pipeline_options = AnalyticsService(repo).pipeline_filter_options()
    selected_pipelines = {int(item) for item in filters.get("pipeline_ids") or []}
    selected_statuses = {int(item) for item in filters.get("status_ids") or []}
    ignored_statuses = {int(item) for item in filters.get("ignored_status_ids") or []}

    def checked(value: bool) -> str:
        return " checked" if value else ""

    pipeline_blocks = "".join(
        f"""
        <article class="filter-block">
          <label class="toggle-line">
            <input type="checkbox" name="pipeline_id_{int(pipeline['pipeline_id'])}" value="1"{checked(not selected_pipelines or int(pipeline['pipeline_id']) in selected_pipelines)}>
            <strong>{html.escape(str(pipeline['pipeline_name']))}</strong>
          </label>
          <div class="status-grid-mini">
            {''.join(
                f'''
                <label>
                  <input type="checkbox" name="status_id_{int(status['status_id'])}" value="1"{checked(not selected_statuses or int(status['status_id']) in selected_statuses)}>
                  <span>{html.escape(str(status['status_name']))}</span>
                </label>
                <label class="ignore-status">
                  <input type="checkbox" name="ignored_status_id_{int(status['status_id'])}" value="1"{checked(int(status['status_id']) in ignored_statuses)}>
                  <span>игнор</span>
                </label>
                '''
                for status in pipeline.get('statuses') or []
            )}
          </div>
        </article>
        """
        for pipeline in pipeline_options
    ) or '<div class="empty-state">Воронки появятся после синхронизации pipelines.</div>'
    body = f"""
      <style>
        .settings-layout {{ display: grid; grid-template-columns: 320px 1fr; gap: 16px; align-items: start; }}
        .settings-aside {{ position: sticky; top: 16px; }}
        .filter-block {{ padding: 14px; border: 1px solid #dbe4ee; border-radius: 8px; background: #fff; margin-bottom: 10px; }}
        .toggle-line {{ display: flex; grid-template-columns: none; gap: 10px; align-items: center; color: #223047; }}
        .status-grid-mini {{ display: grid; grid-template-columns: minmax(220px, 1fr) 90px; gap: 8px; margin-top: 10px; }}
        .status-grid-mini label {{ display: flex; gap: 8px; align-items: center; min-height: 30px; color: #334155; font-weight: 600; }}
        .ignore-status {{ color: #94a3b8 !important; font-size: 12px; }}
        .rule-grid {{ display: grid; gap: 10px; }}
        .rule-card {{ padding: 12px; border: 1px solid #dbe4ee; border-radius: 8px; background: #f8fafc; }}
        .rule-card label {{ display: flex; gap: 10px; align-items: center; color: #223047; }}
        @media (max-width: 980px) {{ .settings-layout {{ grid-template-columns: 1fr; }} .settings-aside {{ position: static; }} }}
      </style>
      {_account_nav(settings, "quality-settings")}
      <section class="panel">
        <h1>Фильтры контроля</h1>
        <p>Эти настройки определяют, какие сделки попадают в рабочий стол РОПа и по каким правилам считаются риски.</p>
      </section>
      <form method="post" action="/quality-settings/save?{suffix}">
        <div class="settings-layout">
          <aside class="panel settings-aside">
            <h2>Порог и правила</h2>
            <label>Сделка зависла после, дней
              <input type="number" min="1" max="90" name="stale_lead_days" value="{int(current['stale_lead_days'])}">
            </label>
            <label>Максимум рисков на экране
              <input type="number" min="10" max="1000" name="max_risks" value="{int(current['max_risks'])}">
            </label>
            <div class="rule-grid">
              <div class="rule-card"><label><input type="checkbox" name="rule_overdue_tasks" value="1"{checked(rules.get('overdue_tasks'))}> Просроченные задачи</label></div>
              <div class="rule-card"><label><input type="checkbox" name="rule_missing_next_task" value="1"{checked(rules.get('missing_next_task'))}> Сделки без следующей задачи</label></div>
              <div class="rule-card"><label><input type="checkbox" name="rule_stale_leads" value="1"{checked(rules.get('stale_leads'))}> Сделки без активности</label></div>
            </div>
            <p><button type="submit">Сохранить фильтры</button></p>
          </aside>
          <section class="panel">
            <h2>Воронки и этапы</h2>
            <p class="muted">Если воронки или этапы не выбраны явно, контроль смотрит все, кроме этапов в колонке “игнор”.</p>
            {pipeline_blocks}
          </section>
        </div>
      </form>
    """
    return _page_shell("Фильтры контроля", body)


def _render_quality_page(settings: Any, query_string: str = "") -> str:
    repo = _repo(settings)
    query = parse_qs(query_string)
    raw_settings = load_account_settings(
        user_key=settings.user_key,
        account_key=settings.account_key,
        data_root=settings.data_root,
    )
    current_quality_settings = quality_settings(raw_settings)
    try:
        stale_days = int((query.get("stale_days") or [str(current_quality_settings["stale_lead_days"])])[0])
    except ValueError:
        stale_days = int(current_quality_settings["stale_lead_days"])
    current_quality_settings["stale_lead_days"] = stale_days
    summary = QualityService(repo).summary(
        stale_lead_days=stale_days,
        max_risks=int(current_quality_settings["max_risks"]),
        settings=current_quality_settings,
    )
    suffix = _account_suffix(settings)
    quality_suffix = f"{suffix}&stale_days={int(summary['settings']['stale_lead_days'])}"

    severity_labels = {
        "critical": "Критично",
        "warning": "Внимание",
        "info": "Инфо",
    }
    type_labels = {
        "overdue_task": "Просрочена задача",
        "lead_without_open_task": "Нет следующей задачи",
        "stale_lead": "Давно без активности",
    }

    def risk_type(value: Any) -> str:
        text = str(value or "")
        return type_labels.get(text, text.replace("_", " "))

    def severity(value: Any) -> str:
        text = str(value or "")
        return severity_labels.get(text, text)

    def lead_url(risk: dict[str, Any]) -> str:
        lead_id = str(risk.get("lead_id") or "").strip()
        if not lead_id or not settings.subdomain:
            return ""
        return f"https://{settings.subdomain}.{settings.base_domain}/leads/detail/{lead_id}"

    def lead_anchor(risk: dict[str, Any]) -> str:
        name = html.escape(str(risk.get("lead_name") or "Без сделки"))
        lead_id = html.escape(str(risk.get("lead_id") or ""))
        href = lead_url(risk)
        title = f"<strong>{name}</strong><br><span class=\"muted\">#{lead_id}</span>"
        if href:
            return f'<a class="lead-link" href="{html.escape(href)}" target="_blank" rel="noopener">{title}</a>'
        return title

    def age_text(risk: dict[str, Any]) -> str:
        hours = int(risk.get("age_hours") or 0)
        if hours >= 48:
            return f"{hours // 24} дн."
        if hours >= 1:
            return f"{hours} ч."
        return "сейчас"

    def risk_card(risk: dict[str, Any]) -> str:
        href = lead_url(risk)
        action = (
            f'<a class="button tiny-button" href="{html.escape(href)}" target="_blank" rel="noopener">Открыть</a>'
            if href
            else ""
        )
        return f"""
          <article class="focus-card {html.escape(str(risk['severity']))}">
            <div class="focus-card-top">
              <span class="quality-badge {html.escape(str(risk['severity']))}">{html.escape(severity(risk['severity']))}</span>
              <span class="risk-age">{html.escape(age_text(risk))}</span>
            </div>
            <h3>{html.escape(str(risk['title']))}</h3>
            <div class="focus-lead">{lead_anchor(risk)}</div>
            <p>{html.escape(str(risk.get('recommendation') or risk['detail']))}</p>
            <div class="focus-meta">
              <span>{html.escape(str(risk.get('responsible_user_name') or 'Без ответственного'))}</span>
              {action}
            </div>
          </article>
        """

    max_penalty = max([int(row.get("score_penalty") or 0) for row in summary["by_user"]] or [1])
    manager_cards = "".join(
        f"""
        <article class="manager-card">
          <div class="manager-card-head">
            <strong>{html.escape(str(row['responsible_user_name']))}</strong>
            <span>{int(row['risks'])} рисков</span>
          </div>
          <div class="risk-meter"><span style="width: {max(4, min(100, round(int(row['score_penalty']) / max_penalty * 100)))}%"></span></div>
          <div class="manager-card-stats">
            <span><b>{int(row['critical'])}</b> критично</span>
            <span><b>{int(row['warning'])}</b> внимание</span>
            <span><b>{int(row['score_penalty'])}</b> штраф</span>
          </div>
        </article>
        """
        for row in summary["by_user"][:8]
    ) or '<div class="empty-state">Рисков по менеджерам пока нет</div>'

    focus_cards = "".join(risk_card(risk) for risk in summary["risks"][:6]) or (
        '<div class="empty-state">Критичных сигналов пока нет. После синхронизации leads/tasks/notes здесь появится рабочая очередь.</div>'
    )
    signal_chips = "".join(
        f"""
        <div class="signal-chip">
          <span>{html.escape(risk_type(row['type']))}</span>
          <strong>{int(row['count'])}</strong>
        </div>
        """
        for row in summary["by_type"]
    ) or '<div class="empty-state">Нет сигналов</div>'

    user_rows = "".join(
        f"""
        <tr>
          <td><strong>{html.escape(str(row['responsible_user_name']))}</strong><br><span class="muted">{html.escape(str(row.get('responsible_user_id') or 'без id'))}</span></td>
          <td>{int(row['risks'])}</td>
          <td>{int(row['critical'])}</td>
          <td>{int(row['warning'])}</td>
          <td>{int(row['score_penalty'])}</td>
        </tr>
        """
        for row in summary["by_user"]
    ) or '<tr><td colspan="5" class="muted">Рисков по менеджерам пока нет</td></tr>'
    type_rows = "".join(
        f"<tr><td>{html.escape(risk_type(row['type']))}</td><td>{int(row['count'])}</td></tr>"
        for row in summary["by_type"]
    ) or '<tr><td colspan="2" class="muted">Нет сигналов</td></tr>'

    risk_rows = "".join(
        f"""
        <tr>
          <td><span class="quality-badge {html.escape(str(risk['severity']))}">{html.escape(severity(risk['severity']))}</span></td>
          <td>{html.escape(risk_type(risk['type']))}<br><span class="muted">вес {int(risk['weight'])}</span></td>
          <td>{lead_anchor(risk)}</td>
          <td>{html.escape(str(risk.get('responsible_user_name') or 'Без ответственного'))}</td>
          <td>{html.escape(str(risk.get('pipeline_name') or ''))}<br><span class="muted">{html.escape(str(risk.get('status_name') or ''))}</span></td>
          <td>{html.escape(_format_datetime(risk.get('due_at') or risk.get('last_activity_at') or risk.get('lead_updated_at')))}</td>
          <td>{html.escape(str(risk['detail']))}<br><span class="muted">{html.escape(str(risk.get('recommendation') or ''))}</span></td>
        </tr>
        """
        for risk in summary["risks"]
    ) or '<tr><td colspan="7" class="muted">Критичных сигналов пока нет. После синхронизации leads/tasks/notes здесь появится контроль гигиены.</td></tr>'

    score_class = "good" if int(summary["health_score"]) >= 80 else "warn" if int(summary["health_score"]) >= 55 else "bad"
    score_color = "#0f8b5f" if score_class == "good" else "#ad6b00" if score_class == "warn" else "#c2410c"
    totals = summary["totals"]
    body = f"""
      <style>
        .quality-hero {{
          display: grid;
          grid-template-columns: minmax(220px, 320px) 1fr;
          gap: 18px;
          align-items: stretch;
        }}
        .quality-score {{
          display: grid;
          grid-template-rows: auto 1fr;
          gap: 18px;
        }}
        .score-dial {{
          min-height: 260px;
          display: grid;
          place-items: center;
          border-radius: 8px;
          background:
            radial-gradient(circle at center, #fff 0 54%, transparent 55%),
            conic-gradient({score_color} 0 {int(summary['health_score'])}%, #e5edf5 {int(summary['health_score'])}% 100%);
          border: 1px solid #d9e3ef;
          text-align: center;
        }}
        .score-dial strong {{ display: block; font-size: 64px; line-height: 1; }}
        .score-dial.good strong {{ color: #0f8b5f; }}
        .score-dial.warn strong {{ color: #ad6b00; }}
        .score-dial.bad strong {{ color: #c2410c; }}
        .quality-grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; }}
        .quality-chip {{ padding: 14px; border: 1px solid #d9e3ef; border-radius: 8px; background: #fff; }}
        .quality-chip strong {{ display: block; font-size: 28px; overflow-wrap: anywhere; }}
        .signal-strip {{ display: grid; grid-template-columns: repeat(3, minmax(150px, 1fr)); gap: 10px; }}
        .signal-chip {{
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          padding: 13px 14px;
          border: 1px solid #d9e3ef;
          border-radius: 8px;
          background: #f8fafc;
        }}
        .signal-chip strong {{ font-size: 24px; }}
        .focus-grid {{ display: grid; grid-template-columns: repeat(3, minmax(220px, 1fr)); gap: 14px; }}
        .focus-card {{
          display: flex;
          flex-direction: column;
          gap: 10px;
          min-height: 220px;
          padding: 16px;
          border: 1px solid #d9e3ef;
          border-left: 5px solid #94a3b8;
          border-radius: 8px;
          background: #fff;
        }}
        .focus-card.critical {{ border-left-color: #dc2626; }}
        .focus-card.warning {{ border-left-color: #d97706; }}
        .focus-card-top, .focus-meta {{
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 10px;
        }}
        .focus-card h3 {{ margin: 0; font-size: 17px; }}
        .focus-card p {{ margin: 0; color: #4b5d73; }}
        .focus-lead {{ padding: 10px; border-radius: 8px; background: #f8fafc; }}
        .lead-link {{ color: inherit; text-decoration: none; }}
        .lead-link:hover strong {{ color: #0f6b8f; }}
        .risk-age {{ color: #66758a; font-weight: 500; }}
        .tiny-button {{ padding: 7px 10px; min-height: auto; }}
        .manager-grid {{ display: grid; grid-template-columns: repeat(4, minmax(180px, 1fr)); gap: 12px; }}
        .manager-card {{
          padding: 14px;
          border: 1px solid #d9e3ef;
          border-radius: 8px;
          background: #fff;
        }}
        .manager-card-head, .manager-card-stats {{
          display: flex;
          justify-content: space-between;
          gap: 10px;
          align-items: center;
        }}
        .manager-card-head span, .manager-card-stats span {{ color: #66758a; font-size: 13px; }}
        .manager-card-stats {{ margin-top: 10px; align-items: flex-start; }}
        .manager-card-stats b {{ display: block; color: #223047; font-size: 18px; font-weight: 600; }}
        .risk-meter {{ height: 10px; margin-top: 12px; border-radius: 999px; background: #e5edf5; overflow: hidden; }}
        .risk-meter span {{ display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #d97706, #dc2626); }}
        .empty-state {{ padding: 18px; border: 1px dashed #cbd5e1; border-radius: 8px; color: #66758a; background: #f8fafc; }}
        .quality-badge {{ display: inline-flex; padding: 5px 9px; border-radius: 999px; font-size: 12px; font-weight: 500; }}
        .quality-badge.critical {{ color: #7f1d1d; background: #fee2e2; }}
        .quality-badge.warning {{ color: #7c4a03; background: #fef3c7; }}
        .quality-badge.info {{ color: #0f3a73; background: #dbeafe; }}
        .quality-table {{ min-width: 1180px; table-layout: fixed; }}
        .quality-filter {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: end; margin-top: 14px; }}
        .quality-filter label {{ min-width: 180px; }}
        @media (max-width: 1180px) {{
          .focus-grid, .manager-grid {{ grid-template-columns: repeat(2, minmax(220px, 1fr)); }}
          .quality-grid, .signal-strip {{ grid-template-columns: repeat(2, minmax(150px, 1fr)); }}
        }}
        @media (max-width: 860px) {{
          .quality-hero, .quality-grid, .signal-strip, .focus-grid, .manager-grid {{ grid-template-columns: 1fr; }}
        }}
      </style>
      {_account_nav(settings, "quality")}
      <section class="panel">
        <div class="panel-header">
          <div>
            <h1>Контроль гигиены продаж</h1>
            <p>Первый слой контроля по локальному зеркалу amoCRM: задачи, открытые сделки, активность и следующий шаг.</p>
          </div>
          <div class="actions">
            <a class="button" href="/api/quality/summary?{quality_suffix}">JSON</a>
            <a class="button secondary-button" href="/quality-settings?{suffix}">Фильтры</a>
          </div>
        </div>
        <form class="quality-filter" method="get" action="/quality">
          <input type="hidden" name="user" value="{html.escape(settings.user_key)}">
          <input type="hidden" name="account" value="{html.escape(settings.account_key)}">
          <label>Сделка зависла после, дней<input type="number" min="1" max="60" name="stale_days" value="{int(summary['settings']['stale_lead_days'])}"></label>
          <button type="submit">Пересчитать</button>
        </form>
      </section>
      <section class="panel quality-hero">
        <div class="score-dial {score_class}">
          <div><strong>{int(summary['health_score'])}</strong><span class="muted">индекс здоровья</span></div>
        </div>
        <div class="quality-score">
          <div class="quality-grid">
            <div class="quality-chip"><strong>{int(totals['risks'])}</strong><span class="muted">всего рисков</span></div>
            <div class="quality-chip"><strong>{int(totals['critical'])}</strong><span class="muted">критичных</span></div>
            <div class="quality-chip"><strong>{int(totals['open_leads'])}</strong><span class="muted">открытых сделок</span></div>
            <div class="quality-chip"><strong>{int(totals['open_tasks'])}</strong><span class="muted">открытых задач</span></div>
          </div>
          <div class="signal-strip">{signal_chips}</div>
        </div>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>Фокус на сейчас</h2>
          <span class="muted">первые сделки, которые стоит открыть</span>
        </div>
        <div class="focus-grid">{focus_cards}</div>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>Менеджеры под контролем</h2>
          <span class="muted">по суммарному весу сигналов</span>
        </div>
        <div class="manager-grid">{manager_cards}</div>
      </section>
      <section class="grid">
        <article class="panel">
          <h2>Менеджеры таблицей</h2>
          <table>
            <thead><tr><th>Менеджер</th><th>Рисков</th><th>Критично</th><th>Внимание</th><th>Штраф score</th></tr></thead>
            <tbody>{user_rows}</tbody>
          </table>
        </article>
        <article class="panel">
          <h2>Типы сигналов</h2>
          <table>
            <thead><tr><th>Тип</th><th>Кол-во</th></tr></thead>
            <tbody>{type_rows}</tbody>
          </table>
        </article>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>Список рисков</h2>
          <span class="muted">{len(summary['risks'])} записей</span>
        </div>
        <div class="table-wrap">
          <table class="quality-table">
            <colgroup>
              <col style="width: 8%">
              <col style="width: 14%">
              <col style="width: 18%">
              <col style="width: 13%">
              <col style="width: 14%">
              <col style="width: 12%">
              <col style="width: 21%">
            </colgroup>
            <thead><tr><th>Статус</th><th>Сигнал</th><th>Сделка</th><th>Менеджер</th><th>Воронка/этап</th><th>Дата</th><th>Деталь</th></tr></thead>
            <tbody>{risk_rows}</tbody>
          </table>
        </div>
      </section>
    """
    return _page_shell("Контроль гигиены продаж", body)


def _render_activity_page(settings: Any, query_string: str = "") -> str:
    repo = _repo(settings)
    query = parse_qs(query_string)
    target_date = (query.get("date") or [""])[0] or None
    activity = ActivityService(repo).dashboard(days=7, limit=120, target_date=target_date, slot_minutes=15)
    totals = activity["totals"]
    pulse = activity["pulse"]
    pulse_totals = pulse["totals"]
    suffix = _account_suffix(settings)
    dated_suffix = f"{suffix}&date={html.escape(pulse['date'])}"

    def minutes(value: int) -> str:
        hours = value // 60
        rest = value % 60
        if hours and rest:
            return f"{hours}ч {rest}м"
        if hours:
            return f"{hours}ч"
        return f"{rest}м"

    category_labels = {
        "calls_out": "Исходящие звонки",
        "calls_in": "Входящие звонки",
        "calls_missed": "Пропущенные звонки",
        "tasks_completed": "Выполненные задачи",
        "tasks_touched": "Задачи",
        "leads_created": "Созданные сделки",
        "stage_changes": "Смены этапов",
        "notes": "Заметки",
        "field_changes": "Изменения полей",
        "webhooks": "Webhook",
        "other": "Другое",
    }

    action_labels = {
        "outgoing call": "Исходящий звонок",
        "incoming call": "Входящий звонок",
        "task completed": "Задача выполнена",
        "task updated": "Задача обновлена",
        "lead status changed": "Смена этапа сделки",
        "lead added": "Создана сделка",
        "common note added": "Добавлена заметка",
        "task result added": "Результат задачи",
    }

    def label_action(value: Any) -> str:
        text = str(value)
        return action_labels.get(text, text)

    def label_category(value: Any) -> str:
        text = str(value)
        return category_labels.get(text, text)

    def rows(items: list[dict[str, Any]], label_key: str) -> str:
        return "".join(
            f"<tr><td>{html.escape(str(item[label_key]))}</td><td>{int(item['count'])}</td><td>{int(item.get('score') or 0)}</td></tr>"
            for item in items
        ) or '<tr><td colspan="3" class="muted">Пока нет данных</td></tr>'

    def pulse_cells(user: dict[str, Any]) -> str:
        labels = pulse["slot_labels"]
        cells = []
        for index, slot in enumerate(user["slots"]):
            label = labels[index]
            title = (
                f"{label}: {slot['count']} действий, "
                f"индекс {slot['score']}"
            )
            cells.append(
                f'<span class="pulse-cell level-{int(slot["level"])}" title="{html.escape(title)}"></span>'
            )
        return "".join(cells)

    def idle_text(user: dict[str, Any]) -> str:
        periods = user.get("idle_periods") or []
        if not periods:
            return '<span class="muted">нет</span>'
        first = periods[0]
        extra = len(periods) - 1
        tail = f" +{extra}" if extra > 0 else ""
        return f"{html.escape(first['from'])}-{html.escape(first['to'])} ({minutes(int(first['minutes']))}){tail}"

    user_rows = "".join(
        f"""
        <tr>
          <td>
            <strong>{html.escape(str(user['user_name']))}</strong>
            <br><span class="muted">{html.escape(str(user.get('user_id') or 'system'))}</span>
          </td>
          <td><strong>{int(user['activity_score'])}</strong><br><span class="muted">{int(user['activity_count'])} действий</span></td>
          <td>{minutes(int(user['active_minutes']))}<br><span class="muted">{html.escape(user['first_activity'] or '—')} - {html.escape(user['last_activity'] or '—')}</span></td>
          <td>{minutes(int(user['idle_minutes']))}<br><span class="muted">{idle_text(user)}</span></td>
          <td>{int(user['tasks_completed'])}/{int(user['tasks_due'])}<br><span class="muted">просрочено {int(user['tasks_overdue'])}</span></td>
          <td>исх {int(user['calls_out'])}<br>вх {int(user['calls_in'])}<br><span class="muted">проп {int(user['calls_missed'])}</span></td>
          <td>{int(user['leads_created'])} сделок<br>{int(user['stage_changes'])} этапов<br><span class="muted">{int(user['notes'])} заметок</span></td>
          <td><div class="pulse-strip">{pulse_cells(user)}</div></td>
        </tr>
        """
        for user in pulse["users"]
    ) or '<tr><td colspan="8" class="muted">На выбранную дату активности пока нет. Запусти первичную выгрузку или обновление активности.</td></tr>'

    timeline = "".join(
        f"""
        <tr>
          <td>{html.escape(_format_datetime(item['happened_at']))}</td>
          <td>{html.escape(str(item['user_name']))}</td>
          <td>{html.escape(label_action(item['action']))}</td>
          <td>{html.escape(label_category(item['category']))}</td>
          <td>{int(item['weight'])}</td>
          <td>{html.escape(_label_entity(item['entity_type']))} {html.escape(str(item.get('entity_id') or ''))}</td>
          <td>{html.escape(str(item['title']))}</td>
          <td>{html.escape(_label_source(item['source']))}</td>
        </tr>
        """
        for item in activity["timeline"]
    ) or '<tr><td colspan="8" class="muted">Активность появится после синхронизации events/tasks/notes или после входящих webhook.</td></tr>'

    body = f"""
      <style>
        .activity-filter {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: end; margin-top: 14px; }}
        .activity-filter label {{ min-width: 180px; }}
        .pulse-table {{ min-width: 1260px; table-layout: fixed; }}
        .pulse-strip {{ display: grid; grid-template-columns: repeat(96, minmax(3px, 1fr)); gap: 2px; min-width: 520px; }}
        .pulse-cell {{ height: 26px; border-radius: 4px; background: #e5edf5; }}
        .pulse-cell.level-1 {{ background: #bfe4f7; }}
        .pulse-cell.level-2 {{ background: #69c2dd; }}
        .pulse-cell.level-3 {{ background: #1f9f88; }}
        .pulse-cell.level-4 {{ background: #096c5a; }}
        .legend {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; color: #66758a; }}
        .legend span {{ display: inline-flex; align-items: center; gap: 5px; }}
        .legend i {{ width: 18px; height: 10px; display: inline-block; border-radius: 3px; }}
        .activity-table {{ min-width: 1320px; table-layout: fixed; }}
        @media (max-width: 720px) {{ .pulse-strip {{ min-width: 420px; }} }}
      </style>
      {_account_nav(settings, "activity")}
      <section class="panel">
        <div class="panel-header">
          <div>
            <h1>Активность пользователей</h1>
            <p>Пульс CRM-работы по 15-минутным окнам: задачи, звонки, сделки, заметки, смены этапов и простои.</p>
          </div>
          <div class="actions">
            <button type="button" data-activity-sync>Обновить активность</button>
            <a class="button" href="/api/activity/summary?{dated_suffix}">JSON</a>
          </div>
        </div>
        <form class="activity-filter" method="get" action="/activity">
          <input type="hidden" name="user" value="{html.escape(settings.user_key)}">
          <input type="hidden" name="account" value="{html.escape(settings.account_key)}">
          <label>Дата<input type="date" name="date" value="{html.escape(pulse['date'])}"></label>
          <button type="submit">Показать день</button>
        </form>
        <div class="job-status" data-job-status hidden></div>
      </section>
      <section class="grid">
        <article class="panel"><h2>Индекс дня</h2><h1>{pulse_totals['activity_score']}</h1><p>{pulse_totals['activity_count']} действий за {html.escape(pulse['date'])}</p></article>
        <article class="panel"><h2>Активных менеджеров</h2><h1>{pulse_totals['active_users']}</h1><p>с действиями в CRM</p></article>
        <article class="panel"><h2>Простои</h2><h1>{pulse_totals['idle_periods']}</h1><p>паузы от 30 минут между действиями</p></article>
        <article class="panel"><h2>Звонки</h2><h1>{pulse_totals['calls_total']}</h1><p>исх {pulse_totals['calls_out']} · вх {pulse_totals['calls_in']} · проп {pulse_totals['calls_missed']}</p></article>
        <article class="panel"><h2>Задачи</h2><h1>{pulse_totals['tasks_completed']}/{pulse_totals['tasks_due']}</h1><p>выполнено / поставлено на день · просрочено {pulse_totals['tasks_overdue']}</p></article>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>Пульс по менеджерам</h2>
          <div class="legend">
            <span><i class="pulse-cell level-0"></i>тишина</span>
            <span><i class="pulse-cell level-1"></i>слабая</span>
            <span><i class="pulse-cell level-3"></i>активная</span>
            <span><i class="pulse-cell level-4"></i>пик</span>
          </div>
        </div>
        <div class="table-wrap">
          <table class="pulse-table">
            <colgroup>
              <col style="width: 12%">
              <col style="width: 8%">
              <col style="width: 10%">
              <col style="width: 10%">
              <col style="width: 9%">
              <col style="width: 8%">
              <col style="width: 10%">
              <col style="width: 33%">
            </colgroup>
            <thead><tr><th>Менеджер</th><th>Индекс</th><th>Активное время</th><th>Простой</th><th>Задачи</th><th>Звонки</th><th>CRM</th><th>00:00 - 24:00</th></tr></thead>
            <tbody>{user_rows}</tbody>
          </table>
        </div>
      </section>
      <section class="grid">
        <article class="panel">
          <h2>По пользователям</h2>
          <table><thead><tr><th>Пользователь</th><th>Действий</th><th>Индекс</th></tr></thead><tbody>{rows(activity['by_user'], 'user_name')}</tbody></table>
        </article>
        <article class="panel">
          <h2>По типам действий</h2>
          <table><thead><tr><th>Действие</th><th>Кол-во</th><th>Индекс</th></tr></thead><tbody>{rows(activity['by_action'], 'action')}</tbody></table>
        </article>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h2>Лента последних действий</h2>
          <span class="muted">{len(activity['timeline'])} записей</span>
        </div>
        <div class="table-wrap">
          <table class="activity-table">
            <colgroup>
              <col style="width: 13%">
              <col style="width: 13%">
              <col style="width: 14%">
              <col style="width: 12%">
              <col style="width: 6%">
              <col style="width: 12%">
              <col style="width: 22%">
              <col style="width: 8%">
            </colgroup>
            <thead><tr><th>Время</th><th>Пользователь</th><th>Действие</th><th>Категория</th><th>Вес</th><th>Объект</th><th>Описание</th><th>Источник</th></tr></thead>
            <tbody>{timeline}</tbody>
          </table>
        </div>
      </section>
      <script>
        (() => {{
          const button = document.querySelector('[data-activity-sync]');
          const statusEl = document.querySelector('[data-job-status]');
          const statusLabels = {{
            pending: 'Ожидает',
            running: 'Выполняется',
            failed: 'Ошибка',
            done: 'Готово',
            ignored: 'Игнорируется',
            interrupted: 'Прервано'
          }};
          const labelStatus = (value) => statusLabels[value] || value || 'Не указано';
          const render = (message) => {{
            statusEl.hidden = false;
            statusEl.innerHTML = message;
          }};
          const readJson = async (response) => {{
            const text = await response.text();
            try {{
              return JSON.parse(text);
            }} catch (error) {{
              const preview = text.replace(/<[^>]*>/g, ' ').replace(/\\s+/g, ' ').trim().slice(0, 220);
              throw new Error(`Сервер вернул не JSON: HTTP ${{response.status}} ${{response.url}}${{preview ? ' · ' + preview : ''}}`);
            }}
          }};
          const poll = async (url) => {{
            for (let i = 0; i < 240; i++) {{
              const response = await fetch(url);
              const data = await readJson(response);
              if (!data.ok) throw new Error(data.error || 'не удалось обновить активность');
              const job = data.job;
              render(`<strong>Задача #${{job.id}}</strong>: ${{labelStatus(job.status)}} · ${{job.done_entities}}/${{job.total_entities}} сущностей · ${{job.items_count}} элементов`);
              if (!['pending', 'running'].includes(job.status)) {{
                setTimeout(() => location.reload(), 600);
                return;
              }}
              await new Promise(resolve => setTimeout(resolve, 1500));
            }}
          }};
          button?.addEventListener('click', async () => {{
            button.disabled = true;
            render('Запускаю фоновое обновление активности...');
            try {{
              const response = await fetch('/api/sync/resync?{suffix}', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ entities: ['events', 'tasks', 'lead_notes', 'contact_notes', 'company_notes', 'customer_notes', 'users'] }})
              }});
              const data = await readJson(response);
              if (!response.ok || !data.ok) throw new Error(data.error || 'не удалось запустить обновление активности');
              await poll(data.status_url);
            }} catch (error) {{
              render(`<strong>Ошибка:</strong> ${{error.message}}`);
              button.disabled = false;
            }}
          }});
        }})();
      </script>
    """
    return _page_shell("Активность пользователей amoCRM", body)


class AmoCRMServiceHandler(BaseHTTPRequestHandler):
    server_version = "amocrm-service/0.1"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Form-Secret")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        settings = _settings_from_query(parsed.query)
        if parsed.path == "/admin":
            self._send_html(_render_admin_page())
            return
        if parsed.path == "/app":
            self._send_html(_render_app_page(settings))
            return
        if parsed.path == "/account-settings":
            self._send_html(_render_account_settings_page(settings, parsed.query))
            return
        if parsed.path == "/conversations":
            self._send_html(_render_conversations_page(settings, parsed.query))
            return
        if parsed.path in {"/queue", "/queue.html"}:
            self._send_html(_render_queue_page(settings, parsed.query))
            return
        if parsed.path in {"/freshness", "/freshness.html"}:
            self._send_html(_render_freshness_page(settings))
            return
        if parsed.path in {"/quality", "/quality.html"}:
            self._send_html(_render_quality_page(settings, parsed.query))
            return
        if parsed.path in {"/quality-settings", "/quality-settings.html"}:
            self._send_html(_render_quality_settings_page(settings))
            return
        if parsed.path in {"/activity", "/activity.html"}:
            self._send_html(_render_activity_page(settings, parsed.query))
            return
        if parsed.path in {"/", "/dashboard", "/dashboard.html"}:
            self._send_html(_dashboard_html(page="dashboard", settings=settings, query_string=parsed.query))
            return
        if parsed.path in {"/drilldown", "/drilldown.html"}:
            self._send_html(_drilldown_html(settings, parsed.query))
            return
        if parsed.path in {"/constructor", "/constructor.html"}:
            self._send_html(_dashboard_html(page="constructor", settings=settings, query_string=parsed.query))
            return
        if parsed.path in {"/settings", "/settings.html"}:
            self._send_html(_dashboard_html(page="settings", settings=settings, query_string=parsed.query))
            return
        if parsed.path == "/oauth/callback":
            saved = _save_oauth_callback(parse_qs(parsed.query), settings)
            code = saved["payload"].get("code")
            self._send_html(f"""
            <!doctype html>
            <html lang="ru">
            <head>
              <meta charset="utf-8">
              <title>amoCRM OAuth</title>
              <link rel="preconnect" href="https://fonts.googleapis.com">
              <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
              <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600&display=swap" rel="stylesheet">
              <style>
                body {{
                  margin: 0;
                  min-height: 100vh;
                  display: grid;
                  place-items: center;
                  background: #f3f7fb;
                  color: #223047;
                  font: 16px/1.48 Montserrat, Arial, sans-serif;
                }}
                main {{
                  width: min(680px, calc(100% - 32px));
                  padding: 32px;
                  border: 1px solid #d9e7f5;
                  border-radius: 18px;
                  background: #fff;
                  box-shadow: 0 14px 36px rgba(41, 73, 112, .06);
                }}
                h1 {{ margin: 0 0 10px; font-size: 30px; font-weight: 600; }}
                p {{ color: #607089; }}
                code {{
                  display: block;
                  padding: 14px;
                  border: 1px solid #d9e7f5;
                  border-radius: 12px;
                  background: #f7fbff;
                  overflow-wrap: anywhere;
                }}
              </style>
            </head>
            <body>
              <main>
                <h1>Код amoCRM получен</h1>
                <p>Callback сохранен локально. Теперь можно обменять authorization code на access token.</p>
                <code>{html.escape(code or "code не пришел в query-параметрах")}</code>
              </main>
            </body>
            </html>
            """)
            return
        if parsed.path == "/api/summary":
            repo = _repo(settings)
            analytics = AnalyticsService(repo)
            analytics_filter = load_analytics_filter(settings.db_path)
            self._send_json({
                "pipeline_summary": analytics.pipeline_summary(analytics_filter),
                "tasks": analytics.tasks_summary(),
            })
            return
        if parsed.path == "/api/sync-options":
            self._send_json([
                {"entity": entity, "label": label, "checked": checked}
                for entity, label, checked in SYNC_OPTIONS
            ])
            return
        if parsed.path == "/api/hub/overview":
            self._send_json({
                "ok": True,
                "user_key": settings.user_key,
                "account_key": settings.account_key,
                "db_path": str(settings.db_path),
                "hub": _repo(settings).hub_overview(),
            })
            return
        if parsed.path == "/api/hub/background":
            self._send_json({
                "ok": True,
                "user_key": settings.user_key,
                "account_key": settings.account_key,
                "background": _background_worker_snapshot(settings.user_key, settings.account_key),
            })
            return
        if parsed.path == "/api/connection/status":
            repo = _repo(settings)
            entities = repo.hub_entity_overview()
            self._send_json({
                "ok": True,
                "user_key": settings.user_key,
                "account_key": settings.account_key,
                "db_path": str(settings.db_path),
                "entities_count": sum(int(row["items_count"] or 0) for row in entities),
                "entity_types": len(entities),
                "entities": entities,
                "queue": repo.queue_status_counts(settings.account_key),
                "latest_jobs": repo.latest_sync_jobs(settings.account_key, 5),
                "latest_runs": repo.latest_sync_runs(5),
                "latest_errors": repo.latest_errors(5),
                "background": _background_worker_snapshot(settings.user_key, settings.account_key),
            })
            return
        if parsed.path == "/api/activity/summary":
            query = parse_qs(parsed.query)
            days = int((query.get("days") or ["7"])[0])
            limit = int((query.get("limit") or ["100"])[0])
            target_date = (query.get("date") or [""])[0] or None
            self._send_json({
                "ok": True,
                "user_key": settings.user_key,
                "account_key": settings.account_key,
                "activity": ActivityService(_repo(settings)).dashboard(
                    days=days,
                    limit=limit,
                    target_date=target_date,
                    slot_minutes=15,
                ),
            })
            return
        if parsed.path == "/api/activity/marts":
            self._send_json({
                "ok": True,
                "user_key": settings.user_key,
                "account_key": settings.account_key,
                "marts": _repo(settings).activity_mart_status(),
            })
            return
        if parsed.path == "/api/freshness":
            self._send_json({
                "ok": True,
                "user_key": settings.user_key,
                "account_key": settings.account_key,
                "freshness": FreshnessService(_repo(settings)).dashboard(settings.account_key),
            })
            return
        if parsed.path == "/api/quality/summary":
            query = parse_qs(parsed.query)
            try:
                stale_days = int((query.get("stale_days") or ["3"])[0])
            except ValueError:
                stale_days = 3
            raw_settings = load_account_settings(
                user_key=settings.user_key,
                account_key=settings.account_key,
                data_root=settings.data_root,
            )
            current_quality_settings = quality_settings(raw_settings)
            current_quality_settings["stale_lead_days"] = stale_days
            self._send_json({
                "ok": True,
                "user_key": settings.user_key,
                "account_key": settings.account_key,
                "quality": QualityService(_repo(settings)).summary(
                    stale_lead_days=stale_days,
                    max_risks=int(current_quality_settings["max_risks"]),
                    settings=current_quality_settings,
                ),
            })
            return
        if parsed.path == "/api/kpi/daily":
            query = parse_qs(parsed.query)
            target_date = (query.get("date") or [""])[0] or None
            limit = int((query.get("limit") or ["500"])[0])
            self._send_json({
                "ok": True,
                "user_key": settings.user_key,
                "account_key": settings.account_key,
                "kpi": KpiService(_repo(settings)).daily(target_date, limit=limit),
            })
            return
        if parsed.path == "/api/kpi/marts":
            self._send_json({
                "ok": True,
                "user_key": settings.user_key,
                "account_key": settings.account_key,
                "marts": _repo(settings).lead_kpi_daily_status(),
            })
            return
        if parsed.path == "/api/sync-queue":
            self._send_json({"ok": True, "queue": _repo(settings).queue_summary(settings.account_key)})
            return
        if parsed.path == "/api/sync-queue/items":
            query = parse_qs(parsed.query)
            status = (query.get("status") or ["failed"])[0] or None
            limit = int((query.get("limit") or ["100"])[0])
            self._send_json({
                "ok": True,
                "items": _repo(settings).list_sync_queue_items(settings.account_key, status=status, limit=limit),
            })
            return
        if parsed.path == "/api/sync-sources":
            self._send_json({
                "ok": True,
                "sources": _repo(settings).list_sync_sources(settings.account_key),
            })
            return
        if parsed.path == "/api/call-checklist-steps":
            try:
                query = parse_qs(parsed.query)
                active = self._active_filter(query)
                self._send_json({
                    "ok": True,
                    "account_key": settings.account_key,
                    "steps": _repo(settings).list_call_checklist_steps(settings.account_key, active=active),
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/conversations":
            repo = _repo(settings)
            self._send_json({
                "ok": True,
                "records": repo.list_conversation_records(settings.account_key, limit=100),
                "analyses": repo.list_conversation_analyses(settings.account_key, limit=100),
            })
            return
        if parsed.path.startswith("/api/sync/jobs/"):
            try:
                job_id = int(parsed.path.removeprefix("/api/sync/jobs/").strip("/"))
                job = _repo(settings).get_sync_job(job_id, settings.account_key)
                if not job:
                    self._send_json({"ok": False, "error": "sync job not found"}, status=404)
                    return
                with _SYNC_THREADS_LOCK:
                    active_in_process = job_id in _SYNC_THREADS
                    job["active_in_process"] = active_in_process
                if job["status"] in {"pending", "running"} and not active_in_process:
                    job["status"] = "interrupted"
                    job["error"] = job.get("error") or "Сервис перезапускался, фоновая задача остановлена. Запустите выгрузку еще раз."
                self._send_json({"ok": True, "job": job})
            except ValueError:
                self._send_json({"ok": False, "error": "invalid sync job id"}, status=400)
            return
        if parsed.path == "/api/analytics-filter":
            self._send_json(load_analytics_filter(settings.db_path).to_json())
            return
        if parsed.path == "/api/analytics/fields":
            try:
                fields = _repo(settings).lead_analytics_fields()
                self._send_json({"ok": True, **fields})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/analytics/field-values":
            try:
                query = parse_qs(parsed.query)
                field = (query.get("field") or [""])[0]
                limit = int((query.get("limit") or ["200"])[0])
                repo = _repo(settings)
                values = FlexibleAnalyticsService(repo).field_values(field, limit)
                self._send_json({"ok": True, "field": field, "values": values})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/formula/dictionary":
            try:
                repo = _repo(settings)
                self._send_json({"ok": True, "dictionary": FormulaDictionaryService(repo).build()})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/dashboard-widgets":
            self._send_json({"ok": True, "widgets": load_widgets(settings.db_path), "pages": load_dashboard_pages(settings.db_path)})
            return
        if parsed.path == "/api/dashboard-pages":
            self._send_json({"ok": True, "pages": load_dashboard_pages(settings.db_path)})
            return
        if parsed.path == "/api/work-sources":
            self._send_json({"ok": True, "source_ids": load_work_sources(settings.db_path)})
            return
        if parsed.path == "/api/dashboard-widget-results":
            try:
                query = parse_qs(parsed.query)
                force = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
                cache_only = (query.get("cache_only") or ["0"])[0] in {"1", "true", "yes"}
                payload = _dashboard_widget_results(settings, force=force, cache_only=cache_only)
                self._send_json({"ok": True, **payload, "pages": load_dashboard_pages(settings.db_path), "refreshed": force})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        settings = _settings_from_query(parsed.query)
        if parsed.path == "/admin/connections":
            self._handle_create_connection()
            return
        if parsed.path == "/admin/connections/status":
            self._handle_connection_status()
            return
        if parsed.path == "/api/site/form":
            self._handle_site_form(settings, parse_qs(parsed.query))
            return
        if parsed.path == "/account-settings/save":
            self._handle_save_account_settings(settings)
            return
        if parsed.path == "/account-settings/user/save":
            self._handle_save_user_settings(settings)
            return
        if parsed.path == "/quality-settings/save":
            self._handle_quality_settings_save(settings)
            return
        if parsed.path == "/api/amo/webhook":
            self._handle_amo_webhook(settings)
            return
        if parsed.path == "/api/conversations/post-note":
            self._handle_conversation_post_note(settings)
            return
        if parsed.path == "/api/conversations/settings":
            self._handle_conversation_settings(settings)
            return
        if parsed.path in {"/api/conversations/auto-run", "/api/conversations/auto-dry-run"}:
            self._handle_conversation_auto(settings, dry_run=parsed.path.endswith("auto-dry-run"))
            return
        if parsed.path == "/api/conversations/export":
            self._handle_conversation_export(settings)
            return
        if parsed.path == "/api/call-checklist-steps":
            self._handle_call_checklist_step_create(settings)
            return
        if parsed.path.startswith("/api/call-checklist-steps/"):
            self._handle_call_checklist_step_update_or_delete(settings, parsed.path)
            return
        if parsed.path == "/api/sync-queue/process":
            self._handle_process_queue(settings)
            return
        if parsed.path == "/api/sync-queue/retry":
            self._handle_queue_action(settings, parse_qs(parsed.query), action="retry")
            return
        if parsed.path == "/api/sync-queue/ignore":
            self._handle_queue_action(settings, parse_qs(parsed.query), action="ignore")
            return
        if parsed.path == "/api/activity/rebuild-marts":
            self._handle_rebuild_activity_marts(settings, parse_qs(parsed.query))
            return
        if parsed.path.startswith("/api/sync-sources/") and parsed.path.endswith("/resync"):
            try:
                source_id = int(parsed.path.removeprefix("/api/sync-sources/").removesuffix("/resync").strip("/"))
            except ValueError:
                self._send_json({"ok": False, "error": "invalid sync source id"}, status=400)
                return
            self._handle_resync_source(settings, source_id)
            return
        if parsed.path == "/api/kpi/rebuild":
            self._handle_rebuild_kpi(settings, parse_qs(parsed.query))
            return
        if parsed.path == "/api/hub/cleanup":
            self._handle_hub_cleanup(settings)
            return
        if parsed.path in {"/api/sync/bootstrap", "/api/sync/resync"}:
            self._handle_sync_job(settings, "bootstrap" if parsed.path.endswith("bootstrap") else "resync")
            return
        if parsed.path == "/api/dashboard-widgets":
            try:
                payload = self._read_json()
                if "widgets" in payload:
                    widgets = save_widgets(settings.db_path, list(payload.get("widgets") or []))
                else:
                    widgets = add_widget(settings.db_path, payload)
                self._send_json({"ok": True, "widgets": widgets})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/dashboard-pages":
            try:
                payload = self._read_json()
                pages = save_dashboard_pages(settings.db_path, list(payload.get("pages") or []))
                self._send_json({"ok": True, "pages": pages})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/work-sources":
            try:
                payload = self._read_json()
                source_ids = save_work_sources(settings.db_path, list(payload.get("source_ids") or []))
                self._send_json({"ok": True, "source_ids": source_ids})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/analytics/query":
            try:
                repo = _repo(settings)
                payload = self._read_json()
                query = AnalyticsQuery.from_payload(payload)
                result = FlexibleAnalyticsService(repo).run(query)
                self._send_json(
                    {"ok": True, "result": result, "freshness": _analytics_freshness(repo, settings.account_key, query)}
                )
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/formula/evaluate":
            try:
                repo = _repo(settings)
                payload = self._read_json()
                formula = payload.get("formula") if isinstance(payload, dict) else None
                if not isinstance(formula, dict):
                    formula = payload
                engine = FormulaEngine(repo)
                result = engine.evaluate(formula)
                diagnostics = engine.diagnose(formula)
                self._send_json({"ok": True, "result": result, "diagnostics": diagnostics})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/ai/formula/draft":
            try:
                started_at = time.perf_counter()
                repo = _repo(settings)
                payload = self._read_json()
                prompt = str(payload.get("prompt") or payload.get("text") or "").strip()
                if not prompt:
                    self._send_json({"ok": False, "error": "empty prompt"}, status=400)
                    return
                dictionary = FormulaDictionaryService(repo).build()
                sources = repo.list_sync_sources(settings.account_key)
                default_source = None
                try:
                    raw_source_id = payload.get("source_id")
                    source_id = int(raw_source_id) if raw_source_id not in (None, "", 0, "0") else None
                except (TypeError, ValueError):
                    source_id = None
                if source_id:
                    default_source = next((source for source in sources if int(source.get("id") or 0) == source_id), None)
                draft = build_formula_draft(
                    user_prompt=prompt,
                    dictionary=dictionary,
                    sources=sources,
                    default_source=default_source,
                )
                print(
                    "AI formula draft generated "
                    f"account={settings.user_key}/{settings.account_key} "
                    f"source_id={source_id or 0} "
                    f"prompt_len={len(prompt)} "
                    f"elapsed={time.perf_counter() - started_at:.1f}s"
                )
                if not draft.get("configured"):
                    self._send_json({"ok": True, "configured": False, "draft": draft})
                    return
                formula = draft.get("formula")
                if not isinstance(formula, dict):
                    self._send_json({"ok": False, "error": "AI did not return formula object", "draft": draft}, status=400)
                    return
                engine = FormulaEngine(repo)
                result = engine.evaluate(formula)
                diagnostics = engine.diagnose(formula)
                print(
                    "AI formula draft evaluated "
                    f"account={settings.user_key}/{settings.account_key} "
                    f"elapsed={time.perf_counter() - started_at:.1f}s"
                )
                self._send_json({
                    "ok": True,
                    "configured": True,
                    "draft": draft,
                    "result": result,
                    "diagnostics": diagnostics,
                })
            except AiFormulaError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=502)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/amo-filter/parse":
            try:
                repo = _repo(settings)
                payload = self._read_json()
                url = str(payload.get("url") or "").strip()
                result = _parse_amo_filter_url(repo, settings, url)
                self._send_json({"ok": True, **result})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/analytics-filter":
            try:
                payload = self._read_json()
                analytics_filter = save_analytics_filter(settings.db_path, payload)
                self._send_json({"ok": True, "filter": analytics_filter.to_json()})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path != "/api/sync":
            self.send_error(404, "Not found")
            return
        try:
            payload = self._read_json()
            entities = payload.get("entities") or []
            if not isinstance(entities, list) or not all(isinstance(item, str) for item in entities):
                raise ValueError("entities must be a list of strings")
            result = _sync_entities(entities, settings)
            self._send_json({"ok": True, "result": result})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _active_filter(self, query: dict[str, list[str]]) -> bool | None:
        raw = str((query.get("active") or ["1"])[-1] or "1").strip().lower()
        if raw in {"", "1", "true", "yes", "active"}:
            return True
        if raw in {"0", "false", "no", "inactive"}:
            return False
        if raw in {"all", "any", "*"}:
            return None
        raise ValueError("active must be one of: true, false, all")

    def _payload_bool(self, payload: dict[str, Any], key: str) -> bool | None:
        if key not in payload:
            return None
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on", "active"}:
            return True
        if text in {"0", "false", "no", "off", "inactive"}:
            return False
        raise ValueError(f"{key} must be boolean")

    def _handle_call_checklist_step_create(self, settings: Any) -> None:
        try:
            payload = self._read_json()
            repo = _repo(settings)
            step = repo.create_call_checklist_step(
                settings.account_key,
                slug=str(payload.get("slug") or "").strip(),
                label=str(payload.get("label") or "").strip(),
                hint=str(payload.get("hint") or "").strip(),
                order_index=int(payload.get("order_index") or 0),
                active=self._payload_bool(payload, "active") if "active" in payload else True,
            )
            self._send_json({"ok": True, "step": step}, status=201)
        except sqlite3.IntegrityError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=409)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_call_checklist_step_update_or_delete(self, settings: Any, path: str) -> None:
        try:
            tail = path.removeprefix("/api/call-checklist-steps/").strip("/")
            soft_delete = tail.endswith("/delete")
            raw_id = tail.removesuffix("/delete").strip("/")
            step_id = int(raw_id)
            repo = _repo(settings)
            if soft_delete:
                step = repo.deactivate_call_checklist_step(settings.account_key, step_id)
            else:
                payload = self._read_json()
                step = repo.update_call_checklist_step(
                    settings.account_key,
                    step_id,
                    label=str(payload["label"]).strip() if "label" in payload else None,
                    hint=str(payload["hint"]).strip() if "hint" in payload else None,
                    order_index=int(payload["order_index"]) if "order_index" in payload else None,
                    active=self._payload_bool(payload, "active"),
                )
            self._send_json({"ok": True, "step": step})
        except LookupError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_site_form(self, settings: Any, query: dict[str, list[str]]) -> None:
        try:
            if settings.form_secret:
                request_secret = self.headers.get("X-Form-Secret") or (query.get("secret") or [""])[-1]
                if request_secret != settings.form_secret:
                    self._send_json({"ok": False, "error": "invalid form secret"}, status=401)
                    return

            payload = self._read_payload()
            form = parse_site_lead_payload(payload)
            tags = list(settings.form_tags)
            client = AmoCRMClient(settings)
            try:
                contact = client.create_contact(
                    name=form.contact_name,
                    phone=form.phone,
                    email=form.email,
                    tags=tags,
                )
                lead = client.create_lead(
                    name=form.lead_name,
                    contact_id=contact.get("id"),
                    price=form.price,
                    pipeline_id=settings.form_pipeline_id,
                    status_id=settings.form_status_id,
                    responsible_user_id=settings.form_responsible_user_id,
                    tags=tags,
                )
                if form.note_text and lead.get("id"):
                    client.add_lead_note(int(lead["id"]), form.note_text)
            finally:
                client.close()

            self._send_json({
                "ok": True,
                "lead_id": lead.get("id"),
                "contact_id": contact.get("id"),
            }, status=201)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_amo_webhook(self, settings: Any) -> None:
        try:
            repo = _repo(settings)
            repo.upsert_account(
                settings.account_key,
                subdomain=settings.subdomain or None,
                base_domain=settings.base_domain,
            )
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            content_type = self.headers.get("Content-Type", "")
            if "application/json" in content_type:
                payload = json.loads(raw or "{}")
            else:
                payload = _flatten_form(parse_qs(raw, keep_blank_values=True))
            items = _extract_webhook_items(payload)
            event_ids = []
            queue_ids = []
            if not items:
                event_id = repo.add_webhook_event(
                    settings.account_key,
                    "unknown",
                    payload,
                    raw_body=raw,
                )
                event_ids.append(event_id)
            for item in items:
                event_id = repo.add_webhook_event(
                    settings.account_key,
                    item["event_type"],
                    payload,
                    entity_type=item["entity_type"],
                    entity_id=item["entity_id"],
                    raw_body=raw,
                )
                event_ids.append(event_id)
                queue_id = repo.enqueue_sync(
                    settings.account_key,
                    item["entity_type"],
                    item["entity_id"],
                    action="delete" if _delete_event(item["event_type"]) else "refresh",
                    reason=item["event_type"],
                    payload={"webhook_event_id": event_id},
                )
                queue_ids.append(queue_id)
            self._send_json({
                "ok": True,
                "events": len(event_ids),
                "queued": len([item for item in queue_ids if item]),
            })
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_process_queue(self, settings: Any) -> None:
        try:
            repo = _repo(settings)
            client = AmoCRMClient(settings)
            try:
                service = SyncService(client, repo)
                result = service.process_queue(settings.account_key, limit=25)
            finally:
                client.close()
            self._send_json({"ok": True, "result": result})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_conversation_post_note(self, settings: Any) -> None:
        try:
            data = self._read_payload()
            conversation_id = str(data.get("conversation_id") or "").strip()
            repo = _repo(settings)
            record, analysis = find_record_and_analysis(
                repo.list_conversation_records(settings.account_key, limit=200),
                repo.list_conversation_analyses(settings.account_key, limit=200),
                conversation_id=conversation_id or None,
            )
            lead_id = record.get("lead_id")
            if not lead_id:
                raise ValueError("conversation is not linked to a lead")
            note_text = build_lead_analysis_note(record, analysis)
            client = AmoCRMClient(settings)
            try:
                try:
                    note = client.add_lead_note(int(lead_id), note_text)
                except Exception as exc:
                    repo.update_conversation_record_status(
                        settings.account_key,
                        str(record["conversation_id"]),
                        status=str(record.get("status") or "transcribed"),
                        metadata_patch={
                            "last_post_note_error": str(exc),
                            "last_post_note_failed_at": utc_now(),
                        },
                    )
                    raise
            finally:
                client.close()
            note_id = str(note.get("id") or "")
            repo.update_conversation_record_status(
                settings.account_key,
                str(record["conversation_id"]),
                status=str(record.get("status") or "transcribed"),
                metadata_patch={
                    "last_posted_note_id": note_id,
                    "last_posted_lead_id": str(lead_id),
                    "last_posted_at": utc_now(),
                },
            )
            self.send_response(303)
            self.send_header(
                "Location",
                f"/conversations?user={settings.user_key}&account={settings.account_key}&posted_note={note_id}",
            )
            self.end_headers()
        except Exception as exc:
            self.send_response(303)
            self.send_header(
                "Location",
                f"/conversations?user={settings.user_key}&account={settings.account_key}&error={quote(str(exc))}",
            )
            self.end_headers()

    def _handle_conversation_settings(self, settings: Any) -> None:
        try:
            data = self._read_payload(preserve_lists=True)
            def selected_ids(prefix: str, legacy_key: str) -> list[int]:
                values = [
                    int(key.removeprefix(prefix))
                    for key, value in data.items()
                    if key.startswith(prefix) and str(value) == "1" and key.removeprefix(prefix).isdigit()
                ]
                if values:
                    return sorted(values)
                return parse_int_list(data.get(legacy_key) or "")

            actions = {
                key: str(data.get(key) or "") == "1"
                for key in [
                    "import_leads",
                    "probe_recordings",
                    "download_recordings",
                    "transcribe",
                    "analyze",
                    "post_note",
                    "export_google_sheets",
                ]
            }
            scoring = json.loads(str(data.get("scoring_json") or "[]"))
            if not isinstance(scoring, list):
                raise ValueError("scoring JSON must be a list")
            raw_settings = load_account_settings(
                user_key=settings.user_key,
                account_key=settings.account_key,
                data_root=settings.data_root,
            )
            current_ci = conversation_settings(raw_settings)
            next_settings = update_conversation_settings(raw_settings, {
                "enabled": str(data.get("enabled") or "") == "1",
                "filters": {
                    "pipeline_ids": selected_ids("pipeline_id_", "pipeline_ids"),
                    "status_ids": selected_ids("status_id_", "status_ids"),
                    "responsible_user_ids": selected_ids("responsible_user_id_", "responsible_user_ids"),
                    "min_duration_seconds": int(data.get("min_duration_seconds") or 0),
                    "max_duration_seconds": int(data.get("max_duration_seconds") or 0),
                    "new_calls_only": True,
                    "started_at": int(current_ci.get("filters", {}).get("started_at") or 0),
                },
                "actions": actions,
                "analysis_prompt": str(data.get("analysis_prompt") or "").strip(),
                "external_analysis": {
                    "mode": str(data.get("external_analysis_mode") or "local").strip(),
                    "provider": "openrouter",
                    "model": str(data.get("external_analysis_model") or "").strip(),
                },
                "scoring": scoring,
                "google_sheets": {
                    "spreadsheet_id": str(data.get("spreadsheet_id") or "").strip(),
                    "worksheet_name": str(data.get("worksheet_name") or "").strip() or "amoCRM call analysis",
                },
            })
            save_account_settings(
                user_key=settings.user_key,
                account_key=settings.account_key,
                settings=next_settings,
                data_root=settings.data_root,
            )
            next_action = str(data.get("next_action") or "save")
            summary = "настройки сохранены"
            if next_action in {"dry_run", "auto_run"}:
                repo = _repo(settings)
                result = ConversationAutomationService(settings, repo).run(limit=25, dry_run=next_action == "dry_run")
                summary = f"настройки сохранены; {_conversation_run_summary(result, dry_run=next_action == 'dry_run')}"
            elif next_action == "reanalyze":
                repo = _repo(settings)
                config = conversation_settings(next_settings)
                result = ConversationPipeline(repo).analyze_transcribed(
                    settings.account_key,
                    limit=25,
                    force=True,
                    analysis_config=config,
                )
                summary = f"настройки сохранены; пересчитано анализов: {int(result.get('analyses') or 0)}"
            elif next_action == "export":
                repo = _repo(settings)
                result = ConversationExportService(repo).export_csv(
                    settings.account_key,
                    settings.workspace_dir / "exports" / "conversation_analysis.csv",
                )
                summary = f"настройки сохранены; экспортировано {result['rows']} строк"
            self.send_response(303)
            self.send_header("Location", f"/conversations?user={settings.user_key}&account={settings.account_key}&result={quote(summary)}")
            self.end_headers()
        except Exception as exc:
            self.send_response(303)
            self.send_header("Location", f"/conversations?user={settings.user_key}&account={settings.account_key}&error={quote(str(exc))}")
            self.end_headers()

    def _handle_quality_settings_save(self, settings: Any) -> None:
        try:
            data = self._read_payload(preserve_lists=True)

            def selected_ids(prefix: str) -> list[int]:
                return sorted([
                    int(key.removeprefix(prefix))
                    for key, value in data.items()
                    if key.startswith(prefix)
                    and str(value) == "1"
                    and key.removeprefix(prefix).isdigit()
                ])

            raw_settings = load_account_settings(
                user_key=settings.user_key,
                account_key=settings.account_key,
                data_root=settings.data_root,
            )
            next_settings = update_quality_settings(raw_settings, {
                "enabled": True,
                "stale_lead_days": int(data.get("stale_lead_days") or 3),
                "max_risks": int(data.get("max_risks") or 200),
                "filters": {
                    "pipeline_ids": selected_ids("pipeline_id_"),
                    "status_ids": selected_ids("status_id_"),
                    "ignored_status_ids": selected_ids("ignored_status_id_"),
                    "responsible_user_ids": [],
                },
                "rules": {
                    "overdue_tasks": str(data.get("rule_overdue_tasks") or "") == "1",
                    "missing_next_task": str(data.get("rule_missing_next_task") or "") == "1",
                    "stale_leads": str(data.get("rule_stale_leads") or "") == "1",
                },
            })
            save_account_settings(
                user_key=settings.user_key,
                account_key=settings.account_key,
                settings=next_settings,
                data_root=settings.data_root,
            )
            self.send_response(303)
            self.send_header("Location", f"/quality-settings?user={settings.user_key}&account={settings.account_key}&saved=1")
            self.end_headers()
        except Exception as exc:
            self.send_response(303)
            self.send_header("Location", f"/quality-settings?user={settings.user_key}&account={settings.account_key}&error={quote(str(exc))}")
            self.end_headers()

    def _handle_conversation_auto(self, settings: Any, *, dry_run: bool) -> None:
        try:
            repo = _repo(settings)
            result = ConversationAutomationService(settings, repo).run(limit=25, dry_run=dry_run)
            summary = _conversation_run_summary(result, dry_run=dry_run)
            self.send_response(303)
            self.send_header("Location", f"/conversations?user={settings.user_key}&account={settings.account_key}&result={quote(summary)}")
            self.end_headers()
        except Exception as exc:
            self.send_response(303)
            self.send_header("Location", f"/conversations?user={settings.user_key}&account={settings.account_key}&error={quote(str(exc))}")
            self.end_headers()

    def _handle_conversation_export(self, settings: Any) -> None:
        try:
            repo = _repo(settings)
            result = ConversationExportService(repo).export_csv(
                settings.account_key,
                settings.workspace_dir / "exports" / "conversation_analysis.csv",
            )
            summary = f"экспортировано {result['rows']} строк: {result['path']}"
            self.send_response(303)
            self.send_header("Location", f"/conversations?user={settings.user_key}&account={settings.account_key}&result={quote(summary)}")
            self.end_headers()
        except Exception as exc:
            self.send_response(303)
            self.send_header("Location", f"/conversations?user={settings.user_key}&account={settings.account_key}&error={quote(str(exc))}")
            self.end_headers()

    def _handle_queue_action(self, settings: Any, query: dict[str, list[str]], action: str) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            content_type = self.headers.get("Content-Type", "")
            if "application/json" in content_type:
                payload = json.loads(raw or "{}")
                raw_ids = payload.get("ids") or payload.get("id") or []
                raw_items = raw_ids if isinstance(raw_ids, list) else [raw_ids]
            else:
                form = parse_qs(raw, keep_blank_values=True)
                raw_items = form.get("ids") or form.get("id") or []
            queue_ids = [int(item) for item in raw_items if str(item).strip().isdigit()]
            repo = _repo(settings)
            if action == "retry":
                changed = repo.retry_sync_queue_items(settings.account_key, queue_ids)
            elif action == "ignore":
                changed = repo.ignore_sync_queue_items(settings.account_key, queue_ids)
            else:
                raise ValueError(f"unknown queue action: {action}")
            redirect = (query.get("redirect") or [""])[0]
            if redirect in {"/queue", "/app"}:
                status = (query.get("status") or ["failed"])[0]
                location = f"{redirect}?user={settings.user_key}&account={settings.account_key}"
                if redirect == "/queue" and status:
                    location += f"&status={status}"
                self.send_response(303)
                self.send_header("Location", location)
                self.end_headers()
                return
            self._send_json({"ok": True, "action": action, "changed": changed, "ids": queue_ids})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_rebuild_activity_marts(self, settings: Any, query: dict[str, list[str]]) -> None:
        try:
            target_date = (query.get("date") or [""])[0] or None
            slot_minutes = int((query.get("slot_minutes") or ["15"])[0])
            repo = _repo(settings)
            result = ActivityService(repo).rebuild_marts_for_day(target_date, slot_minutes)
            self._send_json({"ok": True, "result": result})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_rebuild_kpi(self, settings: Any, query: dict[str, list[str]]) -> None:
        try:
            target_date = (query.get("date") or [""])[0] or None
            result = KpiService(_repo(settings)).rebuild_daily(target_date)
            self._send_json({"ok": True, "result": result})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_hub_cleanup(self, settings: Any) -> None:
        try:
            result = _repo(settings).cleanup_operational_rows(settings.account_key)
            self._send_json({"ok": True, "result": result})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_sync_job(self, settings: Any, job_type: str) -> None:
        try:
            payload = self._read_json()
            entity_types = payload.get("entities")
            if entity_types is not None and (
                not isinstance(entity_types, list) or not all(isinstance(item, str) for item in entity_types)
            ):
                raise ValueError("entities must be a list of strings")
            repo = _repo(settings)
            repo.upsert_account(
                settings.account_key,
                subdomain=settings.subdomain or None,
                base_domain=settings.base_domain,
            )
            active_job = _active_account_sync_job(repo, settings.account_key)
            if active_job:
                self._send_json({
                    "ok": False,
                    "error": f"Уже выполняется задача #{active_job['id']}. Дождись ее завершения и запусти выгрузку снова.",
                    "active_job_id": active_job["id"],
                    "status_url": f"/api/sync/jobs/{active_job['id']}?user={settings.user_key}&account={settings.account_key}",
                }, status=409)
                return
            pipeline_ids = _int_list(payload.get("pipeline_ids"))
            status_ids = _int_list(payload.get("status_ids"))
            source_name = str(payload.get("source_name") or "").strip()
            has_source_filters = bool(source_name or pipeline_ids or status_ids)
            if has_source_filters and entity_types is None:
                entity_types = [
                    "pipelines",
                    "users",
                    "lead_custom_fields",
                    "contact_custom_fields",
                    "company_custom_fields",
                    "leads",
                ]
            entities = entity_types or list(BOOTSTRAP_ENTITIES)
            source_id = None
            filters = None
            if has_source_filters:
                filters = {"pipeline_ids": pipeline_ids, "status_ids": status_ids}
                source_id = repo.create_sync_source(
                    settings.account_key,
                    name=source_name or f"Источник #{utc_now()}",
                    entity_types=entities,
                    pipeline_ids=pipeline_ids,
                    status_ids=status_ids,
                )
                job_type = "source_resync" if job_type == "resync" else "source_bootstrap"
            job_id = repo.start_sync_job(settings.account_key, job_type, entities, status="pending")
            if source_id:
                repo.update_sync_source_job(source_id, job_id)
            _start_sync_job_thread(
                settings.user_key,
                settings.account_key,
                job_id,
                job_type,
                entities,
                filters=filters,
                source_id=source_id,
            )
            self._send_json({
                "ok": True,
                "accepted": True,
                "job_id": job_id,
                "source_id": source_id,
                "status_url": f"/api/sync/jobs/{job_id}?user={settings.user_key}&account={settings.account_key}",
            }, status=202)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_resync_source(self, settings: Any, source_id: int) -> None:
        try:
            repo = _repo(settings)
            source = repo.get_sync_source(source_id, settings.account_key)
            if not source:
                self._send_json({"ok": False, "error": "sync source not found"}, status=404)
                return
            repo.upsert_account(
                settings.account_key,
                subdomain=settings.subdomain or None,
                base_domain=settings.base_domain,
            )
            active_job = _active_account_sync_job(repo, settings.account_key)
            if active_job:
                self._send_json({
                    "ok": False,
                    "error": f"Уже выполняется задача #{active_job['id']}. Дождись ее завершения и запусти выгрузку снова.",
                    "active_job_id": active_job["id"],
                    "status_url": f"/api/sync/jobs/{active_job['id']}?user={settings.user_key}&account={settings.account_key}",
                }, status=409)
                return
            entities = list(source.get("entity_types") or [
                "pipelines",
                "users",
                "lead_custom_fields",
                "contact_custom_fields",
                "company_custom_fields",
                "leads",
            ])
            filters = {
                "pipeline_ids": [int(item) for item in source.get("pipeline_ids") or []],
                "status_ids": [int(item) for item in source.get("status_ids") or []],
            }
            job_id = repo.start_sync_job(settings.account_key, "source_resync", entities, status="pending")
            repo.update_sync_source_job(source_id, job_id)
            _start_sync_job_thread(
                settings.user_key,
                settings.account_key,
                job_id,
                "source_resync",
                entities,
                filters=filters,
                source_id=source_id,
            )
            self._send_json({
                "ok": True,
                "accepted": True,
                "job_id": job_id,
                "source_id": source_id,
                "status_url": f"/api/sync/jobs/{job_id}?user={settings.user_key}&account={settings.account_key}",
            }, status=202)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_create_connection(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            data = _flatten_form(parse_qs(raw, keep_blank_values=True))
            user_key = safe_key(str(data.get("user_key") or "default"))
            subdomain = str(data.get("subdomain") or "").strip()
            account_key = safe_key(str(data.get("account_key") or subdomain or "default"))
            access_token = str(data.get("access_token") or "").strip()
            if not subdomain or not access_token:
                raise ValueError("subdomain and access_token are required")
            result = create_connection(
                user_key=user_key,
                account_key=account_key,
                subdomain=subdomain,
                access_token=access_token,
            )
            self.send_response(303)
            self.send_header("Location", f"/app?user={result['user_key']}&account={result['account_key']}")
            self.end_headers()
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_connection_status(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            data = _flatten_form(parse_qs(raw, keep_blank_values=True))
            set_connection_status(
                user_key=str(data.get("user_key") or ""),
                account_key=str(data.get("account_key") or ""),
                status=str(data.get("status") or ""),
            )
            self.send_response(303)
            self.send_header("Location", "/admin")
            self.end_headers()
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_save_account_settings(self, settings: Any) -> None:
        try:
            data = self._read_payload()
            payload = json.loads(str(data.get("settings_json") or "{}"))
            if not isinstance(payload, dict):
                raise ValueError("settings JSON must be an object")
            save_account_settings(
                user_key=settings.user_key,
                account_key=settings.account_key,
                settings=payload,
                data_root=settings.data_root,
            )
            self.send_response(303)
            self.send_header("Location", f"/account-settings?user={settings.user_key}&account={settings.account_key}")
            self.end_headers()
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_save_user_settings(self, settings: Any) -> None:
        try:
            data = self._read_payload()
            crm_user_id = str(data.get("crm_user_id") or "").strip()
            if not crm_user_id:
                raise ValueError("crm_user_id is required")
            payload = json.loads(str(data.get("settings_json") or "{}"))
            if not isinstance(payload, dict):
                raise ValueError("settings JSON must be an object")
            save_user_settings(
                user_key=settings.user_key,
                account_key=settings.account_key,
                crm_user_id=crm_user_id,
                settings=payload,
                data_root=settings.data_root,
            )
            self.send_response(303)
            self.send_header(
                "Location",
                f"/account-settings?user={settings.user_key}&account={settings.account_key}&crm_user_id={crm_user_id}",
            )
            self.end_headers()
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def log_message(self, format: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), format % args))

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def _read_payload(self, preserve_lists: bool = False) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return json.loads(raw or "{}")
        parsed = parse_qs(raw, keep_blank_values=True)
        if preserve_lists:
            return {
                key: values if len(values) > 1 else (values[-1] if values else "")
                for key, values in parsed.items()
            }
        return _flatten_form(parsed)

    def _send_html(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, body: Any, status: int = 200) -> None:
        data = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")


def serve(host: str = "127.0.0.1", port: int = 8010, cleanup: bool = True) -> None:
    httpd = ThreadingHTTPServer((host, port), AmoCRMServiceHandler)
    if cleanup:
        try:
            cleanup_result = _cleanup_stale_runtime_state()
            print(f"amoCRM service cleanup: {cleanup_result}")
        except Exception as exc:
            print(f"amoCRM service cleanup failed: {exc}")
    else:
        print("amoCRM service cleanup: skipped")
    _start_queue_worker()
    print(f"amoCRM service dashboard: http://{host}:{port}/dashboard")
    httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(prog="amocrm-service-server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--skip-cleanup", action="store_true")
    args = parser.parse_args()
    serve(args.host, args.port, cleanup=not args.skip_cleanup)


if __name__ == "__main__":
    main()

