from __future__ import annotations

import json
import hashlib
import re
import time
from pathlib import Path
from typing import Any


DEFAULT_DASHBOARD_PAGE_ID = "main"
DEFAULT_DASHBOARD_PAGES: list[dict[str, str]] = [{"id": DEFAULT_DASHBOARD_PAGE_ID, "name": "Основной"}]
WIDGET_SIZES = {"small", "medium", "large", "wide"}
WIDGET_VIEWS = {"number", "table", "bar", "line", "list"}
WIDGET_FORMULAS = {"none", "conversion", "lost_rate", "open_rate", "delta_won_lost", "plan_fact"}

DEFAULT_WIDGETS: list[dict[str, Any]] = [
    {
        "id": "default-total-kpi",
        "title": "Общие KPI по сделкам",
        "view": "number",
        "size": "wide",
        "formula": "conversion",
        "query": {
            "entity": "leads",
            "metrics": ["count", "sum_price", "avg_price", "open_count", "won_count", "lost_count"],
            "group_by": [],
            "filters": [],
            "filter_logic": "and",
            "order_by": "count",
            "order_dir": "desc",
            "limit": 1,
        },
    },
    {
        "id": "default-pipelines",
        "title": "Сделки по воронкам",
        "view": "table",
        "size": "wide",
        "formula": "none",
        "query": {
            "entity": "leads",
            "metrics": ["count", "sum_price", "open_count", "won_count", "lost_count"],
            "group_by": ["pipeline_id"],
            "filters": [],
            "filter_logic": "and",
            "order_by": "count",
            "order_dir": "desc",
            "limit": 20,
        },
    },
    {
        "id": "default-created-month",
        "title": "Динамика по месяцу создания",
        "view": "line",
        "size": "wide",
        "formula": "none",
        "query": {
            "entity": "leads",
            "metrics": ["count", "sum_price", "avg_price"],
            "group_by": ["created_month"],
            "filters": [],
            "filter_logic": "and",
            "order_by": "created_month",
            "order_dir": "asc",
            "limit": 24,
        },
    },
    {
        "id": "default-ad-source",
        "title": "Заявки по рекламной площадке",
        "view": "bar",
        "size": "medium",
        "formula": "none",
        "query": {
            "entity": "leads",
            "metrics": ["count", "sum_price", "avg_price"],
            "group_by": ["cf_127785"],
            "filters": [],
            "filter_logic": "and",
            "order_by": "count",
            "order_dir": "desc",
            "limit": 30,
        },
    },
]


def widgets_path(db_path: Path) -> Path:
    return db_path.parent / "dashboard_widgets.json"


def widget_results_path(db_path: Path) -> Path:
    return db_path.parent / "dashboard_widget_results.json"


def dashboard_pages_path(db_path: Path) -> Path:
    return db_path.parent / "dashboard_pages.json"


def work_sources_path(db_path: Path) -> Path:
    return db_path.parent / "work_sources.json"


def widget_signature(widget: dict[str, Any]) -> str:
    payload = {
        "widget_type": widget.get("widget_type") or "analytics",
        "view": widget.get("view"),
        "formula": widget.get("formula"),
        "formula_spec": widget.get("formula_spec") or {},
        "query": widget.get("query") or {},
        "settings": widget.get("settings") or {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_widget_results_cache(db_path: Path) -> dict[str, Any]:
    path = widget_results_path(db_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_widget_results_cache(db_path: Path, cache: dict[str, Any]) -> dict[str, Any]:
    path = widget_results_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return cache


def _safe_page_id(value: Any, fallback: str = DEFAULT_DASHBOARD_PAGE_ID) -> str:
    raw = str(value or "").strip().lower()
    safe = re.sub(r"[^\w-]+", "-", raw, flags=re.UNICODE).strip("-")
    return safe[:60] or fallback


def load_dashboard_pages(db_path: Path) -> list[dict[str, str]]:
    path = dashboard_pages_path(db_path)
    if not path.exists():
        return save_dashboard_pages(db_path, DEFAULT_DASHBOARD_PAGES)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = []
    return save_dashboard_pages(db_path, data if isinstance(data, list) else [])


def save_dashboard_pages(db_path: Path, pages: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        page_id = _safe_page_id(page.get("id") or f"page-{index + 1}")
        if page_id in seen:
            page_id = _safe_page_id(f"{page_id}-{index + 1}")
        name = str(page.get("name") or "").strip() or ("Основной" if not normalized else f"Лист {index + 1}")
        seen.add(page_id)
        normalized.append({"id": page_id, "name": name[:80]})
    if not normalized:
        normalized = [dict(DEFAULT_DASHBOARD_PAGES[0])]
    if DEFAULT_DASHBOARD_PAGE_ID not in seen:
        normalized.insert(0, dict(DEFAULT_DASHBOARD_PAGES[0]))
    path = dashboard_pages_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def load_work_sources(db_path: Path) -> list[int]:
    path = work_sources_path(db_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    raw_ids = data.get("source_ids") if isinstance(data, dict) else data
    if not isinstance(raw_ids, list):
        return []
    result: list[int] = []
    for item in raw_ids:
        try:
            source_id = int(item)
        except (TypeError, ValueError):
            continue
        if source_id > 0 and source_id not in result:
            result.append(source_id)
    return result


def save_work_sources(db_path: Path, source_ids: list[Any]) -> list[int]:
    normalized: list[int] = []
    for item in source_ids:
        try:
            source_id = int(item)
        except (TypeError, ValueError):
            continue
        if source_id > 0 and source_id not in normalized:
            normalized.append(source_id)
    path = work_sources_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source_ids": normalized}, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def load_widgets(db_path: Path) -> list[dict[str, Any]]:
    path = widgets_path(db_path)
    if not path.exists():
        return save_widgets(db_path, DEFAULT_WIDGETS)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    return save_widgets(db_path, data)


def save_widgets(db_path: Path, widgets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = widgets_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = []
    for index, widget in enumerate(widgets):
        size = str(widget.get("size") or "medium")
        view = str(widget.get("view") or "table")
        formula = str(widget.get("formula") or "none")
        widget_type = str(widget.get("widget_type") or "analytics")
        normalized.append({
            "id": str(widget.get("id") or f"widget-{int(time.time() * 1000)}-{index}"),
            "title": str(widget.get("title") or "Новый показатель"),
            "widget_type": widget_type if widget_type in {"analytics", "formula"} else "analytics",
            "view": view if view in WIDGET_VIEWS else "table",
            "size": size if size in WIDGET_SIZES else "medium",
            "formula": formula if formula in WIDGET_FORMULAS else "none",
            "formula_spec": widget.get("formula_spec") or {},
            "query": widget.get("query") or {},
            "settings": widget.get("settings") or {},
            "table_settings": widget.get("table_settings") or {},
            "layout": widget.get("layout") or {},
            "page_id": _safe_page_id(widget.get("page_id")),
        })
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def add_widget(db_path: Path, widget: dict[str, Any]) -> list[dict[str, Any]]:
    widgets = load_widgets(db_path)
    widgets.append(widget)
    return save_widgets(db_path, widgets)
