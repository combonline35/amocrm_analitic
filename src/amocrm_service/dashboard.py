from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


DASHBOARD_RELEASE = "2026.07.09-dashboard-pages-r53"


SYNC_OPTIONS = [
    ("leads", "Сделки", True),
    ("contacts", "Контакты", True),
    ("companies", "Компании", False),
    ("tasks", "Задачи", True),
    ("customers", "Покупатели", False),
    ("events", "События", False),
    ("lead_notes", "Примечания сделок", False),
    ("contact_notes", "Примечания контактов", False),
    ("company_notes", "Примечания компаний", False),
    ("customer_notes", "Примечания покупателей", False),
    ("users", "Пользователи", False),
    ("pipelines", "Воронки и этапы", True),
    ("lead_custom_fields", "Поля сделок", False),
    ("contact_custom_fields", "Поля контактов", False),
    ("company_custom_fields", "Поля компаний", False),
    ("customer_custom_fields", "Поля покупателей", False),
    ("catalogs", "Каталоги", False),
    ("catalog_elements", "Элементы каталогов", False),
    ("salesbots", "Salesbot", False),
]


def fmt_money(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _percent(part: int, total: int) -> int:
    if not total:
        return 0
    return round(part / total * 100)


def render_dashboard(
    summary: dict[str, Any],
    tasks: dict[str, Any],
    filter_options: list[dict[str, Any]] | None = None,
    active_filter: dict[str, list[int]] | None = None,
    sync_sources: list[dict[str, Any]] | None = None,
    selected_source_id: int | None = None,
    work_source_ids: list[int] | None = None,
    sync_result: list[dict[str, Any]] | None = None,
    page: str = "dashboard",
    user_key: str | None = None,
    account_key: str | None = None,
) -> str:
    totals = summary["totals"]
    sync_sources = sync_sources or []
    work_source_ids = work_source_ids or []
    query_builder = _render_query_builder_v2(
        sync_sources,
        selected_source_id=selected_source_id,
        filter_options=filter_options or [],
    )
    sync_sources_json = json.dumps(
        {
            int(source["id"]): {
                "name": str(source["name"]),
                "count": int(source.get("linked_leads_count") or source.get("linked_count") or 0),
                "fresh_at": source.get("linked_synced_at")
                or source.get("last_job_finished_at")
                or source.get("last_job_started_at")
                or source.get("updated_at"),
                "checked_at": source.get("source_checked_at") or source.get("updated_at"),
                "hub_fresh_at": source.get("hub_leads_synced_at"),
                "pipeline_names": list(source.get("pipeline_names") or []),
                "status_count": len(source.get("status_ids") or []),
                "pipeline_status_total": int(source.get("pipeline_status_total") or 0),
            }
            for source in sync_sources
        },
        ensure_ascii=False,
    )
    work_sources_json = json.dumps([int(item) for item in work_source_ids if int(item) > 0], ensure_ascii=False)

    active_pipeline_count = len(active_filter.get("pipeline_ids", [])) if active_filter else 0
    active_status_count = len(active_filter.get("status_ids", [])) if active_filter else 0
    is_settings = page == "settings"
    is_constructor = page == "constructor"
    query_suffix = (
        f"?user={html.escape(user_key)}&amp;account={html.escape(account_key)}"
        if user_key and account_key
        else ""
    )
    dashboard_href = f"/dashboard{query_suffix}"
    constructor_href = f"/constructor{query_suffix}"
    settings_href = f"/settings{query_suffix}"
    activity_href = f"/activity{query_suffix}"
    account_href = f"/app{query_suffix}"
    nav = f"""
        <nav class="top-nav" aria-label="Разделы аккаунта">
          <a class="nav-link" href="{account_href}">Аккаунт и выгрузки</a>
          <a class="nav-link {'active' if page == 'dashboard' else ''}" href="{dashboard_href}">Дашборд</a>
          <a class="nav-link" href="{activity_href}">Активность</a>
          <a class="nav-link {'active' if is_settings else ''}" href="{settings_href}">Массив данных</a>
          <a class="nav-link {'active' if is_constructor else ''}" href="{constructor_href}">Конструктор</a>
          <span class="release-badge" title="Версия выпуска дашборда">{DASHBOARD_RELEASE}</span>
        </nav>
    """
    page_sections = (
        query_builder
        if is_settings
        else _render_constructor_shell(sync_sources, work_source_ids, selected_source_id=selected_source_id)
        if is_constructor
        else _render_saved_dashboard()
    )
    hero_action = (
        '<button class="hero-action" type="button" data-hero-sync>Загрузить live-данные</button>'
        if is_settings
        else f'<a class="hero-action" href="{settings_href}">Настроить данные</a>'
    )
    hero_hidden = " hidden"

    return f"""
    <!doctype html>
    <html lang="ru">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>amoCRM Dashboard</title>
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <style>
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800;900&display=swap');
        :root {{
          color-scheme: light;
          --bg: #edf2f7;
          --panel: #ffffff;
          --panel-soft: #f8fafc;
          --ink: #07101f;
          --muted: #66758a;
          --quiet: #94a3b8;
          --line: #dbe4ee;
          --line-soft: #edf2f7;
          --accent: #0f8f72;
          --accent-soft: #e8f7f1;
          --blue: #2563eb;
          --blue-soft: #edf4ff;
          --red: #dc1f4c;
          --red-soft: #fff0f3;
          --shadow: 0 24px 70px rgba(15, 23, 42, .09);
        }}
        * {{ box-sizing: border-box; }}
        [hidden] {{ display: none !important; }}
        body {{
          margin: 0;
          background:
            radial-gradient(circle at 20% 0%, rgba(255, 255, 255, .95), rgba(255, 255, 255, 0) 32rem),
            linear-gradient(180deg, #f7f9fc 0%, var(--bg) 46rem);
          color: var(--ink);
          font: 14px/1.45 Montserrat, "Segoe UI", Arial, sans-serif;
        }}
        button, input, select {{ font: inherit; }}
        .shell {{
          width: min(1600px, calc(100% - 48px));
          margin: 0 auto;
          padding: 28px 0 56px;
        }}
        .top-nav {{
          display: inline-flex;
          gap: 8px;
          padding: 6px;
          margin-bottom: 18px;
          border: 1px solid var(--line);
          border-radius: 16px;
          background: rgba(255, 255, 255, .86);
          box-shadow: 0 12px 28px rgba(15, 23, 42, .05);
        }}
        .nav-link {{
          display: inline-flex;
          align-items: center;
          min-height: 38px;
          padding: 0 16px;
          border-radius: 12px;
          color: var(--muted);
          font-weight: 900;
          text-decoration: none;
        }}
        .nav-link.active {{
          background: var(--ink);
          color: #fff;
        }}
        .release-badge {{
          display: inline-flex;
          align-items: center;
          min-height: 38px;
          padding: 0 12px;
          border-radius: 12px;
          background: #eff6ff;
          color: #2563eb;
          border: 1px solid #bfdbfe;
          font-size: 12px;
          font-weight: 900;
          white-space: nowrap;
        }}
        .hero {{
          min-height: 286px;
          display: grid;
          grid-template-columns: minmax(0, 1fr) 420px;
          gap: 28px;
          align-items: start;
          padding: 28px;
          border-radius: 28px;
          background: rgba(255, 255, 255, .88);
          border: 1px solid rgba(219, 228, 238, .9);
          box-shadow: var(--shadow);
        }}
        .chips {{
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-bottom: 22px;
        }}
        .chip {{
          display: inline-flex;
          align-items: center;
          min-height: 28px;
          padding: 0 13px;
          border-radius: 999px;
          border: 1px solid var(--line);
          background: var(--panel-soft);
          color: #475569;
          font-size: 12px;
          font-weight: 700;
        }}
        .chip.dark {{
          color: #fff;
          background: #050b1d;
          border-color: #050b1d;
        }}
        h1 {{
          max-width: 780px;
          margin: 0 0 8px;
          font-size: clamp(38px, 5vw, 68px);
          line-height: .95;
          letter-spacing: 0;
        }}
        .lead {{
          margin: 0;
          color: var(--muted);
          font-size: 17px;
        }}
        .hero-panel {{
          display: grid;
          gap: 14px;
          justify-items: stretch;
        }}
        .segmented {{
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 4px;
          padding: 6px;
          min-height: 50px;
          border: 1px solid var(--line);
          border-radius: 18px;
          background: #eef3f8;
        }}
        .segment {{
          border: 0;
          border-radius: 13px;
          background: transparent;
          color: #64748b;
          font-weight: 800;
          cursor: default;
        }}
        .segment.active {{
          color: var(--ink);
          background: var(--panel);
          box-shadow: 0 8px 22px rgba(15, 23, 42, .08);
        }}
        .select-line {{
          display: grid;
          gap: 8px;
        }}
        .select-line label {{
          color: var(--muted);
          font-size: 11px;
          font-weight: 800;
          letter-spacing: .14em;
          text-transform: uppercase;
        }}
        .select-line select {{
          min-height: 46px;
          width: 100%;
          border: 1px solid #cbd7e6;
          border-radius: 14px;
          background: #fff;
          color: var(--ink);
          padding: 0 14px;
          font-weight: 700;
        }}
        .hero-action {{
          min-height: 48px;
          border: 0;
          border-radius: 15px;
          background: #050b1d;
          color: #fff;
          font-weight: 900;
          cursor: pointer;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          text-decoration: none;
        }}
        .hero-note {{
          color: var(--muted);
          text-align: right;
          font-size: 12px;
        }}
        .kpis {{
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 14px;
          margin: 20px 0;
        }}
        .kpi {{
          min-height: 154px;
          padding: 22px;
          border: 1px solid var(--line-soft);
          border-radius: 24px;
          background: rgba(255, 255, 255, .9);
          box-shadow: 0 18px 46px rgba(15, 23, 42, .07);
        }}
        .kpi-top {{
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: flex-start;
          margin-bottom: 14px;
        }}
        .eyebrow {{
          color: #93a4ba;
          font-size: 11px;
          font-weight: 900;
          letter-spacing: .18em;
          text-transform: uppercase;
        }}
        .iconbox {{
          width: 48px;
          height: 48px;
          display: grid;
          place-items: center;
          border: 1px solid var(--line);
          border-radius: 15px;
          color: #334155;
          background: #f8fbff;
          box-shadow: 0 6px 14px rgba(15, 23, 42, .08);
        }}
        .kpi strong {{
          display: block;
          margin-bottom: 4px;
          font-size: 31px;
          line-height: 1;
        }}
        .kpi small {{
          color: var(--muted);
          font-size: 14px;
        }}
        .pill {{
          display: inline-flex;
          align-items: center;
          min-height: 27px;
          margin-top: 18px;
          padding: 0 12px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 800;
          background: var(--blue-soft);
          color: var(--blue);
          border: 1px solid #bfd5ff;
        }}
        .pill.good {{ background: var(--accent-soft); color: var(--accent); border-color: #a9e4d1; }}
        .pill.bad {{ background: var(--red-soft); color: var(--red); border-color: #ffd0dc; }}
        .section-card {{
          margin-top: 20px;
          padding: 24px;
          border: 1px solid var(--line-soft);
          border-radius: 26px;
          background: rgba(255, 255, 255, .94);
          box-shadow: 0 18px 48px rgba(15, 23, 42, .06);
        }}
        .section-head {{
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 24px;
          margin-bottom: 20px;
        }}
        .section-head h2 {{
          margin: 4px 0 4px;
          font-size: 22px;
          letter-spacing: 0;
        }}
        .section-head p {{
          max-width: 880px;
          margin: 0;
          color: var(--muted);
        }}
        .mini-stats {{
          display: grid;
          grid-template-columns: repeat(2, minmax(110px, 1fr));
          gap: 8px;
          min-width: 260px;
        }}
        .mini-stat {{
          min-height: 62px;
          padding: 11px 12px;
          border: 1px solid var(--line-soft);
          border-radius: 16px;
          background: #fbfcfe;
        }}
        .mini-stat span {{
          display: block;
          color: var(--muted);
          font-size: 12px;
        }}
        .mini-stat strong {{
          font-size: 17px;
        }}
        .pipeline-grid {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
        }}
        .pipeline {{
          border: 1px solid var(--line-soft);
          border-radius: 18px;
          overflow: hidden;
          background: #fff;
        }}
        .pipeline header {{
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: center;
          padding: 16px 18px;
          border-bottom: 1px solid var(--line-soft);
          background: #fbfcfe;
        }}
        .pipeline h3 {{
          margin: 0;
          font-size: 16px;
          letter-spacing: 0;
        }}
        .pipeline-total {{
          color: var(--muted);
          text-align: right;
          white-space: nowrap;
        }}
        .pipeline-total strong {{
          display: block;
          color: var(--ink);
        }}
        table {{
          width: 100%;
          border-collapse: collapse;
        }}
        th, td {{
          padding: 10px 12px;
          border-bottom: 1px solid var(--line-soft);
          text-align: right;
          white-space: nowrap;
        }}
        th:first-child, td:first-child {{
          text-align: left;
          white-space: normal;
        }}
        th {{
          color: var(--quiet);
          font-size: 11px;
          font-weight: 900;
          letter-spacing: .08em;
          text-transform: uppercase;
          background: #fff;
        }}
        tr:last-child td {{ border-bottom: 0; }}
        .status {{ width: 42%; }}
        .settings-grid {{
          display: grid;
          grid-template-columns: minmax(0, .85fr) minmax(0, 1.15fr);
          gap: 20px;
          margin-top: 20px;
        }}
        .tool-panel {{
          padding: 22px;
          border: 1px solid var(--line-soft);
          border-radius: 24px;
          background: rgba(255, 255, 255, .94);
          box-shadow: 0 18px 48px rgba(15, 23, 42, .05);
        }}
        .tool-panel h2 {{
          margin: 0 0 14px;
          font-size: 20px;
        }}
        .builder-grid {{
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 14px;
          margin-bottom: 16px;
        }}
        .report-builder {{
          display: grid;
          gap: 16px;
        }}
        .builder-presets {{
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          justify-content: flex-end;
        }}
        .builder-step {{
          padding: 18px;
          border: 1px solid var(--line-soft);
          border-radius: 20px;
          background: #fbfcfe;
        }}
        .builder-step.primary {{
          border-color: #cbd7e6;
          background: #fff;
        }}
        .builder-step details {{
          margin: 0;
        }}
        .filter-step {{
          padding-bottom: 14px;
        }}
        .filter-step summary {{
          cursor: pointer;
          list-style: none;
        }}
        .filter-step summary::-webkit-details-marker {{
          display: none;
        }}
        .step-head {{
          display: flex;
          align-items: flex-start;
          gap: 12px;
          margin-bottom: 14px;
        }}
        .step-number {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 30px;
          height: 30px;
          flex: 0 0 30px;
          border-radius: 10px;
          background: #050b1d;
          color: #fff;
          font-weight: 900;
        }}
        .step-head h3 {{
          margin: 0;
          font-size: 17px;
          letter-spacing: 0;
        }}
        .step-head p {{
          margin: 2px 0 0;
          color: var(--muted);
          font-size: 13px;
        }}
        .builder-grid.two {{
          grid-template-columns: repeat(2, minmax(0, 1fr));
          margin-bottom: 0;
        }}
        .builder-grid.three {{
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          margin-bottom: 0;
        }}
        .source-choice-grid {{
          display: grid;
          grid-template-columns: minmax(260px, 1.1fr) minmax(240px, .9fr);
          gap: 16px;
          align-items: stretch;
        }}
        .source-note {{
          display: grid;
          gap: 10px;
          padding: 14px;
          border: 1px solid #bfdbfe;
          border-radius: 16px;
          background: #eff6ff;
          color: #1e3a5f;
          font-weight: 800;
        }}
        .source-note-head {{
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: center;
        }}
        .source-note-title {{
          font-size: 15px;
          color: #07101f;
        }}
        .source-status-pill {{
          display: inline-flex;
          align-items: center;
          min-height: 26px;
          padding: 0 10px;
          border-radius: 999px;
          background: #dcfce7;
          color: #047857;
          font-size: 12px;
          white-space: nowrap;
        }}
        .source-status-pill.stale {{
          background: #fff7ed;
          color: #c2410c;
        }}
        .source-note-grid {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
          gap: 8px;
        }}
        .source-note-item {{
          min-height: 58px;
          padding: 10px 12px;
          border: 1px solid #dbeafe;
          border-radius: 12px;
          background: rgba(255,255,255,.78);
        }}
        .source-note-item span {{
          display: block;
          margin-bottom: 3px;
          color: #64748b;
          font-size: 11px;
          font-weight: 900;
          letter-spacing: .08em;
          text-transform: uppercase;
        }}
        .source-note-item strong {{
          display: block;
          color: #0f172a;
          font-size: 14px;
          line-height: 1.25;
        }}
        .work-source-panel {{
          margin-top: 14px;
          padding: 14px;
          border: 1px solid #dbeafe;
          border-radius: 18px;
          background: #f8fbff;
        }}
        .work-source-panel h4 {{
          margin: 0 0 4px;
          font-size: 15px;
          letter-spacing: 0;
        }}
        .work-source-panel p {{
          margin: 0 0 12px;
          color: var(--muted);
          font-size: 13px;
        }}
        .work-source-list {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
          gap: 10px;
        }}
        .work-source-card {{
          display: grid;
          gap: 10px;
          padding: 12px;
          border: 1px solid #cfe2ff;
          border-radius: 14px;
          background: #fff;
        }}
        .work-source-card header {{
          display: flex;
          justify-content: space-between;
          gap: 10px;
          align-items: center;
        }}
        .work-source-card strong {{
          color: #07101f;
        }}
        .work-source-meta {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 6px;
        }}
        .work-source-meta span {{
          min-height: 44px;
          padding: 8px;
          border-radius: 10px;
          background: #eff6ff;
          color: #475569;
          font-size: 12px;
        }}
        .work-source-meta b {{
          display: block;
          color: #0f172a;
          font-size: 13px;
        }}
        .work-source-actions {{
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }}
        .work-source-actions button {{
          min-height: 34px;
          padding: 0 11px;
          border-radius: 10px;
          border: 1px solid #bfdbfe;
          background: #dbeafe;
          color: #0f3b72;
          font-weight: 900;
          cursor: pointer;
        }}
        .work-source-actions button.ghost {{
          background: #fff;
          color: #64748b;
          border-color: #e2e8f0;
        }}
        .work-source-empty {{
          padding: 14px;
          border: 1px dashed #bfdbfe;
          border-radius: 14px;
          color: var(--muted);
          background: #fff;
        }}
        .source-create-modal {{
          position: fixed;
          inset: 0;
          z-index: 60;
          display: grid;
          place-items: center;
          padding: 28px;
          background: rgba(15, 23, 42, .28);
          backdrop-filter: blur(4px);
        }}
        .source-create-modal[hidden] {{
          display: none;
        }}
        .source-create-dialog {{
          width: min(1040px, 100%);
          max-height: min(86vh, 840px);
          display: grid;
          grid-template-rows: auto minmax(0, 1fr);
          overflow: hidden;
          border: 1px solid #bfdbfe;
          border-radius: 22px;
          background: #fff;
          box-shadow: 0 28px 80px rgba(15, 23, 42, .24);
        }}
        .source-create-head {{
          display: flex;
          justify-content: space-between;
          gap: 18px;
          align-items: center;
          padding: 18px 20px;
          border-bottom: 1px solid #dbeafe;
          background: #eff6ff;
        }}
        .source-create-head h3 {{
          margin: 0;
          font-size: 20px;
          letter-spacing: 0;
        }}
        .source-create-head p {{
          margin: 4px 0 0;
          color: var(--muted);
        }}
        .modal-close {{
          width: 38px;
          height: 38px;
          border: 1px solid #bfdbfe;
          border-radius: 12px;
          background: #fff;
          color: #0f3b72;
          font-size: 22px;
          font-weight: 900;
          cursor: pointer;
        }}
        .source-create-body {{
          display: grid;
          gap: 14px;
          padding: 14px;
          overflow: auto;
        }}
        .source-create-tools {{
          display: grid;
          grid-template-columns: minmax(240px, .8fr) minmax(260px, 1fr);
          gap: 12px;
          align-items: end;
        }}
        .source-create-list {{
          display: grid;
          gap: 8px;
          max-height: 380px;
          overflow: auto;
          padding-right: 4px;
        }}
        .source-create-summary {{
          padding: 12px 14px;
          border: 1px solid #dbeafe;
          border-radius: 14px;
          background: #f8fbff;
          color: #475569;
          font-weight: 800;
        }}
        .builder-grid.result {{
          grid-template-columns: minmax(180px, .8fr) minmax(220px, 1fr) minmax(180px, .8fr);
          margin-bottom: 0;
        }}
        .condition-list {{
          display: grid;
          gap: 10px;
          margin-bottom: 0;
        }}
        .condition-row {{
          display: grid;
          grid-template-columns: minmax(0, 1fr) minmax(0, .72fr) minmax(0, .72fr) minmax(0, 1.35fr);
          gap: 10px;
          padding: 12px;
          border: 1px solid var(--line-soft);
          border-radius: 16px;
          background: #fff;
        }}
        .builder-actions {{
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          align-items: center;
          margin-top: 14px;
        }}
        .builder-actions button,
        .builder-actions .button-link {{
          min-height: 40px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border: 0;
          border-radius: 12px;
          padding: 0 15px;
          background: #dbeafe;
          color: #0f3b72;
          border: 1px solid #bfdbfe;
          font-weight: 900;
          text-decoration: none;
          cursor: pointer;
          box-shadow: 0 8px 18px rgba(37, 99, 235, .10);
        }}
        .builder-actions button.secondary,
        .builder-actions .button-link.secondary {{
          background: #f1f7ff;
          color: #12355b;
          border: 1px solid #cfe2ff;
        }}
        .builder-field {{
          display: grid;
          gap: 7px;
        }}
        .builder-field label, .metric-picker legend {{
          color: #93a4ba;
          font-size: 11px;
          font-weight: 900;
          letter-spacing: .12em;
          text-transform: uppercase;
        }}
        .builder-field input, .builder-field select {{
          min-height: 42px;
          width: 100%;
          border: 1px solid #cbd7e6;
          border-radius: 13px;
          background: #fff;
          color: var(--ink);
          padding: 0 12px;
          font-weight: 700;
        }}
        .metric-builder {{
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 12px;
        }}
        .report-table-wrap {{
          margin-top: 16px;
          overflow: auto;
          border: 1px solid var(--line-soft);
          border-radius: 18px;
          background: #fff;
        }}
        .formula-table-wrap {{
          border-color: #cfe2ff;
          border-radius: 16px;
          box-shadow: 0 16px 36px rgba(15, 23, 42, .06);
        }}
        .formula-data-table {{
          width: 100%;
          border-collapse: separate;
          border-spacing: 0;
          font-family: Montserrat, "Segoe UI", Arial, sans-serif;
          font-size: calc(14px * var(--widget-font-scale, 1));
          /* Данные — тёмно-серый вместо чёрного --ink; заголовки приглушены
             своим цветом ниже. Скоуп — только таблицы виджетов. */
          color: #3d4a5c;
        }}
        .formula-data-table th {{
          position: sticky;
          top: 0;
          z-index: 1;
          padding: calc(9px * var(--widget-font-scale, 1)) calc(12px * var(--widget-font-scale, 1));
          border-bottom: 1px solid #dbe7f6;
          border-right: 1px solid #e6eef8;
          background: #f4f8fd;
          color: #8a9bb3;
          font-size: calc(var(--widget-header-font, 11px) * var(--widget-font-scale, 1));
          font-weight: 900;
          letter-spacing: .11em;
          text-align: right;
          text-transform: uppercase;
          white-space: nowrap;
        }}
        .formula-data-table th:first-child {{
          left: 0;
          text-align: left;
        }}
        .formula-data-table td {{
          padding: calc(8px * var(--widget-font-scale, 1)) calc(12px * var(--widget-font-scale, 1));
          border-bottom: 1px solid #edf2f8;
          border-right: 1px solid #eef4fb;
          text-align: right;
          white-space: nowrap;
        }}
        .formula-data-table td:first-child {{
          text-align: left;
        }}
        .formula-data-table tr:nth-child(even) td {{
          background: #fbfdff;
        }}
        .formula-data-table tr:hover td {{
          background: #f1f7ff;
        }}
        .formula-data-table tr:last-child td {{
          border-bottom: 0;
        }}
        .formula-row-label {{
          color: #3d4a5c;
          font-weight: 800;
        }}
        .formula-summary-row td {{
          border-top: 1px solid #cfe2ff;
          background: #f0f7ff !important;
          font-weight: 900;
        }}
        .formula-cell-number,
        .formula-cell-percent {{
          font-variant-numeric: tabular-nums;
        }}
        .drilldown-link {{
          display: inline-flex;
          align-items: center;
          justify-content: flex-end;
          min-width: 24px;
          color: inherit;
          font-weight: inherit;
          text-decoration: none;
          border-bottom: 1px dotted rgba(37, 99, 235, .45);
          cursor: pointer;
        }}
        .drilldown-link:hover {{
          color: #1d4ed8;
          border-bottom-color: #1d4ed8;
        }}
        .drilldown-cell:hover {{
          background: #eaf3ff !important;
        }}
        .formula-cell-percent {{
          font-weight: 600;
        }}
        .formula-cell-percent.percent-good {{
          background: #eef6f0 !important;
          color: #33795a;
        }}
        .formula-cell-percent.percent-ok {{
          background: #f2f8f4 !important;
          color: #4a8370;
        }}
        .formula-cell-percent.percent-warn {{
          background: #f9f3e3 !important;
          color: #8a6b26;
        }}
        .formula-cell-percent.percent-bad {{
          background: #f9ecea !important;
          color: #a54a42;
        }}
        .formula-cell-percent.percent-zero {{
          background: #f8fafc !important;
          color: #64748b;
        }}
        .report-empty {{
          padding: 18px;
          border: 1px dashed var(--line);
          border-radius: 18px;
          background: #fbfcfe;
          color: var(--muted);
        }}
        .report-empty strong {{
          display: block;
          margin-bottom: 4px;
          color: var(--ink);
          font-size: 16px;
        }}
        .report-empty a {{
          color: var(--blue);
          font-weight: 900;
          text-decoration: none;
        }}
        .number-grid {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
          gap: 12px;
          margin-top: 16px;
        }}
        .number-card {{
          min-height: 116px;
          padding: 18px;
          border: 1px solid var(--line-soft);
          border-radius: 20px;
          background: #fff;
          box-shadow: 0 12px 32px rgba(15, 23, 42, .05);
        }}
        .number-card span {{
          display: block;
          color: var(--quiet);
          font-size: 11px;
          font-weight: 900;
          letter-spacing: .14em;
          text-transform: uppercase;
          margin-bottom: 12px;
        }}
        .number-card strong {{
          display: block;
          font-size: 30px;
          line-height: 1;
        }}
        .number-card .number-trend {{
          display: flex;
          align-items: baseline;
          gap: 6px;
          margin-top: 8px;
        }}
        .number-card .number-trend strong {{
          display: inline;
          font-size: 13px;
          line-height: 1.2;
          color: #64748b;
        }}
        .number-card .number-trend strong.trend-up {{
          color: #15803d;
        }}
        .number-card .number-trend strong.trend-down {{
          color: #b91c1c;
        }}
        .number-card .number-trend span {{
          display: inline;
          margin-bottom: 0;
          font-size: 11px;
          font-weight: 600;
          letter-spacing: 0;
          text-transform: none;
          color: var(--quiet);
        }}
        .visual-chart {{
          display: grid;
          gap: 12px;
        }}
        .chart-legend {{
          display: flex;
          justify-content: space-between;
          gap: 12px;
          color: var(--muted);
          font-size: 12px;
          font-weight: 800;
        }}
        .line-chart {{
          width: 100%;
          min-height: 260px;
          border: 1px solid var(--line-soft);
          border-radius: 18px;
          background: linear-gradient(180deg, #fbfdff 0%, #f7fbff 100%);
        }}
        .bar-list, .top-list {{
          display: grid;
          gap: 10px;
        }}
        .bar-row {{
          display: grid;
          grid-template-columns: minmax(120px, .75fr) minmax(180px, 1.6fr) minmax(70px, .35fr);
          gap: 12px;
          align-items: center;
        }}
        .bar-label, .top-label {{
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font-weight: 800;
        }}
        .bar-track {{
          height: 16px;
          overflow: hidden;
          border-radius: 999px;
          background: #edf3f9;
        }}
        .bar-fill {{
          display: block;
          height: 100%;
          border-radius: inherit;
          background: linear-gradient(90deg, #3b82f6, #22c7d8);
        }}
        .bar-value, .top-value {{
          text-align: right;
          font-weight: 900;
        }}
        .top-row {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          min-height: 44px;
          padding: 0 12px;
          border: 1px solid var(--line-soft);
          border-radius: 14px;
          background: #fbfdff;
        }}
        .constructor-workbench {{
          padding-bottom: 28px;
        }}
        .formula-layout {{
          display: grid;
          grid-template-columns: minmax(0, 1fr) minmax(390px, .36fr);
          gap: 18px;
          align-items: start;
        }}
        .formula-main {{
          min-width: 0;
          display: grid;
          gap: 14px;
        }}
        .formula-chain {{
          display: flex;
          gap: 10px;
          align-items: stretch;
          overflow-x: auto;
          padding: 10px;
          border: 1px solid #cfe2ff;
          border-radius: 22px;
          background: #f8fbff;
          box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .75);
        }}
        .formula-block {{
          flex: 0 0 210px;
          display: grid;
          gap: 8px;
          align-content: start;
          min-height: 104px;
          padding: 14px;
          border: 1px solid #bfdbfe;
          border-radius: 18px;
          background: #fff;
          box-shadow: 0 10px 24px rgba(15, 23, 42, .06);
        }}
        .formula-block.title-block {{
          flex-basis: 250px;
        }}
        .formula-block.source-block {{
          flex-basis: 280px;
        }}
        .formula-block span {{
          color: #93a4ba;
          font-size: 11px;
          font-weight: 900;
          letter-spacing: .13em;
          text-transform: uppercase;
        }}
        .formula-block input,
        .formula-block select {{
          min-height: 42px;
          width: 100%;
          border: 1px solid #cbd7e6;
          border-radius: 13px;
          background: #fff;
          color: var(--ink);
          padding: 0 12px;
          font-weight: 800;
        }}
        .formula-arrow {{
          flex: 0 0 28px;
          display: grid;
          place-items: center;
          color: #2563eb;
          font-size: 22px;
          font-weight: 900;
        }}
        .formula-details {{
          display: grid;
          gap: 12px;
        }}
        .formula-panel {{
          border: 1px solid var(--line-soft);
          border-radius: 20px;
          background: #fff;
          overflow: hidden;
        }}
        .formula-panel summary {{
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: center;
          min-height: 58px;
          padding: 14px 16px;
          cursor: pointer;
          list-style: none;
          background: #fbfcfe;
          border-bottom: 1px solid var(--line-soft);
        }}
        .formula-panel summary::-webkit-details-marker {{
          display: none;
        }}
        .formula-panel summary strong {{
          font-size: 16px;
        }}
        .formula-panel summary span {{
          color: var(--muted);
          font-size: 12px;
          font-weight: 800;
        }}
        .formula-panel > .condition-list,
        .formula-panel > .builder-field,
        .formula-panel > .formula-inline-grid,
        .formula-panel > .plan-settings {{
          margin: 14px;
        }}
        .formula-panel.manual-mode summary {{
          min-height: 46px;
          padding: 10px 16px;
        }}
        .formula-panel.manual-mode summary strong {{
          font-size: 13px;
          font-weight: 700;
          color: var(--muted);
        }}
        .manual-mode-body {{
          display: grid;
          gap: 14px;
          padding: 14px;
        }}
        .formula-lab-layout {{
          display: grid;
          grid-template-columns: minmax(0, 1fr) minmax(380px, .48fr);
          gap: 18px;
          align-items: start;
        }}
        .formula-editor-panel,
        .formula-preview-panel {{
          display: grid;
          gap: 14px;
          min-width: 0;
          padding: 18px;
          border: 1px solid #bfdbfe;
          border-radius: 22px;
          background: #f8fbff;
        }}
        .formula-preview-panel {{
          position: sticky;
          top: 18px;
          background: linear-gradient(180deg, #f8fbff 0%, #fff 100%);
          box-shadow: 0 18px 44px rgba(15, 23, 42, .07);
        }}
        .formula-preview-title {{
          display: grid;
          gap: 7px;
          padding: 12px;
          border: 1px solid #dbeafe;
          border-radius: 16px;
          background: #ffffff;
        }}
        .formula-preview-title input {{
          width: 100%;
          min-height: 42px;
          border: 1px solid #bfdbfe;
          border-radius: 12px;
          padding: 0 13px;
          background: #f8fbff;
          color: #07101f;
          font: inherit;
          font-weight: 800;
        }}
        .formula-toolbar {{
          display: grid;
          grid-template-columns: minmax(220px, .7fr) minmax(260px, 1fr);
          gap: 12px;
        }}
        .formula-template-grid {{
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 10px;
        }}
        .formula-template-grid button {{
          min-height: 42px;
          border: 1px solid #bfdbfe;
          border-radius: 13px;
          background: #eaf4ff;
          color: #0f3b72;
          font-weight: 900;
        }}
        .formula-human-builder {{
          display: grid;
          gap: 14px;
          padding: 16px;
          border: 1px solid #bfdbfe;
          border-radius: 20px;
          background: #ffffff;
        }}
        .formula-mask-grid {{
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 12px;
        }}
        .formula-filter-list {{
          display: grid;
          gap: 10px;
        }}
        .formula-add-filter {{
          justify-self: start;
          margin-top: 10px;
        }}
        .formula-filter-row {{
          display: grid;
          grid-template-columns: minmax(180px, 1fr) minmax(150px, .6fr) minmax(190px, 1fr);
          gap: 10px;
          padding: 10px;
          border: 1px solid #dbeafe;
          border-radius: 16px;
          background: #f8fbff;
        }}
        .formula-readable {{
          padding: 14px;
          border: 1px solid #cfe2ff;
          border-radius: 16px;
          background: #eff6ff;
          color: #12355b;
          font-weight: 800;
          line-height: 1.55;
        }}
        .formula-readable b {{
          color: #07101f;
        }}
        .ai-formula-box {{
          display: grid;
          gap: 12px;
          padding: 16px;
          border: 1px solid #bfdbfe;
          border-radius: 20px;
          background: linear-gradient(180deg, #f8fbff 0%, #ffffff 100%);
        }}
        .ai-formula-box textarea {{
          min-height: 104px;
          width: 100%;
          resize: vertical;
          border: 1px solid #cbd7e6;
          border-radius: 15px;
          padding: 12px 14px;
          background: #fff;
          color: var(--ink);
          font: inherit;
        }}
        .ai-formula-actions {{
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          align-items: center;
        }}
        .ai-formula-result {{
          display: grid;
          gap: 10px;
        }}
        .filter-import-card {{
          display: grid;
          gap: 10px;
          padding: 12px;
          border: 1px solid #dbeafe;
          border-radius: 16px;
          background: #f8fbff;
        }}
        .filter-import-grid {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 8px;
        }}
        .filter-import-pill {{
          display: grid;
          gap: 4px;
          padding: 10px 12px;
          border-radius: 14px;
          background: #eaf4ff;
          border: 1px solid #bfdbfe;
        }}
        .filter-import-pill span {{
          color: var(--muted);
          font-size: 11px;
          font-weight: 900;
          text-transform: uppercase;
        }}
        .filter-import-pill b {{
          color: var(--ink);
          font-size: 13px;
        }}
        .filter-import-list {{
          display: grid;
          gap: 6px;
          margin: 0;
          padding: 0;
          list-style: none;
        }}
        .filter-import-list li {{
          display: grid;
          grid-template-columns: 30px 1fr;
          gap: 8px;
          align-items: center;
          padding: 8px 10px;
          border-radius: 12px;
          background: #fff;
          border: 1px solid #e2e8f0;
        }}
        .filter-import-list li span {{
          display: inline-grid;
          place-items: center;
          width: 24px;
          height: 24px;
          border-radius: 10px;
          background: #dbeafe;
          color: #1d4ed8;
          font-size: 12px;
          font-weight: 900;
        }}
        .ai-draft-card {{
          padding: 14px;
          border: 1px solid #dbeafe;
          border-radius: 16px;
          background: #fff;
        }}
        .ai-draft-card h4 {{
          margin: 0 0 6px;
          font-size: 16px;
        }}
        .ai-draft-card p {{
          margin: 0;
          color: var(--muted);
        }}
        .ai-question-box {{
          display: grid;
          gap: 10px;
          margin-top: 10px;
          padding: 12px;
          border: 1px solid #dbeafe;
          border-radius: 14px;
          background: #f8fbff;
        }}
        .ai-question-row {{
          display: grid;
          gap: 8px;
          padding: 10px;
          border: 1px solid #e5edf7;
          border-radius: 12px;
          background: #fff;
        }}
        .ai-question-row strong {{
          font-size: 13px;
        }}
        .ai-question-controls {{
          display: grid;
          grid-template-columns: minmax(180px, .8fr) minmax(220px, 1fr) auto auto;
          gap: 8px;
          align-items: center;
        }}
        .ai-question-controls select,
        .ai-question-controls input {{
          min-height: 38px;
          border: 1px solid #cbd7e6;
          border-radius: 10px;
          padding: 0 10px;
          background: #fff;
          color: var(--ink);
          font-weight: 700;
        }}
        .ai-question-apply {{
          justify-self: start;
        }}
        .formula-explanation-list,
        .formula-diagnostics-list {{
          display: grid;
          gap: 10px;
          margin-top: 12px;
        }}
        .formula-diagnostics,
        .formula-explanation {{
          border: 1px solid #bfdbfe;
          border-radius: 16px;
          background: #f8fbff;
          overflow: hidden;
        }}
        .formula-diagnostics summary,
        .formula-explanation summary {{
          display: grid;
          grid-template-columns: auto minmax(0, 1fr) auto;
          gap: 12px;
          align-items: center;
          padding: 13px 14px;
          cursor: pointer;
          list-style: none;
        }}
        .formula-diagnostics summary::-webkit-details-marker,
        .formula-explanation summary::-webkit-details-marker {{
          display: none;
        }}
        .formula-diagnostics summary::before,
        .formula-explanation summary::before {{
          content: '+';
          display: inline-grid;
          place-items: center;
          width: 22px;
          height: 22px;
          border-radius: 8px;
          background: #dbeafe;
          color: #0b3b78;
          font-weight: 900;
        }}
        .formula-diagnostics[open] summary::before,
        .formula-explanation[open] summary::before {{
          content: '−';
        }}
        .formula-diagnostics summary > span,
        .formula-explanation summary > span {{
          display: flex;
          min-width: 0;
          align-items: center;
        }}
        .formula-diagnostics summary b,
        .formula-explanation summary b {{
          display: block;
          overflow: hidden;
          color: #07101f;
          font-size: 15px;
          text-overflow: ellipsis;
          white-space: nowrap;
        }}
        .formula-diagnostics summary small,
        .formula-explanation summary small {{
          display: block;
          margin-top: 3px;
          color: var(--muted);
          font-size: 12px;
          font-weight: 700;
        }}
        .formula-diagnostics summary strong,
        .formula-explanation summary strong {{
          padding: 6px 10px;
          border-radius: 999px;
          background: #eaf3ff;
          color: #12355b;
          font-size: 13px;
          white-space: nowrap;
        }}
        .formula-explanation-steps {{
          display: grid;
          gap: 8px;
          padding: 0 14px 14px;
        }}
        .formula-explanation-step {{
          display: grid;
          grid-template-columns: 28px minmax(0, 1fr) auto;
          gap: 10px;
          align-items: start;
          padding: 10px;
          border: 1px solid #dbeafe;
          border-radius: 12px;
          background: rgba(255, 255, 255, .78);
        }}
        .formula-explanation-step b {{
          display: grid;
          place-items: center;
          width: 24px;
          height: 24px;
          border-radius: 8px;
          background: #dbeafe;
          color: #0b3b78;
          font-size: 12px;
        }}
        .formula-explanation-step span {{
          color: var(--muted);
        }}
        .formula-explanation-step strong {{
          color: var(--ink);
          white-space: nowrap;
        }}
        .diagnostic-row {{
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 12px;
          align-items: center;
          padding: 9px 10px;
          border: 1px solid #e5edf7;
          border-radius: 11px;
          background: #fff;
        }}
        .diagnostic-row span {{
          color: var(--muted);
        }}
        .diagnostic-row strong {{
          font-size: 16px;
        }}
        .diagnostic-rows {{
          display: grid;
          gap: 8px;
          padding: 0 14px 14px;
        }}
        .formula-editor {{
          min-height: 360px;
          width: 100%;
          padding: 14px;
          resize: vertical;
          border: 1px solid #bfdbfe;
          border-radius: 16px;
          background: #fff;
          color: #07101f;
          font: 13px/1.5 Consolas, "SFMono-Regular", monospace;
        }}
        .formula-dictionary {{
          display: grid;
          gap: 10px;
          max-height: 310px;
          overflow: auto;
          padding: 14px;
          color: var(--muted);
        }}
        .dictionary-group {{
          padding: 12px;
          border: 1px solid #dbeafe;
          border-radius: 14px;
          background: #fff;
        }}
        .dictionary-group strong {{
          display: block;
          color: var(--ink);
          margin-bottom: 6px;
        }}
        .dictionary-fields {{
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
        }}
        .dictionary-field {{
          display: inline-flex;
          align-items: center;
          min-height: 26px;
          padding: 0 9px;
          border-radius: 999px;
          background: #eff6ff;
          color: #1e3a5f;
          font-size: 12px;
          font-weight: 800;
        }}
        .compact-size {{
          min-width: 150px;
        }}
        .formula-inline-grid {{
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 12px;
        }}
        .formula-preview {{
          top: 18px;
        }}
        .constructor-layout {{
          display: grid;
          grid-template-columns: minmax(0, 1.08fr) minmax(420px, .92fr);
          gap: 18px;
          align-items: start;
        }}
        .constructor-tools {{
          display: grid;
          gap: 14px;
        }}
        .constructor-preview-panel {{
          position: sticky;
          top: 18px;
          display: grid;
          gap: 14px;
          padding: 18px;
          border: 1px solid #cfe2ff;
          border-radius: 22px;
          background: linear-gradient(180deg, #f8fbff 0%, #ffffff 100%);
          box-shadow: 0 18px 44px rgba(15, 23, 42, .07);
        }}
        .constructor-preview-head {{
          display: flex;
          justify-content: space-between;
          gap: 14px;
          align-items: flex-start;
        }}
        .constructor-preview-head h3 {{
          margin: 2px 0 0;
          font-size: 20px;
          letter-spacing: 0;
        }}
        .constructor-preview-actions {{
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          align-items: center;
        }}
        .constructor-preview-actions button {{
          min-height: 42px;
          border-radius: 13px;
          padding: 0 16px;
          border: 1px solid #bfdbfe;
          background: #dbeafe;
          color: #0f3b72;
          font-weight: 900;
          cursor: pointer;
          box-shadow: 0 8px 18px rgba(37, 99, 235, .10);
        }}
        .constructor-preview-actions button.secondary {{
          background: #f1f7ff;
          color: #12355b;
          border-color: #cfe2ff;
        }}
        .constructor-preview-result {{
          min-height: 260px;
        }}
        .constructor-preview-result .number-grid,
        .constructor-preview-result .report-table-wrap {{
          margin-top: 0;
        }}
        .constructor-source-strip {{
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-bottom: 12px;
        }}
        .constructor-source-pill {{
          display: inline-flex;
          gap: 8px;
          align-items: center;
          min-height: 32px;
          max-width: 100%;
          padding: 0 11px;
          border: 1px solid #bfdbfe;
          border-radius: 999px;
          background: #eff6ff;
          color: #12355b;
          font-weight: 900;
        }}
        .constructor-source-pill b {{
          color: #0f8f72;
        }}
        .source-note.small {{
          margin-top: 12px;
          box-shadow: none;
        }}
        .constructor-metrics {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 12px;
        }}
        .plan-settings {{
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 10px;
          padding: 12px;
          border: 1px solid #dbeafe;
          border-radius: 16px;
          background: #f8fbff;
        }}
        .plan-settings .builder-field input {{
          min-height: 40px;
        }}
        .condition-list.compact {{
          gap: 8px;
        }}
        .condition-row.compact {{
          grid-template-columns: minmax(130px, 1.05fr) minmax(120px, .85fr) minmax(88px, .62fr) minmax(180px, 1.15fr);
          padding: 10px;
        }}
        .builder-field.slim {{
          min-width: 0;
        }}
        .logic-field {{
          max-width: 320px;
          margin-bottom: 12px;
        }}
        .work-source-empty.compact {{
          width: 100%;
          padding: 12px;
          background: #f8fbff;
        }}
        .saved-dashboard-grid {{
          display: grid;
          grid-template-columns: repeat(12, minmax(0, 1fr));
          gap: 14px;
          margin-top: 16px;
        }}
        .dashboard-pages-bar {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-top: 12px;
          padding: 10px;
          border: 1px solid var(--line-soft);
          border-radius: 18px;
          background: #fbfcfe;
        }}
        .dashboard-page-tabs {{
          display: flex;
          flex-wrap: wrap;
          gap: 7px;
          min-width: 0;
        }}
        .dashboard-page-tab {{
          min-height: 34px;
          padding: 0 13px;
          border: 1px solid #cfe2ff;
          border-radius: 999px;
          background: #eff6ff;
          color: #1e3a5f;
          cursor: pointer;
          font-weight: 900;
        }}
        .dashboard-page-tab.active {{
          background: #0b1528;
          border-color: #0b1528;
          color: #fff;
        }}
        .dashboard-page-controls {{
          display: inline-flex;
          gap: 6px;
          align-items: center;
          flex: 0 0 auto;
        }}
        .saved-dashboard-grid:not(.edit-mode) ~ .dashboard-edit-only,
        .dashboard-page-controls .edit-only {{
          display: none;
        }}
        body.dashboard-edit-mode .dashboard-page-controls .edit-only {{
          display: inline-grid;
        }}
        .dashboard-edit-hint {{
          display: inline-flex;
          align-items: center;
          min-height: 34px;
          margin-top: 10px;
          padding: 0 12px;
          border: 1px solid #bfdbfe;
          border-radius: 999px;
          background: #eff6ff;
          color: #1d4ed8;
          font-size: 12px;
          font-weight: 800;
        }}
        .saved-widget {{
          position: relative;
          display: flex;
          flex-direction: column;
          --widget-font-scale: 1;
          grid-column: span 6;
          min-height: 170px;
          padding: 0;
          border: 1px solid var(--line-soft);
          border-radius: 20px;
          background: #fff;
          overflow: hidden;
          box-shadow: 0 12px 34px rgba(15, 23, 42, .055);
        }}
        .saved-widget.small {{
          grid-column: span 3;
        }}
        .saved-widget.medium {{
          grid-column: span 6;
        }}
        .saved-widget.large {{
          grid-column: span 8;
        }}
        .saved-widget.wide {{
          grid-column: span 12;
        }}
        .saved-widget.dragging {{
          opacity: .55;
        }}
        .saved-widget.drag-over {{
          outline: 2px solid #93c5fd;
          outline-offset: 3px;
        }}
        .saved-widget.menu-open,
        .saved-widget.settings-open,
        .saved-widget.details-open {{
          overflow: visible;
          z-index: 20;
        }}
        .saved-widget.settings-open {{
          min-height: 360px;
        }}
        .saved-dashboard-grid:not(.edit-mode) .saved-widget {{
          cursor: default;
        }}
        .saved-dashboard-grid:not(.edit-mode) .saved-widget.menu-open,
        .saved-dashboard-grid:not(.edit-mode) .saved-widget.settings-open,
        .saved-dashboard-grid:not(.edit-mode) .saved-widget.details-open {{
          overflow: hidden;
          z-index: auto;
        }}
        .saved-dashboard-grid:not(.edit-mode) .widget-actions,
        .saved-dashboard-grid:not(.edit-mode) .widget-resize-grip,
        .saved-dashboard-grid:not(.edit-mode) .widget-control-panel,
        .saved-dashboard-grid:not(.edit-mode) .widget-meta-panel {{
          display: none !important;
        }}
        .saved-widget h3 {{
          margin: 0;
          font-size: calc(16px * var(--widget-font-scale, 1));
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }}
        .saved-widget-title {{
          min-width: 0;
          display: grid;
          gap: 3px;
        }}
        .saved-widget-source {{
          color: var(--muted);
          font-size: calc(12px * var(--widget-font-scale, 1));
          font-weight: 800;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }}
        .saved-widget .number-grid {{
          margin-top: 0;
        }}
        .saved-widget-header {{
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: center;
          min-height: 58px;
          padding: calc(14px * var(--widget-font-scale, 1)) calc(16px * var(--widget-font-scale, 1));
          border-bottom: 1px solid var(--line-soft);
          background: #fbfcfe;
        }}
        .widget-actions {{
          display: inline-flex;
          gap: 6px;
          align-items: center;
          flex: 0 0 auto;
        }}
        .widget-menu-wrap {{
          position: relative;
          display: inline-flex;
        }}
        .widget-menu {{
          position: absolute;
          top: calc(100% + 8px);
          right: 0;
          z-index: 8;
          display: none;
          min-width: 210px;
          padding: 6px;
          border: 1px solid var(--line);
          border-radius: 14px;
          background: #fff;
          box-shadow: 0 18px 42px rgba(15, 23, 42, .14);
        }}
        .saved-widget.menu-open .widget-menu {{
          display: grid;
          gap: 3px;
        }}
        .widget-menu button {{
          width: 100%;
          min-height: 34px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          border: 0;
          border-radius: 10px;
          background: transparent;
          color: #334155;
          cursor: pointer;
          font-weight: 800;
          text-align: left;
        }}
        .widget-menu button:hover {{
          background: #f1f7ff;
        }}
        .widget-menu button.danger {{
          color: var(--red);
        }}
        .icon-button {{
          width: 34px;
          height: 34px;
          display: inline-grid;
          place-items: center;
          border: 1px solid var(--line);
          border-radius: 11px;
          background: #fff;
          color: #475569;
          cursor: pointer;
          font-weight: 900;
        }}
        .icon-button.danger {{
          color: var(--red);
          border-color: #ffd0dc;
          background: var(--red-soft);
        }}
        .icon-button.active {{
          background: #0b1528;
          color: #fff;
          border-color: #0b1528;
        }}
        .widget-meta-panel {{
          display: none;
          padding: 12px 16px;
          border-bottom: 1px solid var(--line-soft);
          background: #f8fbff;
          color: var(--muted);
          font-size: calc(12px * var(--widget-font-scale, 1));
          font-weight: 700;
        }}
        .saved-widget.details-open .widget-meta-panel {{
          display: grid;
          gap: 6px;
        }}
        .widget-meta-panel strong {{
          color: #334155;
          font-weight: 900;
        }}
        .widget-drag-handle {{
          cursor: grab;
          user-select: none;
        }}
        .widget-drag-handle:active {{
          cursor: grabbing;
        }}
        .widget-control-panel {{
          display: none;
          grid-template-columns: repeat(4, minmax(150px, 1fr));
          gap: 10px;
          max-height: min(320px, 45vh);
          overflow: auto;
          align-content: start;
          padding: 12px 16px;
          border-bottom: 1px solid var(--line-soft);
          background: #f8fbff;
        }}
        .saved-widget.settings-open .widget-control-panel {{
          display: grid;
        }}
        .widget-control-panel.compact {{
          grid-template-columns: minmax(150px, 220px);
        }}
        .widget-control-panel label {{
          display: grid;
          gap: 5px;
          color: #8a9bb3;
          font-size: 10px;
          font-weight: 900;
          letter-spacing: .1em;
          text-transform: uppercase;
        }}
        .widget-control-panel .wide-field {{
          grid-column: span 2;
        }}
        .widget-control-panel .widget-columns-block {{
          display: grid;
          gap: 5px;
          align-content: start;
        }}
        .widget-columns-title {{
          color: #8a9bb3;
          font-size: 10px;
          font-weight: 900;
          letter-spacing: .1em;
          text-transform: uppercase;
        }}
        .formula-data-table thead th {{
          vertical-align: bottom;
        }}
        .formula-data-table.has-fixed-columns {{
          /* Только для таблиц с заданными ширинами: auto-раскладка трактует
             width на th как пожелание и растягивает колонки по контенту. */
          table-layout: fixed;
        }}
        .formula-data-table.has-fixed-columns th {{
          min-width: 40px;
          /* В узких колонках капс и 11px не читаются: обычный регистр,
             мельче и без разрядки. Обычные таблицы не трогаем. */
          font-size: calc(var(--widget-header-font, 10px) * var(--widget-font-scale, 1));
          text-transform: none;
          letter-spacing: .02em;
        }}
        .formula-data-table.has-fixed-columns td {{
          overflow: hidden;
          white-space: nowrap;
          text-overflow: ellipsis;
        }}
        .formula-data-table.has-fixed-columns th,
        .formula-data-table.has-fixed-columns td {{
          padding-left: calc(7px * var(--widget-font-scale, 1));
          padding-right: calc(7px * var(--widget-font-scale, 1));
        }}
        .formula-data-table th .formula-col-title {{
          display: block;
        }}
        .formula-data-table th.formula-col-fixed .formula-col-title {{
          /* Заголовок переносится ТОЛЬКО по пробелам и растит шапку в высоту;
             слово, которое не влезает, обрезается, а не рвётся. Больше 3
             строк — обрезка, полное имя в title у th. */
          display: -webkit-box;
          -webkit-box-orient: vertical;
          -webkit-line-clamp: 3;
          line-clamp: 3;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: normal;
          word-break: normal;
          overflow-wrap: normal;
        }}
        .widget-columns-list {{
          display: flex;
          flex-direction: column;
          gap: 2px;
          max-height: min(240px, 40vh);
          overflow-y: auto;
          padding: 4px;
          border: 1px solid #cfe2ff;
          border-radius: 11px;
          background: #fff;
        }}
        .widget-column-row {{
          display: flex;
          align-items: center;
          gap: 8px;
          min-height: 32px;
          padding: 0 6px;
          border-radius: 8px;
        }}
        .widget-column-row:hover {{
          background: #eaf4ff;
        }}
        .widget-control-panel label.widget-column-toggle {{
          display: flex;
          flex: 1;
          min-width: 0;
          align-items: center;
          gap: 8px;
          margin: 0;
          color: #12355b;
          font-size: 13px;
          font-weight: 700;
          letter-spacing: 0;
          text-transform: none;
          cursor: pointer;
        }}
        .widget-control-panel .widget-column-toggle input {{
          flex: none;
          width: 16px;
          min-height: 16px;
          margin: 0;
        }}
        .widget-column-name {{
          min-width: 0;
          overflow: hidden;
          white-space: nowrap;
          text-overflow: ellipsis;
        }}
        .widget-control-panel label.widget-column-toggle {{
          flex: 1 1 40%;
          min-width: 90px;
        }}
        .widget-control-panel .widget-column-row input.widget-column-title-input {{
          flex: 1 1 30%;
          min-width: 80px;
          width: auto;
          min-height: 26px;
          padding: 0 8px;
          font-size: 12px;
          border-radius: 8px;
        }}
        .widget-control-panel .widget-column-row input.widget-column-width-input {{
          flex: none;
          width: 70px;
          min-height: 26px;
          padding: 0 6px;
          font-size: 12px;
          border-radius: 8px;
        }}
        .widget-column-row:has(input:not(:checked)) .widget-column-name {{
          color: #8a9bb3;
          font-weight: 600;
        }}
        .column-builder-toolbar {{
          display: flex;
          align-items: flex-end;
          gap: 12px;
          flex-wrap: wrap;
        }}
        .column-builder-toolbar .builder-field {{
          margin: 0;
        }}
        .column-builder-list {{
          display: flex;
          flex-direction: column;
          gap: 10px;
        }}
        .column-builder-row {{
          display: grid;
          gap: 8px;
          padding: 12px;
          border: 1px solid var(--line-soft);
          border-radius: 14px;
          background: #fff;
        }}
        .column-builder-row-head {{
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }}
        .column-builder-row-head input[data-column-title] {{
          flex: 1 1 200px;
          min-width: 140px;
        }}
        .ai-formula-box .column-builder-row textarea {{
          min-height: 52px;
        }}
        .column-builder-row-status {{
          font-size: 12px;
          font-weight: 600;
          color: var(--muted);
        }}
        .column-builder-row-status.ok {{
          color: #15803d;
        }}
        .column-builder-row-status.error {{
          color: #b91c1c;
        }}
        .column-builder-preview {{
          font-size: 12px;
          color: var(--quiet);
        }}
        .column-builder-live-caption {{
          margin-bottom: 6px;
          font-size: 11px;
          color: var(--quiet);
        }}
        .column-builder-live-table {{
          width: 100%;
          border-collapse: collapse;
          font-size: 12.5px;
          color: #3d4a5c;
        }}
        .column-builder-live-table th {{
          padding: 6px 8px;
          border-bottom: 1px solid #dbe7f6;
          background: #f4f8fd;
          color: #8a9bb3;
          font-size: 10px;
          font-weight: 800;
          letter-spacing: .06em;
          text-transform: uppercase;
          text-align: right;
          white-space: nowrap;
        }}
        .column-builder-live-table td {{
          padding: 5px 8px;
          border-bottom: 1px solid #edf2f8;
          text-align: right;
          white-space: nowrap;
          font-variant-numeric: tabular-nums;
        }}
        .column-builder-live-table th:first-child,
        .column-builder-live-table td:first-child {{
          text-align: left;
        }}
        .column-builder-live-table tr:last-child td {{
          border-bottom: 0;
        }}
        .column-builder-live-label {{
          font-weight: 700;
        }}
        .widget-column-move {{
          display: flex;
          flex: none;
          gap: 4px;
        }}
        .widget-column-move button {{
          width: 26px;
          min-height: 26px;
          padding: 0;
          border: 1px solid #bfdbfe;
          border-radius: 8px;
          background: #eaf4ff;
          color: #0f3b72;
          font-weight: 900;
          line-height: 1;
          cursor: pointer;
        }}
        .widget-column-move button:hover:not(:disabled) {{
          background: #dbeafe;
        }}
        .widget-column-move button:disabled {{
          opacity: .35;
          cursor: default;
        }}
        .widget-control-panel .panel-actions {{
          display: flex;
          gap: 8px;
          align-items: end;
          justify-content: flex-end;
        }}
        .widget-control-panel .panel-actions button {{
          min-height: 36px;
          border: 1px solid #bfdbfe;
          border-radius: 11px;
          background: #eaf4ff;
          color: #0f3b72;
          cursor: pointer;
          font-weight: 900;
          padding: 0 12px;
        }}
        .widget-control-panel select,
        .widget-control-panel input {{
          min-height: 36px;
          width: 100%;
          border: 1px solid #cfe2ff;
          border-radius: 11px;
          background: #fff;
          color: var(--ink);
          padding: 0 10px;
          font-weight: 800;
        }}
        .widget-control-panel .checkbox-control {{
          align-content: end;
          grid-template-columns: auto 1fr;
          gap: 8px;
          min-height: 55px;
          color: #12355b;
          letter-spacing: 0;
          text-transform: none;
        }}
        .widget-control-panel .checkbox-control input {{
          width: 16px;
          min-height: 16px;
          margin: 0;
        }}
        .widget-control-help {{
          grid-column: 1 / -1;
          margin: -2px 0 0;
          color: var(--muted);
          font-size: 12px;
          font-weight: 700;
        }}
        .widget-resize-grip {{
          position: absolute;
          border: 0;
          background: transparent;
          color: #12355b;
          opacity: .0;
          transition: opacity .16s ease, background .16s ease;
          font-weight: 900;
        }}
        .widget-resize-grip.corner {{
          right: 8px;
          bottom: 8px;
          width: 24px;
          height: 24px;
          border: 1px solid #cfe2ff;
          border-radius: 8px;
          background: #f1f7ff;
          cursor: nwse-resize;
          font-size: 13px;
        }}
        .widget-resize-grip.right {{
          top: 64px;
          right: 0;
          bottom: 34px;
          width: 10px;
          cursor: ew-resize;
        }}
        .widget-resize-grip.bottom {{
          right: 34px;
          bottom: 0;
          left: 16px;
          height: 10px;
          cursor: ns-resize;
        }}
        .widget-resize-grip.right:hover,
        .widget-resize-grip.bottom:hover {{
          background: rgba(147, 197, 253, .32);
        }}
        .saved-widget:hover .widget-resize-grip,
        .saved-widget.resizing .widget-resize-grip {{
          opacity: 1;
        }}
        .saved-widget.resizing {{
          outline: 2px solid #bfdbfe;
          outline-offset: 3px;
          user-select: none;
        }}
        .widget-body {{
          flex: 1 1 auto;
          min-height: 0;
          padding: calc(16px * var(--widget-font-scale, 1));
          overflow: auto;
        }}
        .saved-dashboard-toolbar {{
          display: flex;
          gap: 8px;
          align-items: center;
          justify-content: flex-end;
        }}
        .preset-button.active {{
          background: #0b1528;
          color: #fff;
          border-color: #0b1528;
        }}
        .sync-grid {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(165px, 1fr));
          gap: 10px 12px;
          margin-bottom: 16px;
        }}
        label.sync-option, .stage-option {{
          display: flex;
          gap: 9px;
          align-items: center;
          min-height: 34px;
          color: var(--ink);
        }}
        input[type="checkbox"] {{
          width: 16px;
          height: 16px;
          flex: 0 0 auto;
          accent-color: var(--accent);
        }}
        .actions {{
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          align-items: center;
        }}
        .actions button {{
          min-height: 40px;
          border: 0;
          border-radius: 12px;
          padding: 0 15px;
          background: var(--accent);
          color: white;
          cursor: pointer;
          font-weight: 900;
        }}
        .actions button.secondary {{
          background: #eef3f8;
          color: var(--ink);
          border: 1px solid var(--line);
        }}
        button:disabled {{
          opacity: .65;
          cursor: wait;
        }}
        .sync-status {{
          color: var(--muted);
          font-size: 13px;
        }}
        .sync-status a {{
          color: var(--blue);
          font-weight: 900;
          text-decoration: none;
        }}
        .sync-status.error {{
          color: var(--red);
        }}
        .sync-result {{
          margin-top: 12px;
          padding: 12px;
          background: #f8fafc;
          border: 1px solid var(--line-soft);
          border-radius: 14px;
          color: var(--muted);
        }}
        .filter-list {{
          display: grid;
          gap: 8px;
          max-height: 520px;
          overflow: auto;
          padding-right: 4px;
          margin-bottom: 16px;
        }}
        .filter-toolbar {{
          display: grid;
          gap: 12px;
          margin-bottom: 14px;
        }}
        .search-input {{
          min-height: 44px;
          width: 100%;
          border: 1px solid #cbd7e6;
          border-radius: 14px;
          background: #fff;
          color: var(--ink);
          padding: 0 14px;
          font-weight: 700;
        }}
        .preset-row {{
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }}
        .preset-button {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 34px;
          max-width: 220px;
          border: 1px solid var(--line);
          border-radius: 999px;
          background: #f8fafc;
          color: #334155;
          padding: 0 12px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          cursor: pointer;
          font-weight: 800;
          text-decoration: none;
        }}
        .preset-button.primary {{
          background: #050b1d;
          border-color: #050b1d;
          color: #fff;
        }}
        .filter-pipeline[hidden] {{
          display: none;
        }}
        .filter-pipeline {{
          border: 1px solid var(--line);
          border-radius: 16px;
          background: #fbfcfe;
        }}
        .filter-pipeline summary {{
          cursor: pointer;
          list-style: none;
          padding: 12px 14px;
        }}
        .filter-pipeline summary::-webkit-details-marker {{ display: none; }}
        .filter-pipeline summary label {{
          display: inline-flex;
          align-items: center;
          gap: 9px;
          font-weight: 900;
        }}
        .stage-grid {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
          gap: 8px 14px;
          padding: 0 14px 14px 38px;
        }}
        .stage-option {{
          color: var(--muted);
          min-width: 0;
        }}
        .stage-option span {{
          min-width: 0;
          overflow-wrap: anywhere;
        }}
        @media (max-width: 1100px) {{
          .hero, .settings-grid, .pipeline-grid {{
            grid-template-columns: 1fr;
          }}
          .builder-grid {{
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }}
          .source-choice-grid,
          .builder-grid.result {{
            grid-template-columns: 1fr;
          }}
          .constructor-layout {{
            grid-template-columns: 1fr;
          }}
          .formula-layout {{
            grid-template-columns: 1fr;
          }}
          .constructor-preview-panel {{
            position: static;
          }}
          .formula-inline-grid {{
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }}
          .hero-panel {{
            justify-items: stretch;
          }}
          .kpis {{
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }}
        }}
        @media (max-width: 720px) {{
          .shell {{
            width: min(100% - 24px, 1600px);
            padding-top: 14px;
          }}
          .hero, .section-card, .tool-panel {{
            border-radius: 18px;
            padding: 18px;
          }}
          .kpis {{
            grid-template-columns: 1fr;
          }}
          .section-head {{
            flex-direction: column;
          }}
          .mini-stats {{
            width: 100%;
            min-width: 0;
          }}
          table {{ font-size: 12px; }}
          th, td {{ padding: 8px 7px; }}
          .builder-grid {{
            grid-template-columns: 1fr;
          }}
          .condition-row {{
            grid-template-columns: 1fr;
          }}
          .constructor-metrics,
          .plan-settings,
          .formula-inline-grid,
          .condition-row.compact {{
            grid-template-columns: 1fr;
          }}
          .formula-chain {{
            padding: 8px;
          }}
          .formula-block {{
            flex-basis: 240px;
          }}
          .constructor-preview-actions {{
            align-items: stretch;
          }}
          .constructor-preview-actions button {{
            width: 100%;
          }}
          .saved-dashboard-grid {{
            grid-template-columns: 1fr;
          }}
          .saved-widget,
          .saved-widget.small,
          .saved-widget.medium,
          .saved-widget.large,
          .saved-widget.wide {{
            grid-column: 1;
          }}
          .widget-control-panel {{
            grid-template-columns: 1fr;
          }}
        }}
      </style>
    </head>
    <body>
      <div class="shell">
        {nav}
        <section class="hero"{hero_hidden}>
          <div>
            <div class="chips">
              <span class="chip dark">amoCRM</span>
              <span class="chip">аналитика</span>
              <span class="chip">локальное зеркало</span>
            </div>
            <h1>Операционный дашборд</h1>
            <p class="lead">Сделки, задачи, воронки и выбранные этапы в одной панели. Сначала выбираем, что синхронизировать, затем собираем отчет только по нужной аналитике.</p>
          </div>
          <div class="hero-panel">
            <div class="segmented" aria-label="Период отчета">
              <button class="segment active" type="button">Неделя</button>
              <button class="segment" type="button">10 дней</button>
              <button class="segment" type="button">Месяц</button>
            </div>
            <div class="select-line">
              <label for="period-select">Отчетный лист</label>
              <select id="period-select">
                <option>Текущий срез данных</option>
                <option>Июнь 2026</option>
              </select>
            </div>
            {hero_action}
            <div class="hero-note">Сумма в текущем срезе: {fmt_money(totals['total_price'])}</div>
          </div>
        </section>

        {page_sections}

      </div>
      <script>
        window.__amoSyncSources = {sync_sources_json};
        window.__amoWorkSources = {work_sources_json};
        const statusEl = document.querySelector('[data-sync-status]');
        const syncBtn = document.querySelector('[data-sync-button]');
        const heroSyncBtn = document.querySelector('[data-hero-sync]');
        const allBtn = document.querySelector('[data-select-all]');
        const coreBtn = document.querySelector('[data-select-core]');
        const checks = [...document.querySelectorAll('[data-sync-entity]')];
        const filterStatusEl = document.querySelector('[data-filter-status]');
        const saveFilterBtn = document.querySelector('[data-save-filter]');
        const filterAllBtn = document.querySelector('[data-filter-all]');
        const filterNoneBtn = document.querySelector('[data-filter-none]');
        const filterOnlyVisibleBtn = document.querySelector('[data-filter-only-visible]');
        const filterSearchInput = document.querySelector('[data-filter-search]');
        const presetButtons = [...document.querySelectorAll('[data-preset-pipeline]')];
        const pipelineBlocks = [...document.querySelectorAll('[data-filter-block]')];
        const pipelineChecks = [...document.querySelectorAll('[data-filter-pipeline]')];
        const statusChecks = [...document.querySelectorAll('[data-filter-status-id]')];
        const reportBtn = document.querySelector('[data-report-run]');
        const reportPresetBtns = [...document.querySelectorAll('[data-report-preset]')];
        const reportStatusEl = document.querySelector('[data-report-status]');
        const sourceRefreshBtn = document.querySelector('[data-source-refresh]');
        const reportResultEl = document.querySelector('[data-report-result]');
        const savedDashboardEl = document.querySelector('[data-saved-dashboard]');
        const dashboardRefreshBtn = document.querySelector('[data-dashboard-refresh]');
        const dashboardEditToggleBtn = document.querySelector('[data-dashboard-edit-toggle]');
        const dashboardEditHintEl = document.querySelector('[data-dashboard-edit-hint]');
        const dashboardPagesEl = document.querySelector('[data-dashboard-pages]');
        const dashboardPagePrevBtn = document.querySelector('[data-dashboard-page-prev]');
        const dashboardPageNextBtn = document.querySelector('[data-dashboard-page-next]');
        const dashboardPageAddBtn = document.querySelector('[data-dashboard-page-add]');
        const dashboardPageRenameBtn = document.querySelector('[data-dashboard-page-rename]');
        const dashboardPageDeleteBtn = document.querySelector('[data-dashboard-page-delete]');
        const saveWidgetBtn = document.querySelector('[data-report-save-widget]');
        const formulaCopyBtn = document.querySelector('[data-formula-copy]');
        const formulaEditorEl = document.querySelector('[data-formula-editor]');
        const formulaRunBtn = document.querySelector('[data-formula-run]');
        const formulaSaveBtn = document.querySelector('[data-formula-save]');
        const formulaSourceEl = document.querySelector('[data-formula-source]');
        const formulaTitleEl = document.querySelector('[data-formula-title]');
        const formulaPreviewTitleEl = document.querySelector('[data-formula-preview-title]');
        const formulaSizeEl = document.querySelector('[data-formula-size]');
        const formulaStatusEl = document.querySelector('[data-formula-status]');
        const formulaResultEl = document.querySelector('[data-formula-result]');
        const formulaDictionaryEl = document.querySelector('[data-formula-dictionary]');
        const formulaTemplateBtns = [...document.querySelectorAll('[data-formula-template]')];
        const formulaEntityEl = document.querySelector('[data-formula-entity]');
        const formulaOpEl = document.querySelector('[data-formula-op]');
        const formulaValueFieldEl = document.querySelector('[data-formula-value-field]');
        const formulaGroupFieldEl = document.querySelector('[data-formula-group-field]');
        const formulaFilterListEl = document.querySelector('[data-formula-filter-list]');
        const formulaFilterAddBtn = document.querySelector('[data-formula-filter-add]');
        let formulaFilterRows = [...document.querySelectorAll('[data-formula-filter]')];
        const formulaReadableEl = document.querySelector('[data-formula-readable]');
        const aiFormulaPromptEl = document.querySelector('[data-ai-formula-prompt]');
        const aiFormulaRunBtn = document.querySelector('[data-ai-formula-run]');
        const aiFormulaApplyBtn = document.querySelector('[data-ai-formula-apply]');
        const aiFormulaStatusEl = document.querySelector('[data-ai-formula-status]');
        const aiFormulaResultEl = document.querySelector('[data-ai-formula-result]');
        const amoFilterUrlEl = document.querySelector('[data-amo-filter-url]');
        const amoFilterParseBtn = document.querySelector('[data-amo-filter-parse]');
        const amoFilterApplyBtn = document.querySelector('[data-amo-filter-apply]');
        const amoFilterStatusEl = document.querySelector('[data-amo-filter-status]');
        const amoFilterResultEl = document.querySelector('[data-amo-filter-result]');
        const widgetTitleEl = document.querySelector('[data-widget-title]');
        const widgetPageEls = [...document.querySelectorAll('[data-widget-page]')];
        const widgetSizeEl = document.querySelector('[data-widget-size]');
        const widgetFormulaEl = document.querySelector('[data-widget-formula]');
        const widgetPlanEl = document.querySelector('[data-widget-plan]');
        const widgetPeriodDaysEl = document.querySelector('[data-widget-period-days]');
        const widgetDaysPassedEl = document.querySelector('[data-widget-days-passed]');
        const reportSourceEl = document.querySelector('[data-report-source]');
        const reportSourceNoteEl = document.querySelector('[data-report-source-note]');
        const sourceWorkAddBtn = document.querySelector('[data-source-work-add]');
        const workSourcesEl = document.querySelector('[data-work-sources]');
        const sourceCreateOpenBtn = document.querySelector('[data-source-create-open]');
        const createSourceModalEl = document.querySelector('[data-create-source-modal]');
        const createSourceCloseBtns = [...document.querySelectorAll('[data-create-source-close]')];
        const createSourceNameEl = document.querySelector('[data-create-source-name]');
        const createSourceSearchEl = document.querySelector('[data-create-source-search]');
        const createSourceRunBtn = document.querySelector('[data-create-source-run]');
        const createSourceClearBtn = document.querySelector('[data-create-source-clear]');
        const createSourceStatusEl = document.querySelector('[data-create-source-status]');
        const createSourceSummaryEl = document.querySelector('[data-create-source-summary]');
        const createSourceBlocks = [...document.querySelectorAll('[data-create-source-block]')];
        const createSourcePipelineChecks = [...document.querySelectorAll('[data-create-source-pipeline]')];
        const createSourceStatusChecks = [...document.querySelectorAll('[data-create-source-status]')];
        const reportGroupEl = document.querySelector('[data-report-group]');
        const reportViewEl = document.querySelector('[data-report-view]');
        const reportLogicEl = document.querySelector('[data-report-logic]');
        const reportConditionRows = [...document.querySelectorAll('[data-report-condition]')];
        const reportMetricSelects = [...document.querySelectorAll('[data-report-metric-select]')];
        const syncSourcesIndex = window.__amoSyncSources || {{}};
        const workSourceIds = new Set((window.__amoWorkSources || []).map((value) => Number(value)).filter(Boolean));
        let savedWidgetsCache = [];
        let dashboardPagesCache = [{{ id: 'main', name: 'Основной' }}];
        let activeDashboardPageId = 'main';
        let draggedWidgetId = null;
        let resizingWidget = null;
        let lastFormulaResult = null;
        let lastFormulaDiagnostics = null;
        let formulaDictionaryCache = null;
        let lastAiFormulaDraft = null;
        let aiFormulaPinned = false;
        let lastAmoFilterImport = null;
        const apiUrl = (path, extra = {{}}) => {{
          const params = new URLSearchParams(window.location.search);
          Object.entries(extra).forEach(([key, value]) => {{
            if (value !== undefined && value !== null && value !== '') params.set(key, value);
          }});
          const query = params.toString();
          return query ? `${{path}}?${{query}}` : path;
        }};
        const dashboardEditStorageKey = 'amo-dashboard-edit-mode';
        const closeDashboardWidgetPanels = () => {{
          savedDashboardEl?.querySelectorAll('.saved-widget.menu-open, .saved-widget.settings-open, .saved-widget.details-open').forEach((item) => {{
            item.classList.remove('menu-open', 'settings-open', 'details-open');
          }});
        }};
        const dashboardEditMode = () => Boolean(savedDashboardEl?.classList.contains('edit-mode'));
        const setDashboardEditMode = (enabled) => {{
          if (!savedDashboardEl) return;
          savedDashboardEl.classList.toggle('edit-mode', enabled);
          document.body.classList.toggle('dashboard-edit-mode', enabled);
          if (dashboardEditToggleBtn) {{
            dashboardEditToggleBtn.classList.toggle('active', enabled);
            dashboardEditToggleBtn.textContent = enabled ? 'Готово' : 'Настроить виджеты';
            dashboardEditToggleBtn.setAttribute('aria-pressed', enabled ? 'true' : 'false');
          }}
          if (dashboardEditHintEl) dashboardEditHintEl.hidden = !enabled;
          if (!enabled) closeDashboardWidgetPanels();
          try {{
            window.localStorage.setItem(dashboardEditStorageKey, enabled ? '1' : '0');
          }} catch (error) {{}}
        }};
        const normalizeDashboardPages = (pages) => {{
          const source = Array.isArray(pages) && pages.length ? pages : [{{ id: 'main', name: 'Основной' }}];
          const result = [];
          const seen = new Set();
          source.forEach((page, index) => {{
            const rawId = String(page?.id || (index === 0 ? 'main' : `page-${{index + 1}}`)).trim();
            const id = rawId || 'main';
            if (seen.has(id)) return;
            seen.add(id);
            result.push({{ id, name: String(page?.name || (id === 'main' ? 'Основной' : `Лист ${{index + 1}}`)).trim() || 'Лист' }});
          }});
          if (!result.some((page) => page.id === 'main')) result.unshift({{ id: 'main', name: 'Основной' }});
          return result;
        }};
        const pageNameById = (pageId) => dashboardPagesCache.find((page) => page.id === pageId)?.name || 'Основной';
        const makeDashboardPageId = (name) => {{
          const base = String(name || 'page')
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9а-яё_-]+/gi, '-')
            .replace(/^-+|-+$/g, '') || 'page';
          let candidate = base.slice(0, 50);
          let counter = 2;
          while (dashboardPagesCache.some((page) => page.id === candidate)) {{
            candidate = `${{base.slice(0, 44)}}-${{counter}}`;
            counter += 1;
          }}
          return candidate;
        }};
        const updateWidgetPageControls = () => {{
          const options = dashboardPagesCache.map((page) => `<option value="${{safeText(page.id)}}">${{safeText(page.name)}}</option>`).join('');
          widgetPageEls.forEach((select) => {{
            const current = select.value || activeDashboardPageId || 'main';
            select.innerHTML = options;
            select.value = dashboardPagesCache.some((page) => page.id === current) ? current : activeDashboardPageId || 'main';
          }});
        }};
        const renderDashboardPages = () => {{
          dashboardPagesCache = normalizeDashboardPages(dashboardPagesCache);
          if (!dashboardPagesCache.some((page) => page.id === activeDashboardPageId)) activeDashboardPageId = dashboardPagesCache[0]?.id || 'main';
          if (dashboardPagesEl) {{
            dashboardPagesEl.innerHTML = dashboardPagesCache.map((page) => `
              <button type="button" class="dashboard-page-tab ${{page.id === activeDashboardPageId ? 'active' : ''}}" data-dashboard-page-id="${{safeText(page.id)}}">
                ${{safeText(page.name)}}
              </button>
            `).join('');
          }}
          updateWidgetPageControls();
        }};
        const saveDashboardPages = async (pages) => {{
          const response = await fetch(apiUrl('/api/dashboard-pages'), {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ pages }}),
          }});
          const data = await response.json();
          if (!response.ok || !data.ok) throw new Error(data.error || 'pages save failed');
          dashboardPagesCache = normalizeDashboardPages(data.pages);
          renderDashboardPages();
          return dashboardPagesCache;
        }};
        const setActiveDashboardPage = async (pageId, reload = true) => {{
          activeDashboardPageId = dashboardPagesCache.some((page) => page.id === pageId) ? pageId : (dashboardPagesCache[0]?.id || 'main');
          try {{
            window.localStorage.setItem('amo-dashboard-active-page', activeDashboardPageId);
          }} catch (error) {{}}
          renderDashboardPages();
          if (reload) await loadSavedDashboard(false, true, true);
        }};
        const selectedWidgetPageId = () => {{
          const selected = widgetPageEls.find((select) => select.offsetParent !== null)?.value || widgetPageEls[0]?.value || activeDashboardPageId || 'main';
          return dashboardPagesCache.some((page) => page.id === selected) ? selected : 'main';
        }};
        const refreshDashboardWidgetResults = async () => {{
          const response = await fetch(apiUrl('/api/dashboard-widget-results', {{ refresh: '1' }}));
          const data = await response.json();
          if (!response.ok || !data.ok) throw new Error(data.error || 'dashboard refresh failed');
          return data;
        }};
        const selectedFormulaSourceName = () => {{
          const text = formulaSourceEl?.selectedOptions?.[0]?.textContent || '';
          return text.split(' · ')[0].trim();
        }};
        const suggestedFormulaTitle = () => {{
          const sourceName = lastAmoFilterImport?.source?.name || selectedFormulaSourceName();
          const conditionCount = Array.isArray(lastAmoFilterImport?.conditions) ? lastAmoFilterImport.conditions.length : 0;
          if (sourceName && conditionCount) return `${{sourceName}}: фильтр amoCRM (${{conditionCount}} условий)`;
          if (sourceName) return `${{sourceName}}: показатель`;
          return 'Новый показатель';
        }};
        const currentFormulaTitle = () => (formulaPreviewTitleEl?.value || formulaTitleEl?.value || '').trim();
        const setFormulaTitle = (title, options = {{}}) => {{
          const value = (title || '').trim();
          if (!value) return;
          const force = Boolean(options.force);
          if (formulaTitleEl && (force || !formulaTitleEl.value.trim())) formulaTitleEl.value = value;
          if (formulaPreviewTitleEl && (force || !formulaPreviewTitleEl.value.trim())) formulaPreviewTitleEl.value = value;
        }};
        const syncFormulaTitleInputs = (source, target) => {{
          if (!source || !target) return;
          source.addEventListener('input', () => {{
            if (target.value !== source.value) target.value = source.value;
          }});
        }};
        document.querySelectorAll('[data-settings-link]').forEach((link) => {{
          link.href = apiUrl('/settings');
        }});
        document.querySelectorAll('[data-dashboard-link]').forEach((link) => {{
          link.href = apiUrl('/dashboard');
        }});
        document.querySelectorAll('[data-constructor-link]').forEach((link) => {{
          link.href = apiUrl('/constructor');
        }});

        allBtn?.addEventListener('click', () => {{
          checks.forEach((item) => item.checked = true);
        }});
        coreBtn?.addEventListener('click', () => {{
          const core = new Set(['leads', 'contacts', 'tasks', 'pipelines']);
          checks.forEach((item) => item.checked = core.has(item.value));
        }});
        heroSyncBtn?.addEventListener('click', () => syncBtn?.click());
        syncBtn?.addEventListener('click', async () => {{
          const entities = checks.filter((item) => item.checked).map((item) => item.value);
          if (!entities.length) {{
            statusEl.textContent = 'Выбери хотя бы один тип данных';
            statusEl.classList.add('error');
            return;
          }}
          syncBtn.disabled = true;
          heroSyncBtn.disabled = true;
          statusEl.classList.remove('error');
          statusEl.textContent = 'Синхронизирую: ' + entities.join(', ');
          try {{
            const response = await fetch(apiUrl('/api/sync'), {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ entities }}),
            }});
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'sync failed');
            statusEl.textContent = 'Готово. Обновляю страницу...';
            window.location.reload();
          }} catch (error) {{
            statusEl.textContent = 'Ошибка: ' + error.message;
            statusEl.classList.add('error');
            syncBtn.disabled = false;
            heroSyncBtn.disabled = false;
          }}
        }});
        pipelineChecks.forEach((item) => {{
          item.addEventListener('change', () => {{
            const pipelineId = item.value;
            statusChecks
              .filter((status) => status.dataset.pipelineId === pipelineId)
              .forEach((status) => status.checked = item.checked);
          }});
        }});
        const setPipelineSelection = (pipelineId, checked) => {{
          pipelineChecks
            .filter((item) => item.value === pipelineId)
            .forEach((item) => item.checked = checked);
          statusChecks
            .filter((status) => status.dataset.pipelineId === pipelineId)
            .forEach((status) => status.checked = checked);
        }};
        const clearFilterSelection = () => {{
          pipelineChecks.forEach((item) => item.checked = false);
          statusChecks.forEach((item) => item.checked = false);
        }};
        const visiblePipelineIds = () => pipelineBlocks
          .filter((block) => !block.hidden)
          .map((block) => block.dataset.pipelineId);
        const applyFilterSearch = () => {{
          const query = (filterSearchInput?.value || '').trim().toLowerCase();
          pipelineBlocks.forEach((block) => {{
            const matched = !query || block.dataset.filterText.includes(query);
            block.hidden = !matched;
            if (matched && query) block.open = true;
          }});
          if (filterStatusEl) {{
            const count = visiblePipelineIds().length;
            filterStatusEl.textContent = query ? `Найдено воронок: ${{count}}` : 'Выбери нужные воронки и этапы';
            filterStatusEl.classList.remove('error');
          }}
        }};
        filterSearchInput?.addEventListener('input', applyFilterSearch);
        filterOnlyVisibleBtn?.addEventListener('click', () => {{
          const ids = new Set(visiblePipelineIds());
          clearFilterSelection();
          ids.forEach((pipelineId) => setPipelineSelection(pipelineId, true));
        }});
        presetButtons.forEach((button) => {{
          button.addEventListener('click', () => {{
            const pipelineId = button.dataset.presetPipeline;
            clearFilterSelection();
            setPipelineSelection(pipelineId, true);
            pipelineBlocks.forEach((block) => {{
              block.hidden = false;
              block.open = block.dataset.pipelineId === pipelineId;
            }});
            if (filterSearchInput) filterSearchInput.value = button.textContent.trim();
            if (filterStatusEl) {{
              filterStatusEl.textContent = `Выбрана воронка: ${{button.textContent.trim()}}`;
              filterStatusEl.classList.remove('error');
            }}
          }});
        }});
        filterAllBtn?.addEventListener('click', () => {{
          pipelineChecks.forEach((item) => item.checked = true);
          statusChecks.forEach((item) => item.checked = true);
        }});
        filterNoneBtn?.addEventListener('click', () => {{
          clearFilterSelection();
        }});
        saveFilterBtn?.addEventListener('click', async () => {{
          const pipelineIds = pipelineChecks.filter((item) => item.checked).map((item) => Number(item.value));
          const statusIds = statusChecks.filter((item) => item.checked).map((item) => Number(item.value));
          if (!pipelineIds.length) {{
            filterStatusEl.textContent = 'Выбери хотя бы одну воронку';
            filterStatusEl.classList.add('error');
            return;
          }}
          saveFilterBtn.disabled = true;
          filterStatusEl.classList.remove('error');
          filterStatusEl.textContent = 'Сохраняю фильтр аналитики...';
          try {{
            const response = await fetch(apiUrl('/api/analytics-filter'), {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ pipeline_ids: pipelineIds, status_ids: statusIds }}),
            }});
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'filter save failed');
            filterStatusEl.textContent = 'Фильтр сохранен. Обновляю отчет...';
            window.location.reload();
          }} catch (error) {{
            filterStatusEl.textContent = 'Ошибка: ' + error.message;
            filterStatusEl.classList.add('error');
            saveFilterBtn.disabled = false;
          }}
        }});
        const reportPresets = {{
          sources: {{
            view: 'table',
            logic: 'and',
            group_by: 'cf_127785',
            filters: [],
            metrics: ['count', 'sum_price', 'avg_price'],
          }},
          created_month: {{
            view: 'table',
            logic: 'and',
            group_by: 'created_month',
            filters: [{{ field: 'created_at', op: 'date_between', value: '2026-01-01..2026-06-30' }}],
            metrics: ['count', 'sum_price', 'avg_price'],
          }},
          contract_month: {{
            view: 'table',
            logic: 'and',
            group_by: 'cf_month_127845',
            filters: [{{ field: 'cf_127845', op: 'date_between', value: '2020-01-01..2026-12-31' }}],
            metrics: ['count', 'sum_price', 'avg_price'],
          }},
          kpi_created: {{
            view: 'number',
            logic: 'and',
            group_by: 'created_month',
            filters: [{{ field: 'created_at', op: 'date_between', value: '2026-01-01..2026-06-30' }}],
            metrics: ['count', 'sum_price', 'avg_price'],
          }},
        }};
        const selectedReportMetrics = () => {{
          const result = [];
          reportMetricSelects.forEach((select) => {{
            const value = select.value;
            if (value && !result.includes(value)) result.push(value);
          }});
          return result.length ? result : ['count'];
        }};
        const applyMetricPreset = (metrics = []) => {{
          reportMetricSelects.forEach((select, index) => {{
            select.value = metrics[index] || '';
          }});
          if (reportMetricSelects[0] && !reportMetricSelects[0].value) reportMetricSelects[0].value = 'count';
        }};
        const setReportPreset = (preset) => {{
          if (reportViewEl) reportViewEl.value = preset.view;
          if (reportLogicEl) reportLogicEl.value = preset.logic;
          if (reportGroupEl) reportGroupEl.value = preset.group_by;
          reportConditionRows.forEach((row, index) => {{
            const filter = preset.filters[index] || {{ field: '', op: 'eq', value: '' }};
            row.querySelector('[data-report-filter-field]').value = filter.field;
            row.querySelector('[data-report-filter-op]').value = filter.op;
            row.querySelector('[data-report-value-type]').value = filter.value_type || 'auto';
            row.querySelector('[data-report-filter-value]').value = filter.value;
          }});
          applyMetricPreset(preset.metrics);
        }};
        reportPresetBtns.forEach((button) => {{
          button.addEventListener('click', () => setReportPreset(reportPresets[button.dataset.reportPreset]));
        }});
        const fillSelectOptions = (select, fields, includeEmpty = false) => {{
          if (!select) return;
          const current = select.value;
          select.innerHTML = '';
          if (includeEmpty) select.append(new Option('Без условия', ''));
          fields.forEach((field) => {{
            const option = new Option(field.label, field.value);
            option.dataset.kind = field.kind || '';
            option.dataset.type = field.type || '';
            option.dataset.suggestedValueType = field.suggested_value_type || field.type || 'auto';
            select.append(option);
          }});
          if ([...select.options].some((option) => option.value === current)) {{
            select.value = current;
          }}
        }};
        const applySuggestedValueType = (row) => {{
          const fieldSelect = row.querySelector('[data-report-filter-field]');
          const typeSelect = row.querySelector('[data-report-value-type]');
          if (!fieldSelect || !typeSelect) return;
          const option = fieldSelect.selectedOptions[0];
          const suggested = option?.dataset?.suggestedValueType || 'auto';
          typeSelect.value = ['text', 'number', 'date', 'datetime'].includes(suggested) ? suggested : 'auto';
        }};
        const loadReportFields = async () => {{
          try {{
            const response = await fetch(apiUrl('/api/analytics/fields'));
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || 'Не удалось загрузить поля');
            fillSelectOptions(reportGroupEl, data.group_fields || [], false);
            reportConditionRows.forEach((row) => {{
              fillSelectOptions(row.querySelector('[data-report-filter-field]'), data.filter_fields || [], true);
            }});
          }} catch (error) {{
            console.warn('analytics fields failed', error);
          }}
        }};
        const loadFieldValues = async (row) => {{
          const field = row.querySelector('[data-report-filter-field]').value;
          const input = row.querySelector('[data-report-filter-value]');
          const list = row.querySelector('datalist');
          list.innerHTML = '';
          if (!field || ['created_at', 'updated_at', 'closed_at', 'price', 'cf_127845'].includes(field)) return;
          try {{
            const response = await fetch(apiUrl('/api/analytics/field-values', {{ field, limit: 200 }}));
            const data = await response.json();
            if (!response.ok || !data.ok) return;
            list.innerHTML = data.values
              .map((item) => `<option value="${{String(item.value ?? '').replace(/"/g, '&quot;')}}">${{item.count}}</option>`)
              .join('');
          }} catch (_error) {{}}
        }};
        loadReportFields();
        reportConditionRows.forEach((row) => {{
          row.querySelector('[data-report-filter-field]').addEventListener('change', () => {{
            applySuggestedValueType(row);
            loadFieldValues(row);
          }});
        }});
        const parseReportValue = (op, raw, valueType = 'auto') => {{
          if (op === 'in' || op === 'not_in') {{
            return raw.split(',').map((item) => item.trim()).filter(Boolean).map((item) => {{
              if (valueType === 'number') return Number(item);
              return valueType === 'auto' && isNaN(Number(item)) ? item : (valueType === 'auto' ? Number(item) : item);
            }});
          }}
          if (op === 'between' || op === 'date_between') {{
            return raw.split('..').map((item) => item.trim()).filter(Boolean);
          }}
          if (valueType === 'number') return Number(raw);
          if (valueType === 'text') return raw;
          return isNaN(Number(raw)) || raw.trim() === '' ? raw : Number(raw);
        }};
        const metricLabels = {{
          value: 'Результат',
          count: 'Количество',
          sum_price: 'Сумма',
          avg_price: 'Средний чек',
          open_count: 'Открыто',
          won_count: 'Успешно',
          lost_count: 'Потеряно',
        }};
        const columnLabels = {{
          pipeline_id: 'ID воронки',
          pipeline_name: 'Воронка',
          status_id: 'ID этапа',
          status_name: 'Этап',
          responsible_user_id: 'Ответственный',
          created_month: 'Месяц создания',
          updated_month: 'Месяц обновления',
          closed_month: 'Месяц закрытия',
          cf_127785: 'Рекламная площадка',
          cf_month_127845: 'Месяц договора',
          count: 'Количество',
          sum_price: 'Сумма',
          avg_price: 'Средний чек',
          open_count: 'Открыто',
          won_count: 'Успешно',
          lost_count: 'Потеряно',
        }};
        const formulaLabels = {{
          conversion: 'Конверсия в успех',
          lost_rate: 'Доля потерь',
          open_rate: 'Доля открытых',
          delta_won_lost: 'Успешно - потеряно',
          plan_fact: 'Выполнение плана',
        }};
        const formulaRequirements = {{
          conversion: ['count', 'won_count'],
          lost_rate: ['count', 'lost_count'],
          open_rate: ['count', 'open_count'],
          delta_won_lost: ['won_count', 'lost_count'],
          plan_fact: [],
        }};
        const starterWidgets = () => [
          {{
            title: 'Общие KPI по сделкам',
            view: 'number',
            size: 'wide',
            formula: 'conversion',
            query: {{
              entity: 'leads',
              metrics: ['count', 'sum_price', 'avg_price', 'open_count', 'won_count', 'lost_count'],
              group_by: [],
              filters: [],
              filter_logic: 'and',
              order_by: 'count',
              order_dir: 'desc',
              limit: 1,
            }},
          }},
          {{
            title: 'Сделки по воронкам',
            view: 'table',
            size: 'wide',
            formula: 'none',
            query: {{
              entity: 'leads',
              metrics: ['count', 'sum_price', 'open_count', 'won_count', 'lost_count'],
              group_by: ['pipeline_id'],
              filters: [],
              filter_logic: 'and',
              order_by: 'count',
              order_dir: 'desc',
              limit: 20,
            }},
          }},
          {{
            title: 'Динамика по месяцу создания',
            view: 'line',
            size: 'wide',
            formula: 'none',
            query: {{
              entity: 'leads',
              metrics: ['count', 'sum_price', 'avg_price'],
              group_by: ['created_month'],
              filters: [],
              filter_logic: 'and',
              order_by: 'created_month',
              order_dir: 'asc',
              limit: 24,
            }},
          }},
          {{
            title: 'Заявки по рекламной площадке',
            view: 'bar',
            size: 'medium',
            formula: 'none',
            query: {{
              entity: 'leads',
              metrics: ['count', 'sum_price', 'avg_price'],
              group_by: ['cf_127785'],
              filters: [],
              filter_logic: 'and',
              order_by: 'count',
              order_dir: 'desc',
              limit: 30,
            }},
          }},
        ];
        const safeText = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({{
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        }}[char]));
        const formatDateTime = (value) => {{
          if (!value) return '';
          const date = new Date(value);
          if (Number.isNaN(date.getTime())) return String(value);
          return new Intl.DateTimeFormat('ru-RU', {{
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
          }}).format(date);
        }};
        const FRESHNESS_BADGE_MAX_AGE_MS = 60 * 60 * 1000;
        const timeAgo = (value) => {{
          if (!value) return '';
          const then = new Date(value).getTime();
          if (Number.isNaN(then)) return '';
          const min = Math.max(0, Math.round((Date.now() - then) / 60000));
          if (min < 1) return 'только что';
          if (min < 60) return `${{min}} мин назад`;
          const hours = Math.round(min / 60);
          if (hours < 24) return `${{hours}} ч назад`;
          const days = Math.round(hours / 24);
          return `${{days}} дн назад`;
        }};
        const freshnessLabel = (value) => {{
          const formatted = formatDateTime(value);
          return formatted ? `актуально на ${{formatted}}` : 'время среза не найдено';
        }};
        const formatNumber = (value, suffix = '') => {{
          const number = Number(value || 0);
          if (!Number.isFinite(number)) return '0' + suffix;
          return new Intl.NumberFormat('ru-RU', {{ maximumFractionDigits: 2 }}).format(number) + suffix;
        }};
        const isPercentColumnName = (column) => {{
          const normalized = String(column || '').trim().toLowerCase();
          return normalized.includes('%')
            || normalized.includes('процент')
            || normalized.includes('конверс')
            || normalized.includes('доля')
            || normalized === 'св'
            || normalized.startsWith('св ');
        }};
        const isPercentColumn = (column, ratioColumns = null) => {{
          // Семантический признак с бэка (meta.ratio_columns: колонки-divide).
          // Если бэк его отдал — верим только ему: AI-названия («Cv в целевые»)
          // по словам не угадываются. Эвристика по названию остаётся только
          // для старых кэшированных результатов без признака.
          if (Array.isArray(ratioColumns)) return ratioColumns.includes(column);
          return isPercentColumnName(column);
        }};
        const formatFormulaTableValue = (column, value, ratioColumns = null) => {{
          const number = Number(value || 0);
          if (isPercentColumn(column, ratioColumns)) return formatNumber(number * 100, '%');
          return formatNumber(value);
        }};
        const formulaPercentToneClass = (column, value, ratioColumns = null) => {{
          if (!isPercentColumn(column, ratioColumns)) return '';
          const number = Number(value || 0);
          if (!Number.isFinite(number) || number <= 0) return 'percent-zero';
          if (number >= 0.65) return 'percent-good';
          if (number >= 0.5) return 'percent-ok';
          if (number >= 0.3) return 'percent-warn';
          return 'percent-bad';
        }};
        const formulaCellClass = (column, value, ratioColumns = null) => {{
          if (isPercentColumn(column, ratioColumns)) {{
            return ['formula-cell-percent', formulaPercentToneClass(column, value, ratioColumns)].filter(Boolean).join(' ');
          }}
          const number = Number(value);
          return Number.isFinite(number) ? 'formula-cell-number' : '';
        }};
        const formulaRowClass = (row) => {{
          const label = String(row?.label ?? row?.key ?? '').trim().toLowerCase();
          return label.includes('итого') || label === 'total' ? 'formula-summary-row' : '';
        }};
        const tableLabelColumn = '__row_label__';
        const dimensionColumnPrefix = '__dim__:';
        const tableColumnLabel = (column, columnTitles = {{}}) => {{
          if (column === tableLabelColumn) return columnTitles[column] || 'Строка';
          if (String(column || '').startsWith(dimensionColumnPrefix)) return String(column).slice(dimensionColumnPrefix.length);
          if (!column) return 'Без сортировки';
          return columnTitles[column] || column;
        }};
        const seriesDimensionColumns = (rows) => {{
          const columns = [];
          (Array.isArray(rows) ? rows : []).forEach((row) => {{
            (Array.isArray(row.dimensions) ? row.dimensions : []).forEach((dimension) => {{
              const label = dimension.label || dimension.field || 'Разрез';
              const key = `${{dimensionColumnPrefix}}${{label}}`;
              if (!columns.some((item) => item.key === key)) columns.push({{ key, label }});
            }});
          }});
          return columns;
        }};
        const formulaTableColumns = (result, rows) => {{
          if (result?.kind === 'series') {{
            const dimensionColumns = seriesDimensionColumns(rows);
            return dimensionColumns.length > 1
              ? dimensionColumns.map((column) => column.key).concat(['Результат'])
              : ['Результат'];
          }}
          const metaColumns = Array.isArray(result?.meta?.columns) ? result.meta.columns : [];
          const rowKeys = Object.keys(rows[0] || {{}}).filter((key) => !['key', 'label'].includes(key));
          return metaColumns.length ? metaColumns : rowKeys;
        }};
        const normalizeTableSettings = (settings = {{}}, columns = []) => {{
          const sortColumns = [tableLabelColumn].concat(columns);
          const sortBy = sortColumns.includes(settings.sort_by) ? settings.sort_by : '';
          const zeroColumn = columns.includes(settings.zero_column) ? settings.zero_column : '';
          const limit = Math.max(0, Math.min(Number(settings.row_limit || 0), 500));
          const visibleColumns = Array.isArray(settings.visible_columns)
            ? settings.visible_columns.filter((column) => columns.includes(column))
            : [];
          const hiddenColumns = Array.isArray(settings.hidden_columns)
            ? settings.hidden_columns.filter((column) => columns.includes(column))
            : [];
          // Колонка группировки (tableLabelColumn) настраивается наравне с
          // данными: имя и ширина — да, видимость/порядок — нет.
          const titleableColumns = [tableLabelColumn].concat(columns);
          const rawTitles = settings.column_titles && typeof settings.column_titles === 'object' ? settings.column_titles : {{}};
          const columnTitles = {{}};
          titleableColumns.forEach((column) => {{
            const title = String(rawTitles[column] ?? '').trim();
            if (title) columnTitles[column] = title;
          }});
          const rawWidths = settings.column_widths && typeof settings.column_widths === 'object' ? settings.column_widths : {{}};
          const columnWidths = {{}};
          titleableColumns.forEach((column) => {{
            const width = Number(rawWidths[column]);
            if (Number.isFinite(width) && width > 0) columnWidths[column] = Math.max(60, Math.min(600, Math.round(width)));
          }});
          return {{
            sort_by: sortBy,
            sort_dir: settings.sort_dir === 'asc' ? 'asc' : 'desc',
            hide_zero_rows: Boolean(settings.hide_zero_rows),
            zero_column: zeroColumn,
            row_limit: Number.isFinite(limit) ? limit : 0,
            visible_columns: visibleColumns,
            hidden_columns: hiddenColumns,
            column_titles: columnTitles,
            column_widths: columnWidths,
            header_font_size: ['small', 'normal', 'large'].includes(settings.header_font_size) ? settings.header_font_size : 'normal',
          }};
        }};
        const rowColumnValue = (row, column) => {{
          if (column === tableLabelColumn) return row?.label ?? row?.key ?? '';
          if (String(column || '').startsWith(dimensionColumnPrefix)) {{
            const label = String(column).slice(dimensionColumnPrefix.length);
            const dimension = (Array.isArray(row?.dimensions) ? row.dimensions : []).find((item) => (item.label || item.field) === label);
            return dimension?.value ?? dimension?.key ?? '';
          }}
          if (column === 'Результат') return formulaRowValue(row);
          return row?.[column];
        }};
        const tableComparableValue = (value) => {{
          const number = Number(value);
          if (Number.isFinite(number)) return number;
          return String(value ?? '').toLowerCase();
        }};
        const isZeroTableRow = (row, columns, settings) => {{
          const targetColumns = settings.zero_column ? [settings.zero_column] : columns;
          const values = targetColumns.map((column) => Number(rowColumnValue(row, column) || 0));
          return values.length > 0 && values.every((value) => Number.isFinite(value) && value === 0);
        }};
        const applyFormulaTableSettings = (result, rows, columns, settings = {{}}) => {{
          const normalized = normalizeTableSettings(settings, columns);
          // Порядок показа = visible_columns; колонки формулы, которых нет ни в
          // видимых, ни в скрытых (появились позже настройки), дописываются в
          // хвост, а не пропадают молча. hidden_columns скрывает явно.
          let displayColumns = normalized.visible_columns.length
            ? normalized.visible_columns.concat(columns.filter(
                (column) => !normalized.visible_columns.includes(column) && !normalized.hidden_columns.includes(column)))
            : columns.filter((column) => !normalized.hidden_columns.includes(column));
          if (!displayColumns.length) displayColumns = columns;
          let nextRows = [...rows];
          if (normalized.hide_zero_rows) {{
            nextRows = nextRows.filter((row) => !isZeroTableRow(row, displayColumns, normalized));
          }}
          if (normalized.sort_by) {{
            const direction = normalized.sort_dir === 'asc' ? 1 : -1;
            nextRows.sort((a, b) => {{
              const left = tableComparableValue(rowColumnValue(a, normalized.sort_by));
              const right = tableComparableValue(rowColumnValue(b, normalized.sort_by));
              if (left < right) return -1 * direction;
              if (left > right) return 1 * direction;
              return 0;
            }});
          }}
          if (normalized.row_limit > 0) nextRows = nextRows.slice(0, normalized.row_limit);
          return {{ rows: nextRows, columns: displayColumns, settings: normalized }};
        }};
        const sourceDisplayName = (source, fallbackId = '') => {{
          const rawName = String(source?.name || '');
          const pipelineName = (source?.pipeline_names || []).filter(Boolean).join(', ');
          if (/^Источник #\\d{{4}}-\\d{{2}}-\\d{{2}}T/.test(rawName)) {{
            return pipelineName || `Источник ${{fallbackId}}`;
          }}
          return rawName || `Источник ${{fallbackId}}`;
        }};
        const sourcePipelineLabel = (source) => {{
          const names = (source?.pipeline_names || []).filter(Boolean);
          return names.length ? names.join(', ') : 'не указана';
        }};
        const sourceStagesLabel = (source) => {{
          const selected = Number(source?.status_count || 0);
          const total = Number(source?.pipeline_status_total || 0);
          if (!selected) return 'Все этапы';
          if (total && selected >= total) return `Все этапы (${{selected}})`;
          return `${{selected}} этапов`;
        }};
        const sourceIsStale = (source) => {{
          const hub = source?.hub_fresh_at ? new Date(source.hub_fresh_at).getTime() : null;
          if (hub === null || Number.isNaN(hub)) return true;
          return (Date.now() - hub) > FRESHNESS_BADGE_MAX_AGE_MS;
        }};
        const sourceActualityLabel = (source) => {{
          const hub = source?.hub_fresh_at ? new Date(source.hub_fresh_at).getTime() : null;
          if (hub === null || Number.isNaN(hub)) return 'нет данных';
          if ((Date.now() - hub) > FRESHNESS_BADGE_MAX_AGE_MS) {{
            return `данные не обновлялись ${{timeAgo(source.hub_fresh_at)}} — проверь синхронизацию`;
          }}
          return `актуально · обновлено ${{timeAgo(source.hub_fresh_at)}}`;
        }};
        const sourceSubtitle = (sourceId) => {{
          const id = Number(sourceId || 0);
          if (!id) return 'Источник: весь хаб';
          const source = syncSourcesIndex[id];
          return source ? `Источник: ${{sourceDisplayName(source, id)}} · ${{source.count}} сделок · ${{freshnessLabel(source.fresh_at)}} · ${{sourceActualityLabel(source)}}` : `Источник #${{id}}`;
        }};
        const sourceInfoHtml = (sourceId) => {{
          const id = Number(sourceId || 0);
          if (!id) {{
            return `
              <div class="source-note-head">
                <div class="source-note-title">Весь хаб</div>
                <div class="source-status-pill">живой</div>
              </div>
              <div class="source-note-grid">
                <div class="source-note-item"><span>Источник</span><strong>Все данные хаба</strong></div>
                <div class="source-note-item"><span>Обновление</span><strong>Через webhook и очередь</strong></div>
              </div>
            `;
          }}
          const source = syncSourcesIndex[id];
          if (!source) return `<div class="source-note-title">Источник #${{id}} не найден</div>`;
          const stale = sourceIsStale(source);
          return `
            <div class="source-note-head">
              <div class="source-note-title">${{safeText(sourceDisplayName(source, id))}}</div>
              <div class="source-status-pill${{stale ? ' stale' : ''}}">${{stale ? 'устарел' : 'актуален'}}</div>
            </div>
            <div class="source-note-grid">
              <div class="source-note-item"><span>Воронка</span><strong>${{safeText(sourcePipelineLabel(source))}}</strong></div>
              <div class="source-note-item"><span>Этапы</span><strong>${{safeText(sourceStagesLabel(source))}}</strong></div>
              <div class="source-note-item"><span>Сделок</span><strong>${{formatNumber(source.count)}}</strong></div>
              <div class="source-note-item"><span>Данные на</span><strong>${{safeText((formatDateTime(source.hub_fresh_at) || 'неизвестно') + (timeAgo(source.hub_fresh_at) ? ' · ' + timeAgo(source.hub_fresh_at) : ''))}}</strong></div>
              <div class="source-note-item"><span>Ручная проверка</span><strong>${{safeText(formatDateTime(source.checked_at) || 'еще нет')}}</strong></div>
              <div class="source-note-item"><span>Автообновление</span><strong>Включено</strong></div>
            </div>
          `;
        }};
        const workSourceCardHtml = (sourceId) => {{
          const source = syncSourcesIndex[sourceId];
          if (!source) return '';
          const stale = sourceIsStale(source);
          return `
            <article class="work-source-card" data-work-source-id="${{sourceId}}">
              <header>
                <strong>${{safeText(sourceDisplayName(source, sourceId))}}</strong>
                <span class="source-status-pill${{stale ? ' stale' : ''}}">${{stale ? 'устарел' : 'актуален'}}</span>
              </header>
              <div class="work-source-meta">
                <span>Воронка<b>${{safeText(sourcePipelineLabel(source))}}</b></span>
                <span>Этапы<b>${{safeText(sourceStagesLabel(source))}}</b></span>
                <span>Сделок<b>${{formatNumber(source.count)}}</b></span>
                <span>Ручная проверка<b>${{safeText(formatDateTime(source.checked_at) || 'еще нет')}}</b></span>
              </div>
              <div class="work-source-actions">
                <button type="button" data-work-source-select="${{sourceId}}">Выбрать</button>
                <button type="button" class="ghost" data-work-source-remove="${{sourceId}}">Убрать из работы</button>
              </div>
            </article>
          `;
        }};
        const renderWorkSources = () => {{
          if (!workSourcesEl) return;
          const ids = [...workSourceIds].filter((id) => syncSourcesIndex[id]);
          if (!ids.length) {{
            workSourcesEl.innerHTML = '<div class="work-source-empty">Пока нет зафиксированных источников. Выбери источник выше и нажми “Добавить в работу”.</div>';
            return;
          }}
          workSourcesEl.innerHTML = ids.map(workSourceCardHtml).join('');
        }};
        const saveWorkSources = async () => {{
          const response = await fetch(apiUrl('/api/work-sources'), {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ source_ids: [...workSourceIds] }}),
          }});
          const data = await response.json();
          if (!response.ok || !data.ok) throw new Error(data.error || 'Не удалось сохранить источники');
          return data.source_ids || [];
        }};
        const updateReportSourceNote = () => {{
          if (!reportSourceNoteEl) return;
          reportSourceNoteEl.innerHTML = sourceInfoHtml(reportSourceEl?.value || 0);
        }};
        const openCreateSourceModal = () => {{
          if (!createSourceModalEl) return;
          createSourceModalEl.hidden = false;
          window.setTimeout(() => createSourceNameEl?.focus(), 40);
        }};
        const closeCreateSourceModal = () => {{
          if (!createSourceModalEl) return;
          createSourceModalEl.hidden = true;
        }};
        sourceCreateOpenBtn?.addEventListener('click', openCreateSourceModal);
        createSourceCloseBtns.forEach((button) => button.addEventListener('click', closeCreateSourceModal));
        createSourceModalEl?.addEventListener('click', (event) => {{
          if (event.target === createSourceModalEl) closeCreateSourceModal();
        }});
        document.addEventListener('keydown', (event) => {{
          if (event.key === 'Escape' && createSourceModalEl && !createSourceModalEl.hidden) {{
            closeCreateSourceModal();
          }}
        }});
        reportSourceEl?.addEventListener('change', updateReportSourceNote);
        updateReportSourceNote();
        renderWorkSources();
        sourceWorkAddBtn?.addEventListener('click', async () => {{
          const sourceId = Number(reportSourceEl?.value || 0);
          if (!sourceId) {{
            reportStatusEl.textContent = 'Выбери конкретный источник, чтобы добавить его в работу.';
            reportStatusEl.classList.add('error');
            return;
          }}
          workSourceIds.add(sourceId);
          renderWorkSources();
          reportStatusEl.classList.remove('error');
          reportStatusEl.textContent = 'Сохраняю источник в рабочем наборе...';
          try {{
            await saveWorkSources();
            reportStatusEl.textContent = `${{sourceDisplayName(syncSourcesIndex[sourceId], sourceId)}} добавлен в работу`;
          }} catch (error) {{
            reportStatusEl.textContent = 'Ошибка сохранения источника: ' + error.message;
            reportStatusEl.classList.add('error');
          }}
        }});
        workSourcesEl?.addEventListener('click', async (event) => {{
          const selectBtn = event.target.closest('[data-work-source-select]');
          const removeBtn = event.target.closest('[data-work-source-remove]');
          if (selectBtn) {{
            const sourceId = Number(selectBtn.dataset.workSourceSelect || 0);
            if (reportSourceEl && sourceId) {{
              reportSourceEl.value = String(sourceId);
              updateReportSourceNote();
              reportStatusEl.textContent = `${{sourceDisplayName(syncSourcesIndex[sourceId], sourceId)}} выбран для проверки`;
            }}
            return;
          }}
          if (removeBtn) {{
            const sourceId = Number(removeBtn.dataset.workSourceRemove || 0);
            if (!sourceId) return;
            workSourceIds.delete(sourceId);
            renderWorkSources();
            reportStatusEl.classList.remove('error');
            reportStatusEl.textContent = 'Сохраняю рабочий набор...';
            try {{
              await saveWorkSources();
              reportStatusEl.textContent = 'Источник убран из рабочего набора';
            }} catch (error) {{
              reportStatusEl.textContent = 'Ошибка сохранения источников: ' + error.message;
              reportStatusEl.classList.add('error');
            }}
          }}
        }});
        const selectedCreateSource = () => {{
          const pipelineIds = createSourcePipelineChecks
            .filter((input) => input.checked)
            .map((input) => Number(input.value))
            .filter(Boolean);
          const statusIds = createSourceStatusChecks
            .filter((input) => input.checked)
            .map((input) => Number(input.value))
            .filter(Boolean);
          return {{ pipelineIds, statusIds }};
        }};
        const updateCreateSourceSummary = () => {{
          if (!createSourceSummaryEl) return;
          const {{ pipelineIds, statusIds }} = selectedCreateSource();
          if (!pipelineIds.length) {{
            createSourceSummaryEl.textContent = 'Выбери одну или несколько воронок. Этапы внутри выбранной воронки отметятся автоматически.';
            return;
          }}
          createSourceSummaryEl.textContent = `Выбрано: воронок ${{pipelineIds.length}}, этапов ${{statusIds.length}}. После создания источник начнет выгрузку из amoCRM.`;
        }};
        createSourcePipelineChecks.forEach((pipelineInput) => {{
          pipelineInput.addEventListener('change', () => {{
            const pipelineId = String(pipelineInput.value);
            createSourceStatusChecks
              .filter((statusInput) => String(statusInput.dataset.pipelineId) === pipelineId)
              .forEach((statusInput) => {{
                statusInput.checked = pipelineInput.checked;
              }});
            updateCreateSourceSummary();
          }});
        }});
        createSourceStatusChecks.forEach((statusInput) => {{
          statusInput.addEventListener('change', () => {{
            const pipelineId = String(statusInput.dataset.pipelineId || '');
            const statusInputs = createSourceStatusChecks.filter((item) => String(item.dataset.pipelineId) === pipelineId);
            const pipelineInput = createSourcePipelineChecks.find((item) => String(item.value) === pipelineId);
            if (pipelineInput) {{
              pipelineInput.checked = statusInputs.some((item) => item.checked);
              pipelineInput.indeterminate = pipelineInput.checked && !statusInputs.every((item) => item.checked);
            }}
            updateCreateSourceSummary();
          }});
        }});
        createSourceSearchEl?.addEventListener('input', () => {{
          const query = String(createSourceSearchEl.value || '').trim().toLowerCase();
          createSourceBlocks.forEach((block) => {{
            block.hidden = Boolean(query) && !String(block.dataset.createSourceText || '').includes(query);
          }});
        }});
        createSourceClearBtn?.addEventListener('click', () => {{
          createSourcePipelineChecks.forEach((input) => {{
            input.checked = false;
            input.indeterminate = false;
          }});
          createSourceStatusChecks.forEach((input) => {{
            input.checked = false;
          }});
          if (createSourceNameEl) createSourceNameEl.value = '';
          updateCreateSourceSummary();
        }});
        const pollCreatedSource = async (statusUrl, sourceId) => {{
          const response = await fetch(statusUrl);
          const data = await response.json();
          if (!response.ok || !data.ok) throw new Error(data.error || 'Не удалось получить статус выгрузки');
          const job = data.job || {{}};
          const status = job.status || 'pending';
          const done = Number(job.items_count || 0);
          const failed = Number(job.failed_count || 0);
          if (createSourceStatusEl) {{
            createSourceStatusEl.textContent = `Выгружаю источник: ${{status}} · обработано ${{formatNumber(done)}} · ошибок ${{formatNumber(failed)}}`;
          }}
          if (status === 'pending' || status === 'running') {{
            window.setTimeout(() => pollCreatedSource(statusUrl, sourceId).catch((error) => {{
              if (createSourceStatusEl) {{
                createSourceStatusEl.textContent = 'Ошибка выгрузки источника: ' + error.message;
                createSourceStatusEl.classList.add('error');
              }}
              if (createSourceRunBtn) createSourceRunBtn.disabled = false;
            }}), 2500);
            return;
          }}
          if (status !== 'success' && status !== 'done') {{
            throw new Error(job.error || 'Выгрузка источника завершилась с ошибкой');
          }}
          if (createSourceStatusEl) createSourceStatusEl.textContent = 'Источник создан. Обновляю страницу...';
          const nextUrl = apiUrl('/settings', sourceId ? {{ source_id: sourceId }} : {{}});
          window.location.href = nextUrl;
        }};
        createSourceRunBtn?.addEventListener('click', async () => {{
          const {{ pipelineIds, statusIds }} = selectedCreateSource();
          const sourceName = String(createSourceNameEl?.value || '').trim();
          if (!sourceName) {{
            if (createSourceStatusEl) {{
              createSourceStatusEl.textContent = 'Назови источник, чтобы потом было понятно, что это за массив.';
              createSourceStatusEl.classList.add('error');
            }}
            return;
          }}
          if (!pipelineIds.length && !statusIds.length) {{
            if (createSourceStatusEl) {{
              createSourceStatusEl.textContent = 'Выбери хотя бы одну воронку или этап.';
              createSourceStatusEl.classList.add('error');
            }}
            return;
          }}
          createSourceRunBtn.disabled = true;
          if (createSourceStatusEl) {{
            createSourceStatusEl.classList.remove('error');
            createSourceStatusEl.textContent = 'Создаю источник и запускаю выгрузку...';
          }}
          try {{
            const response = await fetch(apiUrl('/api/sync/resync'), {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{
                source_name: sourceName,
                pipeline_ids: pipelineIds,
                status_ids: statusIds,
                entities: ['pipelines', 'users', 'lead_custom_fields', 'contact_custom_fields', 'company_custom_fields', 'leads'],
              }}),
            }});
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || 'Не удалось создать источник');
            await pollCreatedSource(data.status_url, data.source_id);
          }} catch (error) {{
            if (createSourceStatusEl) {{
              createSourceStatusEl.textContent = 'Ошибка создания источника: ' + error.message;
              createSourceStatusEl.classList.add('error');
            }}
            createSourceRunBtn.disabled = false;
          }}
        }});
        updateCreateSourceSummary();
        const pollSourceRefresh = async (statusUrl) => {{
          const response = await fetch(statusUrl);
          const data = await response.json();
          if (!response.ok || !data.ok) throw new Error(data.error || 'Не удалось получить статус обновления');
          const job = data.job || {{}};
          const status = job.status || 'pending';
          const done = Number(job.items_count || 0);
          const failed = Number(job.failed_count || 0);
          reportStatusEl.textContent = `Обновляю источник: ${{status}} · обработано ${{formatNumber(done)}} · ошибок ${{formatNumber(failed)}}`;
          if (status === 'pending' || status === 'running') {{
            window.setTimeout(() => pollSourceRefresh(statusUrl).catch((error) => {{
              reportStatusEl.textContent = 'Ошибка обновления источника: ' + error.message;
              reportStatusEl.classList.add('error');
              if (sourceRefreshBtn) sourceRefreshBtn.disabled = false;
            }}), 2500);
            return;
          }}
          if (status !== 'success' && status !== 'done') {{
            throw new Error(job.error || 'Обновление источника завершилось с ошибкой');
          }}
          reportStatusEl.textContent = 'Источник обновлен. Проверяю массив...';
          window.setTimeout(() => reportBtn?.click(), 300);
          if (sourceRefreshBtn) sourceRefreshBtn.disabled = false;
        }};
        sourceRefreshBtn?.addEventListener('click', async () => {{
          const sourceId = Number(reportSourceEl?.value || 0);
          if (!sourceId) {{
            reportStatusEl.textContent = 'Для всего хаба нужна полная перевыгрузка. Выбери именованный источник.';
            reportStatusEl.classList.add('error');
            return;
          }}
          sourceRefreshBtn.disabled = true;
          reportStatusEl.classList.remove('error');
          reportStatusEl.textContent = 'Запускаю обновление источника...';
          try {{
            const response = await fetch(apiUrl(`/api/sync-sources/${{sourceId}}/resync`), {{ method: 'POST' }});
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || 'Не удалось запустить обновление');
            await pollSourceRefresh(data.status_url);
          }} catch (error) {{
            reportStatusEl.textContent = 'Ошибка обновления источника: ' + error.message;
            reportStatusEl.classList.add('error');
            sourceRefreshBtn.disabled = false;
          }}
        }});
        const visibleColumns = (columns) => columns.filter((column) => {{
          if (column === 'pipeline_id' && columns.includes('pipeline_name')) return false;
          if (column === 'status_id' && columns.includes('status_name')) return false;
          return true;
        }});
        const formatCell = (column, value) => {{
          if (value === null || value === undefined || value === '') return '—';
          if (column === 'sum_price' || column === 'avg_price') return formatNumber(value, ' ₽');
          if (['count', 'open_count', 'won_count', 'lost_count'].includes(column)) return formatNumber(value);
          return value;
        }};
        const renderTableHtml = (rows) => {{
          if (!rows.length) return '<div class="report-empty">Нет данных под выбранные условия</div>';
          const columns = visibleColumns(Object.keys(rows[0]));
          return `
            <div class="report-table-wrap">
              <table>
                <thead><tr>${{columns.map((column) => `<th>${{safeText(columnLabels[column] || column)}}</th>`).join('')}}</tr></thead>
                <tbody>
                  ${{rows.map((row) => `<tr>${{columns.map((column) => `<td>${{safeText(formatCell(column, row[column]))}}</td>`).join('')}}</tr>`).join('')}}
                </tbody>
              </table>
            </div>
          `;
        }};
        const widgetSettingsPayload = () => {{
          const now = new Date();
          const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
          return {{
            plan: Number(widgetPlanEl?.value || 0),
            period_days: Number(widgetPeriodDaysEl?.value || daysInMonth),
            days_passed: Number(widgetDaysPassedEl?.value || now.getDate()),
          }};
        }};
        const calculateFormula = (formula, row, settings = {{}}, metric = 'count') => {{
          const count = Number(row.count || 0);
          const won = Number(row.won_count || 0);
          const lost = Number(row.lost_count || 0);
          const open = Number(row.open_count || 0);
          if (formula === 'conversion') return {{ label: formulaLabels[formula], value: count ? won / count * 100 : 0, suffix: '%' }};
          if (formula === 'lost_rate') return {{ label: formulaLabels[formula], value: count ? lost / count * 100 : 0, suffix: '%' }};
          if (formula === 'open_rate') return {{ label: formulaLabels[formula], value: count ? open / count * 100 : 0, suffix: '%' }};
          if (formula === 'delta_won_lost') return {{ label: formulaLabels[formula], value: won - lost, suffix: '' }};
          if (formula === 'plan_fact') {{
            const plan = Number(settings.plan || 0);
            const fact = Number(row[metric] || 0);
            const periodDays = Math.max(Number(settings.period_days || 0), 1);
            const daysPassed = Math.min(Math.max(Number(settings.days_passed || 0), 1), periodDays);
            const forecast = fact / daysPassed * periodDays;
            return {{
              label: 'Выполнение',
              value: plan ? fact / plan * 100 : 0,
              suffix: '%',
              extra: [
                {{ label: 'План', value: plan, suffix: '' }},
                {{ label: 'Факт', value: fact, suffix: '' }},
                {{ label: 'Прогноз', value: forecast, suffix: '' }},
              ],
            }};
          }}
          return null;
        }};
        const renderMetricCard = (label, value, suffix = '', extraHtml = '') => `
          <article class="number-card">
            <span>${{safeText(label)}}</span>
            <strong>${{formatNumber(value, suffix)}}</strong>
            ${{extraHtml}}
          </article>
        `;
        const labelForRow = (row) => {{
          if (row?.label !== undefined && row.label !== null && row.label !== '') return row.label;
          const preferred = ['pipeline_name', 'status_name', 'created_month', 'updated_month', 'closed_month', 'cf_month_127845', 'cf_127785'];
          const key = preferred.find((item) => row[item] !== undefined && row[item] !== null && row[item] !== '');
          if (key) return formatCell(key, row[key]);
          const fallback = Object.keys(row).find((item) => !Object.keys(metricLabels).includes(item));
          return fallback ? formatCell(fallback, row[fallback]) : 'Без названия';
        }};
        const primaryMetric = (widgetOrBuilt) => (widgetOrBuilt.query?.metrics || widgetOrBuilt.metrics || ['count'])[0] || 'count';
        const metricValue = (row, metric) => Number(row?.[metric] || 0);
        const renderBarChart = (rows, metric = 'count') => {{
          if (!rows.length) return '<div class="report-empty">Нет данных</div>';
          const max = Math.max(...rows.map((row) => metricValue(row, metric)), 1);
          return `
            <div class="visual-chart">
              <div class="chart-legend"><span>${{safeText(metricLabels[metric] || metric)}}</span><span>${{formatNumber(max)}}</span></div>
              <div class="bar-list">
                ${{rows.slice(0, 12).map((row) => {{
                  const value = metricValue(row, metric);
                  const width = Math.max(4, Math.round(value / max * 100));
                  return `
                    <div class="bar-row">
                      <div class="bar-label" title="${{safeText(labelForRow(row))}}">${{safeText(labelForRow(row))}}</div>
                      <div class="bar-track"><span class="bar-fill" style="width: ${{width}}%"></span></div>
                      <div class="bar-value">${{formatCell(metric, value)}}</div>
                    </div>
                  `;
                }}).join('')}}
              </div>
            </div>
          `;
        }};
        const renderTopList = (rows, metric = 'count') => {{
          if (!rows.length) return '<div class="report-empty">Нет данных</div>';
          return `
            <div class="top-list">
              ${{rows.slice(0, 10).map((row, index) => `
                <div class="top-row">
                  <div class="top-label" title="${{safeText(labelForRow(row))}}">${{index + 1}}. ${{safeText(labelForRow(row))}}</div>
                  <div class="top-value">${{safeText(formatCell(metric, row[metric]))}}</div>
                </div>
              `).join('')}}
            </div>
          `;
        }};
        const renderLineChart = (rows, metric = 'count') => {{
          if (!rows.length) return '<div class="report-empty">Нет данных</div>';
          const items = rows.slice(0, 24);
          const values = items.map((row) => metricValue(row, metric));
          const max = Math.max(...values, 1);
          const minValue = Math.min(...values);
          const width = 900;
          const height = 280;
          const pad = 26;
          const padBottom = 46;
          const chartBottom = height - padBottom;
          const step = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0;
          const pointAt = (value, index) => [pad + index * step, chartBottom - (value / max) * (chartBottom - pad)];
          const points = values.map((value, index) => pointAt(value, index).join(',')).join(' ');
          const area = `${{pad}},${{chartBottom}} ${{points}} ${{pad + Math.max(values.length - 1, 0) * step}},${{chartBottom}}`;
          const gridLines = [0.25, 0.5, 0.75, 1].map((ratio) => {{
            const y = chartBottom - ratio * (chartBottom - pad);
            return `<line x1="${{pad}}" y1="${{y}}" x2="${{width - pad}}" y2="${{y}}" stroke="#e6eef8" stroke-width="1"></line>`;
          }}).join('');
          // Подписи X (ключи группировки — месяцы) с прореживанием до ~6 меток;
          // последняя точка подписывается всегда.
          const labelStep = Math.max(1, Math.ceil(items.length / 6));
          const xLabels = items.map((row, index) => {{
            if (index % labelStep !== 0 && index !== items.length - 1) return '';
            const [x] = pointAt(values[index], index);
            const text = String(labelForRow(row) || '').slice(0, 12);
            return `<text x="${{x}}" y="${{height - 12}}" text-anchor="middle" font-size="12" fill="#8a9bb3">${{safeText(text)}}</text>`;
          }}).join('');
          // Значения точек: все, когда точек немного; иначе только min и max.
          const valueLabels = values.map((value, index) => {{
            if (values.length > 12 && value !== max && value !== minValue) return '';
            const [x, y] = pointAt(value, index);
            return `<text x="${{x}}" y="${{y - 10}}" text-anchor="middle" font-size="12" font-weight="700" fill="#12355b">${{safeText(formatNumber(value))}}</text>`;
          }}).join('');
          return `
            <div class="visual-chart">
              <div class="chart-legend"><span>${{safeText(metricLabels[metric] || metric)}}</span><span>${{formatNumber(max)}}</span></div>
              <svg class="line-chart" viewBox="0 0 ${{width}} ${{height}}" role="img" aria-label="График">
                ${{gridLines}}
                <polyline points="${{area}}" fill="rgba(37, 99, 235, .10)" stroke="none"></polyline>
                <polyline points="${{points}}" fill="none" stroke="#2563eb" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"></polyline>
                ${{points.split(' ').map((point) => {{
                  const [x, y] = point.split(',');
                  return `<circle cx="${{x}}" cy="${{y}}" r="5" fill="#2563eb" stroke="#fff" stroke-width="3"></circle>`;
                }}).join('')}}
                ${{valueLabels}}
                ${{xLabels}}
              </svg>
            </div>
          `;
        }};
        const renderVisualHtml = (view, rows, metric = 'count') => {{
          if (view === 'bar') return renderBarChart(rows, metric);
          if (view === 'line') return renderLineChart(rows, metric);
          if (view === 'list') return renderTopList(rows, metric);
          return renderTableHtml(rows);
        }};
        const formulaRowValue = (row) => row?.value ?? row?.result ?? row?.['Результат'] ?? 0;
        const formulaNumber = (value) => {{
          const number = Number(value);
          return Number.isFinite(number) ? number : 0;
        }};
        const formulaPercent = (value) => {{
          if (!Number.isFinite(value)) return '0%';
          const rounded = Math.round(value * 10) / 10;
          return `${{String(rounded).replace('.', ',')}}%`;
        }};
        const formulaResultSummary = (result) => {{
          if (!result) return 'Результат еще не рассчитан.';
          if (result.kind === 'scalar') {{
            return `Итог получился одним числом: ${{formatNumber(result.value ?? 0)}}.`;
          }}
          const rows = Array.isArray(result.rows) ? result.rows : [];
          if (result.kind === 'series') {{
            const total = rows.reduce((sum, row) => sum + formulaNumber(formulaRowValue(row)), 0);
            return `Итог получился таблицей по группировке: ${{rows.length}} строк, сумма строк ${{formatNumber(total)}}.`;
          }}
          if (result.kind === 'table') {{
            return `Итог получился сводной таблицей: ${{rows.length}} строк.`;
          }}
          return 'Итог рассчитан, формат результата нестандартный.';
        }};
        const renderFormulaExplanationHtml = (result, diagnostics) => {{
          const items = Array.isArray(diagnostics?.items) ? diagnostics.items : [];
          if (!items.length) return '';
          const amoFilter = diagnostics?.amo_filter || null;
          const amoUnmapped = Array.isArray(amoFilter?.unmapped) ? amoFilter.unmapped : [];
          let amoLinkHtml = '';
          if (amoFilter?.url && !amoUnmapped.length) {{
            amoLinkHtml = `<div class="formula-explanation-amo" style="margin: 4px 0 8px;"><a href="${{safeText(amoFilter.url)}}" target="_blank" rel="noopener">Открыть в amoCRM</a></div>`;
          }} else if (amoUnmapped.length) {{
            amoLinkHtml = `<div class="formula-explanation-amo" style="margin: 4px 0 8px; color: #8a94a6; font-size: 12px;">Фильтр не переводится в amoCRM полностью, ссылка скрыта.</div>`;
          }}
          const sections = items.map((item, itemIndex) => {{
            const stages = Array.isArray(item.stages) ? item.stages : [];
            if (!stages.length) return '';
            const firstValue = formulaNumber(stages[0]?.value);
            const finalValue = formulaNumber(stages[stages.length - 1]?.value);
            const steps = stages.map((stage, index) => {{
              const value = formulaNumber(stage.value);
              const previous = index > 0 ? formulaNumber(stages[index - 1]?.value) : null;
              const delta = previous === null ? 0 : previous - value;
              const note = index === 0
                ? 'Стартовый массив до условий.'
                : `После этого условия осталось ${{previous ? formulaPercent((value / previous) * 100) : '0%'}} от предыдущего шага, отсеяно ${{formatNumber(Math.max(delta, 0))}}.`;
              return `
                <div class="formula-explanation-step">
                  <b>${{index + 1}}</b>
                  <span>${{safeText(stage.label || 'Шаг расчета')}}<br>${{safeText(note)}}</span>
                  <strong>${{safeText(formatNumber(stage.value ?? 0))}}</strong>
                </div>
              `;
            }}).join('');
            return `
              <details class="formula-explanation" ${{itemIndex === 0 ? 'open' : ''}}>
                <summary>
                  <span>
                    <b>Расшифровка${{item.title ? ': ' + safeText(item.title) : ''}}</b>
                    <small>${{safeText(stages.length)}} шагов · старт ${{safeText(formatNumber(firstValue))}} · итог ${{safeText(formatNumber(finalValue))}}</small>
                  </span>
                  <strong>${{safeText(formatNumber(finalValue))}}</strong>
                </summary>
                <div class="formula-explanation-steps">${{steps}}</div>
              </details>
            `;
          }}).join('');
          return `<div class="formula-explanation-list">${{amoLinkHtml}}${{sections}}</div>`;
        }};
        const renderFormulaDiagnosticsHtml = (diagnostics) => {{
          const items = Array.isArray(diagnostics?.items) ? diagnostics.items : [];
          if (!items.length) return '';
          const sections = items.map((item) => {{
            const stages = Array.isArray(item.stages) ? item.stages : [];
            if (!stages.length) return '';
            const finalValue = formulaNumber(stages[stages.length - 1]?.value);
            return `
              <details class="formula-diagnostics">
                <summary>
                  <span>
                    <b>Проверка условий${{item.title ? ': ' + safeText(item.title) : ''}}</b>
                    <small>Каждый шаг показывает, сколько строк осталось после условия.</small>
                  </span>
                  <strong>${{safeText(formatNumber(finalValue))}}</strong>
                </summary>
                <div class="diagnostic-rows">
                  ${{stages.map((stage) => `
                    <div class="diagnostic-row">
                      <span>${{safeText(stage.label || '')}}</span>
                      <strong>${{safeText(formatNumber(stage.value ?? 0))}}</strong>
                    </div>
                  `).join('')}}
                </div>
              </details>
            `;
          }}).join('');
          return `<div class="formula-diagnostics-list">${{sections}}</div>`;
        }};
        const drilldownHref = (widgetId, rowKey, column) => {{
          if (!widgetId) return '';
          return apiUrl('/drilldown', {{
            widget_id: widgetId,
            row_key: rowKey || '',
            column: column || '',
          }});
        }};
        const renderDrilldownCell = (widgetId, row, column, formattedValue, className = '') => {{
          const drilldown = row?._drilldown?.[column];
          const ids = drilldown?.entity_ids;
          const canOpen = widgetId && Array.isArray(ids) && ids.length > 0;
          if (!canOpen) return `<td class="${{safeText(className)}}" title="${{safeText(formattedValue)}}">${{safeText(formattedValue)}}</td>`;
          const title = drilldown.truncated
            ? `Открыть первые ${{ids.length}} сделок из ${{drilldown.total || ids.length}}`
            : `Открыть ${{drilldown.total || ids.length}} сделок`;
          return `
            <td class="${{safeText(className)}} drilldown-cell">
              <a class="drilldown-link" href="${{drilldownHref(widgetId, row.key || row.label || '', column)}}" title="${{safeText(title)}}">${{safeText(formattedValue)}}</a>
            </td>
          `;
        }};
        const renderSeriesDrilldownCell = (widgetId, row, formattedValue, className = '') => {{
          const ids = row?.entity_ids;
          const canOpen = widgetId && Array.isArray(ids) && ids.length > 0;
          if (!canOpen) return `<td class="${{safeText(className)}}" title="${{safeText(formattedValue)}}">${{safeText(formattedValue)}}</td>`;
          const title = row.trace_truncated
            ? `Открыть первые ${{ids.length}} сделок из ${{row.trace_total || ids.length}}`
            : `Открыть ${{row.trace_total || ids.length}} сделок`;
          return `
            <td class="${{safeText(className)}} drilldown-cell">
              <a class="drilldown-link" href="${{drilldownHref(widgetId, row.key || row.label || '', 'Результат')}}" title="${{safeText(title)}}">${{safeText(formattedValue)}}</a>
            </td>
          `;
        }};
        const formulaComparesPeriods = (formulaSpec) => {{
          // Тренд под числом показываем только для сравнения периодов: в формуле
          // должен быть фильтр по прошлому периоду (previous_month/previous_week).
          if (!formulaSpec) return false;
          try {{
            return /"(previous_month|previous_week)"/.test(JSON.stringify(formulaSpec));
          }} catch (error) {{
            return false;
          }}
        }};
        const renderScalarTrendHtml = (result, formulaSpec = null) => {{
          const meta = result?.meta || {{}};
          const op = String(meta.op || '');
          if (op !== 'subtract' && op !== 'divide') return '';
          const left = Number(meta.left);
          const right = Number(meta.right);
          if (!Number.isFinite(left) || !Number.isFinite(right)) return '';
          if (!formulaComparesPeriods(formulaSpec)) return '';
          let delta = 0;
          let suffix = '';
          if (op === 'subtract') {{
            delta = left - right;
          }} else {{
            if (!right) return '';
            delta = Math.round((left / right - 1) * 1000) / 10;
            suffix = '%';
          }}
          const cls = delta > 0 ? 'trend-up' : delta < 0 ? 'trend-down' : '';
          const sign = delta > 0 ? '+' : delta < 0 ? '−' : '';
          return `
            <div class="number-trend">
              <strong class="${{cls}}">${{safeText(sign + formatNumber(Math.abs(delta), suffix))}}</strong>
              <span>к прошлому периоду</span>
            </div>
          `;
        }};
        const renderFormulaResultHtml = (result, preferredView = 'table', tableSettings = {{}}, widgetId = '', formulaSpec = null) => {{
          if (!result) return '<div class="report-empty">Результат еще не рассчитан</div>';
          if (result.kind === 'scalar') {{
            return `<div class="number-grid">${{renderMetricCard('Результат', result.value ?? 0, '', renderScalarTrendHtml(result, formulaSpec))}}</div>`;
          }}
          const rows = Array.isArray(result.rows) ? result.rows : [];
          if (!rows.length) return '<div class="report-empty">Нет данных</div>';
          if (result.kind === 'series') {{
            const seriesRows = rows.map((row) => ({{ ...row, value: formulaRowValue(row) }}));
            const dimensionColumns = seriesDimensionColumns(seriesRows);
            if (dimensionColumns.length > 1) {{
              const tableColumns = dimensionColumns.map((column) => column.key).concat(['Результат']);
              const prepared = applyFormulaTableSettings(result, seriesRows, tableColumns, tableSettings);
              return `
                <div class="report-table-wrap formula-table-wrap">
                  <table class="formula-data-table">
                    <thead>
                      <tr>
                        ${{dimensionColumns.map((column) => `<th>${{safeText(column.label)}}</th>`).join('')}}
                        <th>Результат</th>
                      </tr>
                    </thead>
                    <tbody>${{prepared.rows.map((row) => {{
                      return `
                        <tr class="${{formulaRowClass(row)}}">
                          ${{dimensionColumns.map((column) => {{
                            return `<td class="formula-row-label">${{safeText(rowColumnValue(row, column.key) || 'Без значения')}}</td>`;
                          }}).join('')}}
                          ${{renderSeriesDrilldownCell(widgetId, row, formatNumber(formulaRowValue(row)), 'formula-cell-number')}}
                        </tr>
                      `;
                    }}).join('')}}</tbody>
                  </table>
                </div>
              `;
            }}
            if (preferredView === 'bar') return renderBarChart(seriesRows, 'value');
            if (preferredView === 'line') return renderLineChart(seriesRows, 'value');
            if (preferredView === 'list') return renderTopList(seriesRows, 'value');
            const prepared = applyFormulaTableSettings(result, seriesRows, ['Результат'], tableSettings);
            return `
              <div class="report-table-wrap formula-table-wrap">
                <table class="formula-data-table">
                  <thead><tr><th>Значение</th><th>Результат</th></tr></thead>
                  <tbody>${{prepared.rows.map((row) => `
                    <tr class="${{formulaRowClass(row)}}">
                      <td class="formula-row-label">${{safeText(row.label ?? row.key ?? 'Без названия')}}</td>
                      ${{renderSeriesDrilldownCell(widgetId, row, formatNumber(formulaRowValue(row)), 'formula-cell-number')}}
                    </tr>
                  `).join('')}}</tbody>
                </table>
              </div>
            `;
          }}
          const columns = formulaTableColumns(result, rows);
          const prepared = applyFormulaTableSettings(result, rows, columns, tableSettings);
          const columnTitles = prepared.settings?.column_titles || {{}};
          const columnWidths = prepared.settings?.column_widths || {{}};
          const headerCell = (column, fallbackLabel = '') => {{
            const original = fallbackLabel || column;
            const width = Number(columnWidths[column] || 0);
            const widthStyle = width ? ` class="formula-col-fixed" style="width: ${{width}}px; max-width: ${{width}}px;"` : '';
            return `<th title="${{safeText(original)}}"${{widthStyle}}><span class="formula-col-title">${{safeText(columnTitles[column] || original)}}</span></th>`;
          }};
          const ratioColumns = Array.isArray(result.meta?.ratio_columns) ? result.meta.ratio_columns : null;
          // table-layout: fixed включается только при заданных ширинах — иначе
          // auto-раскладка игнорирует width на th и раздаёт остаток по контенту.
          const hasFixedColumns = Object.keys(columnWidths).length > 0;
          // Ширина таблицы = сумма колонок (незаданным даём дефолт), иначе
          // width:100% растянул бы колонки на контейнер. width:auto не годится:
          // с ним браузер отключает fixed-алгоритм раскладки.
          const fixedTotal = [tableLabelColumn].concat(prepared.columns).reduce(
            (total, column) => total + (Number(columnWidths[column]) || (column === tableLabelColumn ? 160 : 120)), 0);
          // Размер заголовков: small/large меняют CSS-переменную, «Обычный»
          // не ставит её — работают дефолты (11px, в fixed-режиме 10px).
          const headerFontMap = {{ small: '9px', large: '12px' }};
          const headerFont = headerFontMap[prepared.settings?.header_font_size] || '';
          const tableStyles = [];
          if (hasFixedColumns) tableStyles.push(`width: ${{fixedTotal}}px`);
          if (headerFont) tableStyles.push(`--widget-header-font: ${{headerFont}}`);
          const tableAttrs = ` class="formula-data-table${{hasFixedColumns ? ' has-fixed-columns' : ''}}"`
            + (tableStyles.length ? ` style="${{tableStyles.join('; ')}}"` : '');
          return `
            <div class="report-table-wrap formula-table-wrap">
              <table${{tableAttrs}}>
                <thead>
                  <tr>
                    ${{headerCell(tableLabelColumn, 'Строка')}}
                    ${{prepared.columns.map((column) => headerCell(column)).join('')}}
                  </tr>
                </thead>
                <tbody>${{prepared.rows.map((row) => `
                  <tr class="${{formulaRowClass(row)}}">
                    <td class="formula-row-label" title="${{safeText(row.label ?? row.key ?? 'Итого')}}">${{safeText(row.label ?? row.key ?? 'Итого')}}</td>
                    ${{prepared.columns.map((column) => renderDrilldownCell(widgetId, row, column, formatFormulaTableValue(column, row[column] ?? 0, ratioColumns), formulaCellClass(column, row[column] ?? 0, ratioColumns))).join('')}}
                  </tr>
                `).join('')}}</tbody>
              </table>
            </div>
          `;
        }};
        const selectedFormulaSourceId = () => Number(formulaSourceEl?.value || 0) || null;
        const withFormulaSource = (node) => {{
          const sourceId = selectedFormulaSourceId();
          if (!sourceId || !node || typeof node !== 'object') return node;
          const copy = Array.isArray(node) ? [...node] : {{ ...node }};
          const aggregateOps = new Set(['count', 'sum', 'avg', 'min', 'max']);
          if (aggregateOps.has(copy.op) && !copy.source_id) copy.source_id = sourceId;
          Object.keys(copy).forEach((key) => {{
            const value = copy[key];
            if (Array.isArray(value)) copy[key] = value.map((item) => withFormulaSource(item));
            else if (value && typeof value === 'object') copy[key] = withFormulaSource(value);
          }});
          return copy;
        }};
        const formulaTemplates = {{
          count: () => withFormulaSource({{
            op: 'count',
            from: 'leads',
            filters: [],
          }}),
          sum: () => withFormulaSource({{
            op: 'sum',
            from: 'leads',
            field: 'price',
            filters: [],
          }}),
          responsible: () => withFormulaSource({{
            op: 'count',
            from: 'leads',
            group_by: 'responsible_user_id',
            filters: [],
          }}),
          table: () => withFormulaSource({{
            op: 'table',
            columns: {{
              'Всего': {{
                op: 'count',
                from: 'leads',
                group_by: 'responsible_user_id',
                filters: [],
              }},
              'Сумма': {{
                op: 'sum',
                from: 'leads',
                field: 'price',
                group_by: 'responsible_user_id',
                filters: [],
              }},
              'Средний чек': {{
                op: 'avg',
                from: 'leads',
                field: 'price',
                group_by: 'responsible_user_id',
                filters: [],
              }},
            }},
          }}),
        }};
        const setFormulaTemplate = (name) => {{
          const factory = formulaTemplates[name] || formulaTemplates.count;
          if (!formulaEditorEl) return;
          formulaEditorEl.value = JSON.stringify(factory(), null, 2);
          lastFormulaResult = null;
          lastFormulaDiagnostics = null;
          if (formulaStatusEl) {{
            formulaStatusEl.classList.remove('error');
            formulaStatusEl.textContent = 'Шаблон вставлен. Можно менять поля, условия и нажать “Посчитать”.';
          }}
        }};
        const parseFormulaEditor = () => {{
          if (!formulaEditorEl) throw new Error('Редактор формулы не найден');
          try {{
            const parsed = JSON.parse(formulaEditorEl.value || '{{}}');
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Формула должна быть JSON-объектом');
            return parsed;
          }} catch (error) {{
            throw new Error('Не могу прочитать формулу: ' + error.message);
          }}
        }};
        const renderFormulaDictionary = (dictionary) => {{
          if (!formulaDictionaryEl) return;
          const entities = Array.isArray(dictionary?.entities) ? dictionary.entities : [];
          const rows = entities.map((entity) => {{
            const fields = (entity.fields || []).slice(0, 24);
            return `
              <div class="dictionary-group">
                <strong>${{safeText(entity.label || entity.value)}} · ${{formatNumber(entity.count || 0)}} строк</strong>
                <div class="dictionary-fields">
                  ${{fields.map((field) => `
                    <span class="dictionary-field" title="${{safeText(field.value)}}">${{safeText(field.label || field.value)}}</span>
                  `).join('')}}
                </div>
              </div>
            `;
          }});
          formulaDictionaryEl.innerHTML = rows.length ? rows.join('') : '<div class="report-empty">Словарь пока пустой</div>';
        }};
        const formulaEntities = () => Array.isArray(formulaDictionaryCache?.entities) ? formulaDictionaryCache.entities : [];
        const currentFormulaEntity = () => {{
          const entities = formulaEntities();
          return entities.find((entity) => entity.value === formulaEntityEl?.value) || entities[0] || null;
        }};
        const fieldLabel = (fieldValue) => {{
          const entities = formulaEntities();
          const field = entities.flatMap((entity) => entity.fields || []).find((item) => item.value === fieldValue);
          return field?.label || fieldValue || 'не выбрано';
        }};
        const fillFormulaSelect = (select, options, placeholder = '') => {{
          if (!select) return;
          const current = select.value;
          select.innerHTML = [
            placeholder ? `<option value="">${{safeText(placeholder)}}</option>` : '',
            ...options.map((item) => `<option value="${{safeText(item.value)}}">${{safeText(item.label || item.value)}}</option>`),
          ].join('');
          if ([...select.options].some((option) => option.value === current)) select.value = current;
        }};
        const currentFormulaFields = () => currentFormulaEntity()?.fields || [];
        const formulaFieldByValue = (fieldValue) => currentFormulaFields().find((field) => field.value === fieldValue) || null;
        const allFormulaFields = () => {{
          const seen = new Set();
          const fields = [];
          formulaEntities().forEach((entity) => {{
            (entity.fields || []).forEach((field) => {{
              if (!field?.value || seen.has(field.value)) return;
              seen.add(field.value);
              fields.push({{ ...field, entity_label: entity.label || entity.value }});
            }});
          }});
          return fields;
        }};
        const formulaFieldOptionsHtml = () => {{
          const options = allFormulaFields().map((field) => {{
            const suffix = field.entity_label ? ` · ${{field.entity_label}}` : '';
            return `<option value="${{safeText(field.value)}}">${{safeText(field.label || field.value)}}${{safeText(suffix)}} · ${{safeText(field.value)}}</option>`;
          }});
          return `<option value="">Выбрать поле, если вопрос про поле</option>${{options.join('')}}`;
        }};
        const formulaFilterOperators = (fieldType) => {{
          const common = [
            {{ value: 'eq', label: 'равно' }},
            {{ value: 'neq', label: 'не равно' }},
          ];
          if (fieldType === 'number') return [
            ...common,
            {{ value: 'gt', label: 'больше' }},
            {{ value: 'gte', label: 'больше или равно' }},
            {{ value: 'lt', label: 'меньше' }},
            {{ value: 'lte', label: 'меньше или равно' }},
            {{ value: 'between', label: 'число между' }},
            {{ value: 'in', label: 'в списке' }},
            {{ value: 'not_in', label: 'не в списке' }},
          ];
          if (fieldType === 'date' || fieldType === 'datetime') return [
            {{ value: 'this_month', label: 'текущий месяц' }},
            {{ value: 'previous_month', label: 'прошлый месяц' }},
            {{ value: 'this_week', label: 'текущая неделя' }},
            {{ value: 'previous_week', label: 'прошлая неделя' }},
            {{ value: 'last_days', label: 'последние N дней' }},
            {{ value: 'date_between', label: 'дата между' }},
            ...common,
            {{ value: 'gt', label: 'после' }},
            {{ value: 'lt', label: 'до' }},
          ];
          if (fieldType === 'month') return [
            {{ value: 'this_month', label: 'текущий месяц' }},
            {{ value: 'previous_month', label: 'прошлый месяц' }},
            {{ value: 'date_between', label: 'месяц между' }},
            ...common,
            {{ value: 'gt', label: 'после месяца' }},
            {{ value: 'lt', label: 'до месяца' }},
          ];
          if (fieldType === 'boolean') return common;
          return [
            ...common,
            {{ value: 'like', label: 'содержит текст' }},
            {{ value: 'in', label: 'в списке' }},
            {{ value: 'not_in', label: 'не в списке' }},
          ];
        }};
        const refreshFormulaFilterOperator = (row) => {{
          const fieldSelect = row.querySelector('[data-formula-filter-field]');
          const opSelect = row.querySelector('[data-formula-filter-op]');
          const valueInput = row.querySelector('[data-formula-filter-value]');
          if (!fieldSelect || !opSelect || !valueInput) return;
          const field = formulaFieldByValue(fieldSelect.value);
          const fieldType = String(field?.type || 'text');
          const current = opSelect.value;
          const operators = formulaFilterOperators(fieldType);
          opSelect.innerHTML = operators.map((item) => `<option value="${{safeText(item.value)}}">${{safeText(item.label)}}</option>`).join('');
          opSelect.value = operators.some((item) => item.value === current) ? current : operators[0]?.value || 'eq';
          const op = opSelect.value;
          if (['this_month', 'previous_month', 'this_week', 'previous_week'].includes(op)) {{
            valueInput.value = '';
            valueInput.placeholder = 'значение не нужно';
            valueInput.disabled = true;
          }} else {{
            valueInput.disabled = false;
            if (op === 'last_days') valueInput.placeholder = 'Например: 30';
            else if (fieldType === 'month') valueInput.placeholder = 'Например: 2026-07 или 2026-01..2026-07';
            else if (fieldType === 'date' || fieldType === 'datetime') valueInput.placeholder = 'Например: 2026-07-01..2026-07-31';
            else if (fieldType === 'number') valueInput.placeholder = 'Например: 1000 или 1000..5000';
            else valueInput.placeholder = 'Например: Конкин или Яндекс';
          }}
        }};
        const refreshFormulaFilterRows = () => {{
          formulaFilterRows = [...document.querySelectorAll('[data-formula-filter]')];
          return formulaFilterRows;
        }};
        const bindFormulaFilterRow = (row) => {{
          if (!row || row.dataset.formulaFilterBound === '1') return;
          row.dataset.formulaFilterBound = '1';
          row.querySelector('[data-formula-filter-field]')?.addEventListener('change', () => {{
            aiFormulaPinned = false;
            refreshFormulaFilterOperator(row);
            syncFormulaEditorFromMask();
          }});
          row.querySelector('[data-formula-filter-op]')?.addEventListener('change', () => {{
            aiFormulaPinned = false;
            refreshFormulaFilterOperator(row);
            syncFormulaEditorFromMask();
          }});
          row.querySelector('[data-formula-filter-value]')?.addEventListener('input', () => {{
            aiFormulaPinned = false;
            syncFormulaEditorFromMask();
          }});
        }};
        const addFormulaFilterRow = () => {{
          if (!formulaFilterListEl) return null;
          const sourceRow = formulaFilterRows[0] || document.querySelector('[data-formula-filter]');
          if (!sourceRow) return null;
          const row = sourceRow.cloneNode(true);
          row.dataset.formulaFilterBound = '';
          row.querySelectorAll('select').forEach((select) => select.value = '');
          const fieldSelect = row.querySelector('[data-formula-filter-field]');
          const opSelect = row.querySelector('[data-formula-filter-op]');
          const valueInput = row.querySelector('[data-formula-filter-value]');
          if (fieldSelect) fieldSelect.value = '';
          if (opSelect) opSelect.value = 'eq';
          if (valueInput) {{
            valueInput.value = '';
            valueInput.disabled = false;
          }}
          formulaFilterListEl.append(row);
          refreshFormulaFilterRows();
          bindFormulaFilterRow(row);
          refreshFormulaFieldControls();
          return row;
        }};
        const refreshFormulaFieldControls = () => {{
          const entity = currentFormulaEntity();
          const fields = entity?.fields || [];
          const numericFields = fields.filter((field) => ['number', 'numeric', 'price'].includes(String(field.type || '')));
          const groupableFields = fields.filter((field) => field.groupable !== false);
          fillFormulaSelect(formulaValueFieldEl, numericFields.length ? numericFields : fields, 'Не нужно для количества');
          fillFormulaSelect(formulaGroupFieldEl, groupableFields, 'Не разбивать, одно число');
          refreshFormulaFilterRows().forEach((row) => {{
            fillFormulaSelect(row.querySelector('[data-formula-filter-field]'), fields, 'Без условия');
            refreshFormulaFilterOperator(row);
          }});
          if (formulaOpEl?.value === 'count') formulaValueFieldEl.value = '';
        }};
        const refreshFormulaHumanBuilder = () => {{
          const entities = formulaEntities();
          fillFormulaSelect(formulaEntityEl, entities, '');
          if (!formulaEntityEl?.value && entities[0]) formulaEntityEl.value = entities[0].value;
          refreshFormulaFieldControls();
        }};
        const parseFormulaMaskValue = (op, raw) => {{
          const value = String(raw || '').trim();
          if (['this_month', 'previous_month', 'this_week', 'previous_week'].includes(op)) return true;
          if (op === 'last_days') return Number(value || 30);
          if (['in', 'not_in'].includes(op)) {{
            return value.split(',').map((item) => item.trim()).filter(Boolean).map((item) => {{
              const number = Number(item);
              return Number.isFinite(number) && item !== '' ? number : item;
            }});
          }}
          if (['between', 'date_between'].includes(op)) {{
            const parts = value.includes('..') ? value.split('..') : value.split(',');
            return parts.map((item) => item.trim()).filter(Boolean);
          }}
          const number = Number(value);
          return Number.isFinite(number) && value !== '' ? number : value;
        }};
        const buildFormulaFromMask = () => {{
          const op = formulaOpEl?.value || 'count';
          const entity = formulaEntityEl?.value || 'leads';
          const groupBy = formulaGroupFieldEl?.value || '';
          const valueField = formulaValueFieldEl?.value || '';
          const sourceId = selectedFormulaSourceId();
          const filters = refreshFormulaFilterRows().map((row) => {{
            const field = row.querySelector('[data-formula-filter-field]')?.value || '';
            const filterOp = row.querySelector('[data-formula-filter-op]')?.value || 'eq';
            const raw = row.querySelector('[data-formula-filter-value]')?.value || '';
            const valueOptional = ['this_month', 'previous_month', 'this_week', 'previous_week', 'last_days'].includes(filterOp);
            if (!field || (!raw.trim() && !valueOptional)) return null;
            return {{
              field,
              op: filterOp,
              value: parseFormulaMaskValue(filterOp, raw),
            }};
          }}).filter(Boolean);
          const formula = {{
            op,
            from: entity,
            where: filters,
          }};
          if (sourceId) formula.source_id = sourceId;
          if (op !== 'count' && valueField) formula.field = valueField;
          if (groupBy) formula.group_by = groupBy;
          return formula;
        }};
        const describeFormulaMask = (formula) => {{
          const opLabels = {{
            count: 'считаем количество',
            sum: 'складываем',
            avg: 'считаем среднее',
            min: 'берем минимум',
            max: 'берем максимум',
          }};
          const filterOpLabels = {{
            eq: 'равно',
            neq: 'не равно',
            like: 'содержит',
            in: 'в списке',
            not_in: 'не в списке',
            gt: 'больше',
            gte: 'больше или равно',
            lt: 'меньше',
            lte: 'меньше или равно',
            between: 'между',
            date_between: 'дата между',
            this_month: 'текущий месяц',
            previous_month: 'прошлый месяц',
            this_week: 'текущая неделя',
            previous_week: 'прошлая неделя',
            last_days: 'последние дни',
          }};
          const presetFilterOps = new Set(['this_month', 'previous_month', 'this_week', 'previous_week']);
          const entity = currentFormulaEntity();
          const sourceName = formula.source_id ? sourceSubtitle(formula.source_id) : 'весь хаб';
          const fieldPart = formula.field ? ` по столбцу <b>${{safeText(fieldLabel(formula.field))}}</b>` : '';
          const groupPart = formula.group_by ? `, разбиваем по <b>${{safeText(fieldLabel(formula.group_by))}}</b>` : ', получаем одно число';
          const filters = formula.where || formula.filters || [];
          const filterPart = filters.length
            ? `, условия: ${{filters.map((filter) => {{
                const value = Array.isArray(filter.value) ? filter.value.join(' .. ') : filter.value;
                const valueText = presetFilterOps.has(filter.op) ? '' : ` ${{safeText(value)}}`;
                return `<b>${{safeText(fieldLabel(filter.field))}}</b> — ${{safeText(filterOpLabels[filter.op] || filter.op)}}${{valueText}}`;
              }}).join('; ')}}`
            : ', без дополнительных условий';
          return `Берем <b>${{safeText(entity?.label || formula.from)}}</b> из источника <b>${{safeText(sourceName)}}</b>, ${{opLabels[formula.op] || formula.op}}${{fieldPart}}${{groupPart}}${{filterPart}}.`;
        }};
        const syncFormulaEditorFromMask = () => {{
          if (!formulaEditorEl) return;
          // An applied AI draft pins the editor: mask events must not overwrite
          // it until the user deliberately edits the mask (which clears the pin).
          if (aiFormulaPinned) return;
          const formula = buildFormulaFromMask();
          formulaEditorEl.value = JSON.stringify(formula, null, 2);
          if (formulaReadableEl) formulaReadableEl.innerHTML = describeFormulaMask(formula);
          lastFormulaResult = null;
        }};
        const renderAmoFilterImport = (data) => {{
          if (!amoFilterResultEl) return;
          const source = data.source
            ? `<div class="filter-import-pill"><span>Источник</span><b>${{safeText(data.source.name)}}</b></div>`
            : '<div class="filter-import-pill"><span>Источник</span><b>Не найден, останется текущий</b></div>';
          const conditions = (data.conditions || []).map((condition, index) => `
            <li>
              <span>${{index + 1}}</span>
              <b>${{safeText(condition.label || condition.field)}}</b>
            </li>
          `).join('');
          const ignored = (data.ignored || []).length
            ? `<div class="sync-status">Не перенес в формулу: ${{safeText((data.ignored || []).join(', '))}}</div>`
            : '';
          amoFilterResultEl.innerHTML = `
            <div class="filter-import-card">
              <div class="filter-import-grid">${{source}}</div>
              <ul class="filter-import-list">${{conditions || '<li><span>0</span><b>Условия не найдены</b></li>'}}</ul>
              ${{ignored}}
            </div>
          `;
        }};
        const parseAmoFilterUrl = async () => {{
          if (!amoFilterUrlEl || !amoFilterParseBtn) return;
          const url = amoFilterUrlEl.value.trim();
          if (!url) {{
            if (amoFilterStatusEl) {{
              amoFilterStatusEl.textContent = 'Вставь ссылку фильтра из amoCRM.';
              amoFilterStatusEl.classList.add('error');
            }}
            return;
          }}
          amoFilterParseBtn.disabled = true;
          if (amoFilterApplyBtn) amoFilterApplyBtn.disabled = true;
          if (amoFilterStatusEl) {{
            amoFilterStatusEl.classList.remove('error');
            amoFilterStatusEl.textContent = 'Разбираю ссылку amoCRM...';
          }}
          try {{
            const response = await fetch(apiUrl('/api/amo-filter/parse'), {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ url }}),
            }});
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || 'parse failed');
            lastAmoFilterImport = data;
            renderAmoFilterImport(data);
            setFormulaTitle(suggestedFormulaTitle());
            if (amoFilterApplyBtn) amoFilterApplyBtn.disabled = false;
            if (amoFilterStatusEl) amoFilterStatusEl.textContent = 'Готово. Можно применить условия к формуле.';
          }} catch (error) {{
            lastAmoFilterImport = null;
            if (amoFilterResultEl) amoFilterResultEl.innerHTML = `<div class="report-empty">Ошибка: ${{safeText(error.message)}}</div>`;
            if (amoFilterStatusEl) {{
              amoFilterStatusEl.textContent = 'Не смог разобрать ссылку';
              amoFilterStatusEl.classList.add('error');
            }}
          }} finally {{
            amoFilterParseBtn.disabled = false;
          }}
        }};
        const applyAmoFilterImport = async () => {{
          const patch = lastAmoFilterImport?.formula_patch || {{}};
          const conditions = Array.isArray(patch.conditions) ? patch.conditions : [];
          if (amoFilterApplyBtn) amoFilterApplyBtn.disabled = true;
          if (patch.source_id && formulaSourceEl) {{
            formulaSourceEl.value = String(patch.source_id);
          }}
          setFormulaTitle(suggestedFormulaTitle());
          if (formulaEntityEl) formulaEntityEl.value = 'leads';
          refreshFormulaFieldControls();
          while (refreshFormulaFilterRows().length < conditions.length) {{
            addFormulaFilterRow();
          }}
          refreshFormulaFilterRows().forEach((row, index) => {{
            const condition = conditions[index] || {{}};
            const fieldEl = row.querySelector('[data-formula-filter-field]');
            const opEl = row.querySelector('[data-formula-filter-op]');
            const valueEl = row.querySelector('[data-formula-filter-value]');
            if (!fieldEl || !opEl || !valueEl) return;
            fieldEl.value = condition.field || '';
            refreshFormulaFilterOperator(row);
            if (condition.op && [...opEl.options].some((option) => option.value === condition.op)) {{
              opEl.value = condition.op;
            }}
            refreshFormulaFilterOperator(row);
            const value = condition.value;
            valueEl.value = Array.isArray(value) ? value.join(', ') : (value === true || value === undefined || value === null ? '' : String(value));
          }});
          // amo-filter import is a fresh, deliberate formula — release the pin so
          // the editor picks up the imported mask conditions.
          aiFormulaPinned = false;
          syncFormulaEditorFromMask();
          if (amoFilterStatusEl) amoFilterStatusEl.textContent = `Применил ${{conditions.length}} условий к формуле. Считаю предпросмотр...`;
          try {{
            await runFormula();
            if (amoFilterStatusEl) amoFilterStatusEl.textContent = `Применил ${{conditions.length}} условий и посчитал предпросмотр. Дальше можно отправить показатель на дашборд.`;
          }} finally {{
            if (amoFilterApplyBtn) amoFilterApplyBtn.disabled = false;
          }}
        }};
        // ═══ Конструктор колонок: блоки «название + описание», каждый —
        // отдельный AI-запрос на одну колонку; таблица склеивается из готовых.
        const columnBuilderEl = document.querySelector('[data-column-builder]');
        const columnBuilderListEl = document.querySelector('[data-column-builder-list]');
        const columnBuilderLiveEl = document.querySelector('[data-column-builder-live]');
        const columnBuilderGroupEl = document.querySelector('[data-column-builder-group]');
        const columnBuilderStatusEl = document.querySelector('[data-column-builder-status]');
        let columnBuilderSeq = 0;
        const columnBuilderBlocks = [];
        const columnBuilderAggregateOps = new Set(['count', 'sum', 'avg', 'min', 'max']);
        const setColumnBuilderStatus = (text, isError = false) => {{
          if (!columnBuilderStatusEl) return;
          columnBuilderStatusEl.textContent = text;
          columnBuilderStatusEl.classList.toggle('error', Boolean(isError));
        }};
        const refreshColumnBuilderGroupOptions = () => {{
          if (!columnBuilderGroupEl) return;
          const groupable = allFormulaFields().filter((field) => field.groupable);
          fillFormulaSelect(
            columnBuilderGroupEl,
            groupable.map((field) => ({{ value: field.value, label: `${{field.label || field.value}} · ${{field.value}}` }})),
            '(из первой колонки)',
          );
        }};
        const pinGroupByDeep = (node, groupField) => {{
          if (!node || typeof node !== 'object' || Array.isArray(node)) return;
          if (columnBuilderAggregateOps.has(String(node.op || '').toLowerCase())) node.group_by = groupField;
          ['left', 'right', 'return', 'body'].forEach((key) => pinGroupByDeep(node[key], groupField));
          if (node.vars && typeof node.vars === 'object') {{
            Object.values(node.vars).forEach((child) => pinGroupByDeep(child, groupField));
          }}
        }};
        const findGroupByDeep = (node) => {{
          if (!node || typeof node !== 'object' || Array.isArray(node)) return null;
          if (node.group_by) return node.group_by;
          for (const key of ['left', 'right', 'return', 'body']) {{
            const found = findGroupByDeep(node[key]);
            if (found) return found;
          }}
          if (node.vars && typeof node.vars === 'object') {{
            for (const child of Object.values(node.vars)) {{
              const found = findGroupByDeep(child);
              if (found) return found;
            }}
          }}
          return null;
        }};
        // Поле для пина группировки: выбранное в селекте, иначе — из первой
        // успешно собранной колонки (null = первый блок задаёт группировку сам).
        const columnBuilderPinField = () => {{
          if (columnBuilderGroupEl?.value) return columnBuilderGroupEl.value;
          const first = columnBuilderBlocks.find((block) => block.status === 'ok' && block.formula);
          return first ? findGroupByDeep(first.formula) : null;
        }};
        const columnBlockPreviewText = (result) => {{
          if (!result) return '';
          if (result.kind === 'series') return `Превью: ${{(result.rows || []).length}} строк`;
          if (result.kind === 'scalar') return `Превью: итог ${{formatNumber(result.value ?? 0)}} (без группировки)`;
          return `Превью: ${{result.kind}}`;
        }};
        const columnBlockStatusHtml = (block) => {{
          if (block.status === 'busy') return '<span class="column-builder-row-status">Собираю...</span>';
          if (block.status === 'ok') return '<span class="column-builder-row-status ok">готово ✓</span>';
          if (block.status === 'error') return `<span class="column-builder-row-status error">ошибка ✗ ${{safeText(block.error || '')}}</span>`;
          return '';
        }};
        // Доля (форматировать процентом): у одиночной колонки нет
        // meta.ratio_columns (он только у table-результата) — признак берём
        // из op формулы блока либо meta.op его результата.
        const columnBlockIsRatio = (block) => {{
          if (String(block.formula?.op || '').toLowerCase() === 'divide') return true;
          if (Array.isArray(block.result?.meta?.ratio_columns) && block.result.meta.ratio_columns.length) return true;
          return String(block.result?.meta?.op || '').toLowerCase() === 'divide';
        }};
        const columnBlockRowsMap = (block) => {{
          const map = new Map();
          const result = block.result;
          if (!result) return map;
          if (result.kind === 'series') {{
            (result.rows || []).forEach((row) => {{
              const key = String(row.key ?? row.label ?? '');
              if (!key) return;
              map.set(key, {{ label: row.label ?? key, value: Number(row.value) || 0 }});
            }});
          }} else if (result.kind === 'scalar') {{
            map.set('__total__', {{ label: 'Итого', value: Number(result.value) || 0 }});
          }}
          return map;
        }};
        // Живая таблица: склейка УЖЕ полученных результатов блоков на фронте,
        // ни одного запроса к бэку. Финальную формулу с честным пересчётом
        // по-прежнему делает «Собрать таблицу».
        const renderColumnBuilderLiveTable = () => {{
          if (!columnBuilderLiveEl) return;
          const okBlocks = columnBuilderBlocks.filter((block) => block.status === 'ok' && block.formula && block.result);
          if (!okBlocks.length) {{
            columnBuilderLiveEl.innerHTML = '';
            return;
          }}
          const skipped = columnBuilderBlocks.length - okBlocks.length;
          const columnDefs = okBlocks.map((block, index) => ({{
            title: String(block.title || '').trim() || `Колонка ${{index + 1}}`,
            rows: columnBlockRowsMap(block),
            isRatio: columnBlockIsRatio(block),
          }}));
          // Union ключей в порядке первого появления (первая колонка главнее),
          // затем сортировка по убыванию первой колонки; строка «Итого» — в конец.
          const keys = [];
          const seen = new Set();
          columnDefs.forEach((def) => def.rows.forEach((_, key) => {{
            if (!seen.has(key)) {{
              seen.add(key);
              keys.push(key);
            }}
          }}));
          const firstRows = columnDefs[0].rows;
          keys.sort((a, b) => (Number(firstRows.get(b)?.value) || 0) - (Number(firstRows.get(a)?.value) || 0));
          const totalIndex = keys.indexOf('__total__');
          if (totalIndex >= 0) keys.push(keys.splice(totalIndex, 1)[0]);
          const labelFor = (key) => {{
            for (const def of columnDefs) {{
              const row = def.rows.get(key);
              if (row?.label) return row.label;
            }}
            return key;
          }};
          const cellText = (def, key) => {{
            const value = Number(def.rows.get(key)?.value) || 0;
            return def.isRatio ? formatNumber(value * 100, '%') : formatNumber(value);
          }};
          columnBuilderLiveEl.innerHTML = `
            <div class="column-builder-live-caption">предпросмотр · собрано из отдельных колонок${{skipped > 0 ? ` · пропущено: ${{skipped}}` : ''}}</div>
            <div class="report-table-wrap">
              <table class="column-builder-live-table">
                <thead>
                  <tr>
                    <th>Строка</th>
                    ${{columnDefs.map((def) => `<th>${{safeText(def.title)}}</th>`).join('')}}
                  </tr>
                </thead>
                <tbody>${{keys.map((key) => `
                  <tr>
                    <td class="column-builder-live-label">${{safeText(labelFor(key))}}</td>
                    ${{columnDefs.map((def) => `<td>${{safeText(cellText(def, key))}}</td>`).join('')}}
                  </tr>
                `).join('')}}</tbody>
              </table>
            </div>
          `;
        }};
        const renderColumnBuilder = () => {{
          if (!columnBuilderListEl) return;
          if (!columnBuilderBlocks.length) {{
            columnBuilderListEl.innerHTML = '<div class="report-empty">Колонок пока нет — добавь первую.</div>';
            return;
          }}
          columnBuilderListEl.innerHTML = columnBuilderBlocks.map((block, index) => `
            <div class="column-builder-row" data-column-block="${{safeText(block.id)}}">
              <div class="column-builder-row-head">
                <input type="text" data-column-title placeholder="Название (пусто — возьмём из AI)" value="${{safeText(block.title)}}">
                <button type="button" class="secondary" data-column-block-run ${{block.status === 'busy' ? 'disabled' : ''}}>Собрать</button>
                <div class="widget-column-move">
                  <button type="button" data-column-block-move="up" title="Выше" ${{index === 0 ? 'disabled' : ''}}>&#8593;</button>
                  <button type="button" data-column-block-move="down" title="Ниже" ${{index === columnBuilderBlocks.length - 1 ? 'disabled' : ''}}>&#8595;</button>
                </div>
                <button type="button" class="secondary" data-column-block-remove title="Удалить колонку">&#10005;</button>
                ${{columnBlockStatusHtml(block)}}
              </div>
              <textarea rows="2" data-column-prompt placeholder="Что считаем: например, количество сделок за текущий месяц">${{safeText(block.prompt)}}</textarea>
              ${{block.preview ? `<div class="column-builder-preview">${{safeText(block.preview)}}</div>` : ''}}
            </div>
          `).join('');
          renderColumnBuilderLiveTable();
        }};
        const columnBlockById = (blockId) => columnBuilderBlocks.find((block) => String(block.id) === String(blockId));
        const generateColumnBlock = async (blockId) => {{
          const block = columnBlockById(blockId);
          if (!block) return;
          const text = String(block.prompt || '').trim();
          if (!text) {{
            block.status = 'error';
            block.error = 'Опиши, что считаем';
            renderColumnBuilder();
            return;
          }}
          const pinBefore = columnBuilderPinField();
          const groupHint = pinBefore ? ` с группировкой по полю ${{fieldLabel(pinBefore)}} (${{pinBefore}})` : ' с группировкой';
          const prompt = `${{text}}. Верни ОДНУ агрегатную формулу (count/sum/avg/divide)${{groupHint}}, без таблицы (op не table).`;
          block.status = 'busy';
          block.error = '';
          renderColumnBuilder();
          try {{
            const response = await fetch(apiUrl('/api/ai/formula/draft'), {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ prompt, source_id: selectedFormulaSourceId() }}),
            }});
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || 'draft failed');
            const draft = data.draft || {{}};
            if (draft.configured === false) throw new Error(draft.message || 'AI-ключ не настроен');
            let formula = draft.formula;
            let draftTitle = String(draft.title || '').trim();
            if (formula && String(formula.op || '').toLowerCase() === 'table') {{
              // Модель всё же вернула таблицу — берём её первую колонку.
              const entries = Object.entries(formula.columns || {{}});
              if (!entries.length) throw new Error('AI вернул таблицу без колонок');
              draftTitle = entries[0][0] || draftTitle;
              formula = entries[0][1];
            }}
            if (!formula || typeof formula !== 'object') throw new Error('AI не вернул формулу');
            const pinField = columnBuilderPinField();
            if (pinField) pinGroupByDeep(formula, pinField);
            block.formula = formula;
            if (!String(block.title || '').trim() && draftTitle) block.title = draftTitle;
            block.status = 'ok';
            block.preview = columnBlockPreviewText(data.result);
            block.result = data.result || null;
          }} catch (error) {{
            block.status = 'error';
            block.error = error.message;
            block.formula = null;
            block.preview = '';
            block.result = null;
          }}
          renderColumnBuilder();
        }};
        const assembleColumnBuilderTable = async () => {{
          if (!formulaEditorEl) return;
          const ready = columnBuilderBlocks.filter((block) => block.status === 'ok' && block.formula);
          const skipped = columnBuilderBlocks.filter((block) => !(block.status === 'ok' && block.formula));
          if (!ready.length) {{
            setColumnBuilderStatus('Нет собранных колонок: нажми «Собрать» у каждого блока.', true);
            return;
          }}
          const columns = {{}};
          ready.forEach((block, index) => {{
            let title = String(block.title || '').trim() || `Колонка ${{index + 1}}`;
            while (columns[title]) title += ' ·';
            columns[title] = block.formula;
          }});
          formulaEditorEl.value = JSON.stringify({{ op: 'table', columns }}, null, 2);
          aiFormulaPinned = true;
          const skippedNote = skipped.length
            ? `, пропущено: ${{skipped.map((block) => String(block.title || '').trim() || 'без названия').join(', ')}}`
            : '';
          setColumnBuilderStatus(`Собрано ${{ready.length}} из ${{columnBuilderBlocks.length}}${{skippedNote}}. Считаю превью...`, false);
          await runFormula();
          setColumnBuilderStatus(`Собрано ${{ready.length}} из ${{columnBuilderBlocks.length}}${{skippedNote}}. Превью ниже — можно отправить на дашборд.`, false);
        }};
        columnBuilderEl?.addEventListener('click', async (event) => {{
          if (event.target.closest('[data-column-builder-add]')) {{
            columnBuilderSeq += 1;
            columnBuilderBlocks.push({{ id: `col-${{columnBuilderSeq}}`, title: '', prompt: '', formula: null, status: 'empty', error: '', preview: '', result: null }});
            renderColumnBuilder();
            return;
          }}
          if (event.target.closest('[data-column-builder-assemble]')) {{
            await assembleColumnBuilderTable();
            return;
          }}
          const row = event.target.closest('[data-column-block]');
          if (!row) return;
          const blockId = row.dataset.columnBlock;
          if (event.target.closest('[data-column-block-remove]')) {{
            const index = columnBuilderBlocks.findIndex((block) => String(block.id) === String(blockId));
            if (index >= 0) columnBuilderBlocks.splice(index, 1);
            renderColumnBuilder();
            return;
          }}
          const moveButton = event.target.closest('[data-column-block-move]');
          if (moveButton) {{
            const index = columnBuilderBlocks.findIndex((block) => String(block.id) === String(blockId));
            const target = moveButton.dataset.columnBlockMove === 'up' ? index - 1 : index + 1;
            if (index < 0 || target < 0 || target >= columnBuilderBlocks.length) return;
            [columnBuilderBlocks[index], columnBuilderBlocks[target]] = [columnBuilderBlocks[target], columnBuilderBlocks[index]];
            renderColumnBuilder();
            return;
          }}
          if (event.target.closest('[data-column-block-run]')) {{
            await generateColumnBlock(blockId);
          }}
        }});
        columnBuilderEl?.addEventListener('input', (event) => {{
          const row = event.target.closest('[data-column-block]');
          if (!row) return;
          const block = columnBlockById(row.dataset.columnBlock);
          if (!block) return;
          // Правка текста не перерисовывает список — фокус остаётся в поле.
          if (event.target.matches('[data-column-title]')) block.title = event.target.value;
          if (event.target.matches('[data-column-prompt]')) block.prompt = event.target.value;
        }});
        // Заголовок в живой таблице обновляем по blur (change), не на каждый
        // символ — чтобы не дёргать перерисовку под руками.
        columnBuilderEl?.addEventListener('change', (event) => {{
          if (event.target.matches('[data-column-title]')) renderColumnBuilderLiveTable();
        }});
        renderColumnBuilder();
        const loadFormulaDictionary = async () => {{
          if (!formulaDictionaryEl) return;
          try {{
            const response = await fetch(apiUrl('/api/formula/dictionary'));
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || 'dictionary failed');
            formulaDictionaryCache = data.dictionary;
            renderFormulaDictionary(data.dictionary);
            refreshFormulaHumanBuilder();
            refreshColumnBuilderGroupOptions();
            syncFormulaEditorFromMask();
          }} catch (error) {{
            formulaDictionaryEl.innerHTML = `<div class="report-empty">Не удалось загрузить словарь: ${{safeText(error.message)}}</div>`;
          }}
        }};
        const runFormula = async () => {{
          if (!formulaRunBtn || !formulaResultEl) return;
          formulaRunBtn.disabled = true;
          if (formulaStatusEl) {{
            formulaStatusEl.classList.remove('error');
            formulaStatusEl.textContent = 'Считаю формулу по текущему массиву...';
          }}
          try {{
            const formula = parseFormulaEditor();
            const response = await fetch(apiUrl('/api/formula/evaluate'), {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ formula }}),
            }});
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || 'formula failed');
            lastFormulaResult = data.result;
            lastFormulaDiagnostics = data.diagnostics || null;
            formulaResultEl.innerHTML = renderFormulaResultHtml(data.result, 'table', {{}}, '', formula) + renderFormulaExplanationHtml(data.result, lastFormulaDiagnostics) + renderFormulaDiagnosticsHtml(lastFormulaDiagnostics);
            if (formulaStatusEl) formulaStatusEl.textContent = 'Готово. Результат можно отправить на дашборд.';
          }} catch (error) {{
            lastFormulaResult = null;
            lastFormulaDiagnostics = null;
            formulaResultEl.innerHTML = `<div class="report-empty">Ошибка: ${{safeText(error.message)}}</div>`;
            if (formulaStatusEl) {{
              formulaStatusEl.textContent = 'Ошибка формулы';
              formulaStatusEl.classList.add('error');
            }}
          }} finally {{
            formulaRunBtn.disabled = false;
          }}
        }};
        const renderAiFormulaDraft = (draft, result, diagnostics = null) => {{
          const questionItems = Array.isArray(draft.questions) ? draft.questions.filter(Boolean) : [];
          const questions = questionItems.length
            ? `
              <div class="ai-question-box" data-ai-question-box>
                ${{questionItems.map((item, index) => `
                  <div class="ai-question-row" data-ai-question-row>
                    <strong>${{safeText(item)}}</strong>
                    <div class="ai-question-controls">
                      <select data-ai-question-field>
                        ${{formulaFieldOptionsHtml()}}
                      </select>
                      <input data-ai-question-answer placeholder="Ответ текстом: да, нет, 10 строк, весь хаб...">
                      <button type="button" class="secondary" data-ai-question-quick="да">Да</button>
                      <button type="button" class="secondary" data-ai-question-quick="нет">Нет</button>
                    </div>
                  </div>
                `).join('')}}
                <button type="button" class="secondary ai-question-apply" data-ai-question-submit>Уточнить и пересчитать</button>
              </div>
            `
            : '<p>Уточняющих вопросов нет.</p>';
          const resultHtml = result ? renderFormulaResultHtml(result, draft.view || 'table', {{}}, '', draft.formula || null) + renderFormulaExplanationHtml(result, diagnostics) + renderFormulaDiagnosticsHtml(diagnostics) : '';
          return `
            <div class="ai-draft-card">
              <h4>${{safeText(draft.title || 'Черновик показателя')}}</h4>
              <p>${{safeText(draft.explanation || 'AI собрал черновик формулы.')}}</p>
              <p style="margin-top: 8px;"><b>Уверенность:</b> ${{Math.round(Number(draft.confidence || 0) * 100)}}%</p>
              <div style="margin-top: 8px;"><b>Вопросы:</b> ${{questions}}</div>
            </div>
            ${{resultHtml}}
          `;
        }};
        const runAiFormulaDraft = async () => {{
          if (!aiFormulaRunBtn || !aiFormulaPromptEl || !aiFormulaResultEl) return;
          const prompt = aiFormulaPromptEl.value.trim();
          if (!prompt) {{
            aiFormulaStatusEl.textContent = 'Сначала опиши, что нужно посчитать.';
            aiFormulaStatusEl.classList.add('error');
            return;
          }}
          aiFormulaRunBtn.disabled = true;
          aiFormulaApplyBtn.disabled = true;
          lastAiFormulaDraft = null;
          aiFormulaStatusEl.classList.remove('error');
          aiFormulaStatusEl.textContent = 'AI-запрос отправлен. Собираю формулу и проверяю ее на данных...';
          aiFormulaResultEl.innerHTML = '<div class="report-empty">Готовлю черновик...</div>';
          const controller = new AbortController();
          const timeoutId = window.setTimeout(() => controller.abort(), 300000);
          const startedAt = Date.now();
          const formatAiElapsed = () => {{
            const total = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
            if (total < 60) return total + ' сек';
            const minutes = Math.floor(total / 60);
            const seconds = total % 60;
            return minutes + ' мин ' + String(seconds).padStart(2, '0') + ' сек';
          }};
          const updateAiProgress = () => {{
            if (aiFormulaStatusEl && aiFormulaRunBtn.disabled) {{
              aiFormulaStatusEl.textContent = 'AI думает: ' + formatAiElapsed() + '. Сложные таблицы могут собираться 2-4 минуты.';
            }}
          }};
          const timerId = window.setInterval(updateAiProgress, 1000);
          const slowHintId = window.setTimeout(() => {{
            if (aiFormulaStatusEl && aiFormulaRunBtn.disabled) {{
              updateAiProgress();
            }}
          }}, 12000);
          try {{
            const response = await fetch(apiUrl('/api/ai/formula/draft'), {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ prompt, source_id: selectedFormulaSourceId() }}),
              signal: controller.signal,
            }});
            const contentType = response.headers.get('content-type') || '';
            const data = contentType.includes('application/json')
              ? await response.json()
              : {{ ok: false, error: await response.text() }};
            if (!response.ok || !data.ok) {{
              if (response.status === 504) {{
                throw new Error('AI не успел собрать сложную формулу до таймаута шлюза. Я уже увеличил ожидание, попробуй еще раз.');
              }}
              throw new Error(data.error || `AI draft failed: HTTP ${{response.status}}`);
            }}
            if (!data.configured) {{
              aiFormulaStatusEl.textContent = data.draft?.message || 'AI-ключ не настроен.';
              aiFormulaStatusEl.classList.add('error');
              aiFormulaResultEl.innerHTML = '<div class="report-empty">Добавь OPENAI_API_KEY или OPENROUTER_API_KEY на сервере.</div>';
              return;
            }}
            lastAiFormulaDraft = data.draft;
            lastFormulaResult = data.result || null;
            lastFormulaDiagnostics = data.diagnostics || null;
            aiFormulaResultEl.innerHTML = renderAiFormulaDraft(data.draft, data.result, lastFormulaDiagnostics);
            aiFormulaApplyBtn.disabled = false;
            aiFormulaStatusEl.textContent = 'Черновик готов. Проверь результат и нажми “Применить черновик”.';
          }} catch (error) {{
            const message = error.name === 'AbortError'
              ? 'AI не ответил за 300 секунд. Попробуй еще раз или разбей таблицу на несколько показателей.'
              : 'Ошибка AI: ' + error.message;
            aiFormulaStatusEl.textContent = message;
            aiFormulaStatusEl.classList.add('error');
            aiFormulaResultEl.innerHTML = `<div class="report-empty">${{safeText(message)}}</div>`;
          }} finally {{
            window.clearTimeout(timeoutId);
            window.clearTimeout(slowHintId);
            window.clearInterval(timerId);
            aiFormulaRunBtn.disabled = false;
          }}
        }};
        const collectAiQuestionAnswers = () => {{
          if (!aiFormulaResultEl) return [];
          return [...aiFormulaResultEl.querySelectorAll('[data-ai-question-row]')].map((row) => {{
            const question = row.querySelector('strong')?.textContent?.trim() || '';
            const field = row.querySelector('[data-ai-question-field]')?.value || '';
            const answer = row.querySelector('[data-ai-question-answer]')?.value?.trim() || '';
            const parts = [];
            if (question) parts.push(`вопрос: ${{question}}`);
            if (answer) parts.push(`ответ: ${{answer}}`);
            if (field) parts.push(`выбранное поле: ${{field}} (${{fieldLabel(field)}})`);
            return parts.join('; ');
          }}).filter(Boolean);
        }};
        const rerunAiFormulaWithAnswers = async () => {{
          if (!aiFormulaPromptEl) return;
          const answers = collectAiQuestionAnswers();
          if (!answers.length) {{
            if (aiFormulaStatusEl) {{
              aiFormulaStatusEl.textContent = 'Сначала ответь хотя бы на один вопрос или выбери поле.';
              aiFormulaStatusEl.classList.add('error');
            }}
            return;
          }}
          const answerMarker = '\\n\\nОтветы на вопросы:';
          const markerIndex = aiFormulaPromptEl.value.indexOf(answerMarker);
          const basePrompt = (markerIndex >= 0
            ? aiFormulaPromptEl.value.slice(0, markerIndex)
            : aiFormulaPromptEl.value
          ).trim();
          aiFormulaPromptEl.value = `${{basePrompt}}\\n\\nОтветы на вопросы:\\n- ${{answers.join('\\n- ')}}`;
          await runAiFormulaDraft();
        }};
        const applyAiFormulaDraft = () => {{
          if (!lastAiFormulaDraft?.formula || !formulaEditorEl) return;
          formulaEditorEl.value = JSON.stringify(lastAiFormulaDraft.formula, null, 2);
          aiFormulaPinned = true;
          if (lastAiFormulaDraft.title) setFormulaTitle(lastAiFormulaDraft.title, {{ force: true }});
          if (formulaSizeEl && lastAiFormulaDraft.size) formulaSizeEl.value = lastAiFormulaDraft.size;
          if (formulaReadableEl) {{
            formulaReadableEl.innerHTML = `AI-черновик: <b>${{safeText(lastAiFormulaDraft.title || 'показатель')}}</b>. ${{safeText(lastAiFormulaDraft.explanation || '')}}`;
          }}
          if (formulaResultEl && lastFormulaResult) {{
            formulaResultEl.innerHTML = renderFormulaResultHtml(lastFormulaResult, lastAiFormulaDraft.view || 'table', {{}}, '', lastAiFormulaDraft.formula || null) + renderFormulaExplanationHtml(lastFormulaResult, lastFormulaDiagnostics) + renderFormulaDiagnosticsHtml(lastFormulaDiagnostics);
          }}
          formulaStatusEl.textContent = 'AI-черновик применен. Можно отправить показатель на дашборд.';
        }};
        const saveFormulaWidget = async () => {{
          if (!formulaSaveBtn) return;
          formulaSaveBtn.disabled = true;
          if (formulaStatusEl) {{
            formulaStatusEl.classList.remove('error');
            formulaStatusEl.textContent = 'Сохраняю показатель на дашборд...';
          }}
          try {{
            const formula = parseFormulaEditor();
            const title = currentFormulaTitle() || suggestedFormulaTitle();
            setFormulaTitle(title, {{ force: true }});
            if (!lastFormulaResult) await runFormula();
            const response = await fetch(apiUrl('/api/dashboard-widgets'), {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{
                title,
                widget_type: 'formula',
                view: lastAiFormulaDraft?.view || 'table',
                size: formulaSizeEl?.value || 'medium',
                page_id: selectedWidgetPageId(),
                formula: 'none',
                formula_spec: formula,
                query: {{}},
                settings: {{}},
                table_settings: lastAiFormulaDraft?.table_settings || {{}},
              }}),
            }});
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || 'save failed');
            if (formulaStatusEl) {{
              formulaStatusEl.textContent = 'Показатель сохранен. Считаю результат для дашборда...';
            }}
            await refreshDashboardWidgetResults();
            if (formulaStatusEl) {{
              formulaStatusEl.innerHTML = `Показатель добавлен и рассчитан. <a href="${{apiUrl('/dashboard')}}">Открыть дашборд</a>`;
            }}
            await loadSavedDashboard(false, false, true);
          }} catch (error) {{
            if (formulaStatusEl) {{
              formulaStatusEl.textContent = 'Не удалось сохранить: ' + error.message;
              formulaStatusEl.classList.add('error');
            }}
          }} finally {{
            formulaSaveBtn.disabled = false;
          }}
        }};
        formulaTemplateBtns.forEach((button) => {{
          button.addEventListener('click', () => setFormulaTemplate(button.dataset.formulaTemplate));
        }});
        syncFormulaTitleInputs(formulaTitleEl, formulaPreviewTitleEl);
        syncFormulaTitleInputs(formulaPreviewTitleEl, formulaTitleEl);
        formulaSourceEl?.addEventListener('change', () => {{
          aiFormulaPinned = false;
          syncFormulaEditorFromMask();
        }});
        formulaEntityEl?.addEventListener('change', () => {{
          aiFormulaPinned = false;
          refreshFormulaFieldControls();
          syncFormulaEditorFromMask();
        }});
        formulaOpEl?.addEventListener('change', () => {{
          aiFormulaPinned = false;
          syncFormulaEditorFromMask();
        }});
        formulaValueFieldEl?.addEventListener('change', () => {{
          aiFormulaPinned = false;
          syncFormulaEditorFromMask();
        }});
        formulaGroupFieldEl?.addEventListener('change', () => {{
          aiFormulaPinned = false;
          syncFormulaEditorFromMask();
        }});
        refreshFormulaFilterRows().forEach(bindFormulaFilterRow);
        formulaFilterAddBtn?.addEventListener('click', () => {{
          aiFormulaPinned = false;
          addFormulaFilterRow();
          syncFormulaEditorFromMask();
        }});
        formulaRunBtn?.addEventListener('click', runFormula);
        formulaSaveBtn?.addEventListener('click', saveFormulaWidget);
        aiFormulaRunBtn?.addEventListener('click', runAiFormulaDraft);
        aiFormulaApplyBtn?.addEventListener('click', applyAiFormulaDraft);
        amoFilterParseBtn?.addEventListener('click', parseAmoFilterUrl);
        amoFilterApplyBtn?.addEventListener('click', applyAmoFilterImport);
        aiFormulaResultEl?.addEventListener('click', (event) => {{
          const quick = event.target.closest('[data-ai-question-quick]');
          if (quick) {{
            const row = quick.closest('[data-ai-question-row]');
            const input = row?.querySelector('[data-ai-question-answer]');
            if (input) input.value = quick.dataset.aiQuestionQuick || '';
            return;
          }}
          if (event.target.closest('[data-ai-question-submit]')) {{
            rerunAiFormulaWithAnswers();
          }}
        }});
        if (formulaEditorEl) {{
          loadFormulaDictionary();
        }}
        const renderReportRows = (rows) => {{
          if (!rows.length) {{
            reportResultEl.innerHTML = '<div class="report-empty">Нет строк под выбранные условия</div>';
            return;
          }}
          const view = reportViewEl?.value || 'table';
          reportResultEl.innerHTML = renderVisualHtml(view, rows, primaryMetric({{ query: {{ metrics: selectedReportMetrics() }} }}));
        }};
        const renderReportNumbers = (rows, metrics) => {{
          const row = rows[0] || {{}};
          const labels = {{
            count: 'Количество',
            sum_price: 'Сумма',
            avg_price: 'Средний чек',
            open_count: 'Открыто',
            won_count: 'Успешно',
            lost_count: 'Потеряно',
          }};
          const formula = calculateFormula(widgetFormulaEl?.value || 'none', row, widgetSettingsPayload(), metrics[0] || 'count');
          const visibleMetrics = (widgetFormulaEl?.value || 'none') === 'plan_fact' ? metrics.slice(1) : metrics;
          reportResultEl.innerHTML = `
            <div class="number-grid">
              ${{formula ? renderMetricCard(formula.label, formula.value, formula.suffix) : ''}}
              ${{formula?.extra ? formula.extra.map((item) => renderMetricCard(item.label, item.value, item.suffix)).join('') : ''}}
              ${{visibleMetrics.map((metric) => `
                <article class="number-card">
                  <span>${{labels[metric] || metric}}</span>
                  <strong>${{row[metric] ?? 0}}</strong>
                </article>
              `).join('')}}
            </div>
          `;
        }};
        const buildReportPayload = () => {{
          const metrics = selectedReportMetrics();
          const formula = widgetFormulaEl?.value || 'none';
          if (formula !== 'none') {{
            (formulaRequirements[formula] || []).forEach((metric) => {{
              if (!metrics.includes(metric)) metrics.push(metric);
            }});
          }}
          const filters = reportConditionRows
            .map((row) => {{
              const field = row.querySelector('[data-report-filter-field]').value;
              const op = row.querySelector('[data-report-filter-op]').value;
              const valueType = row.querySelector('[data-report-value-type]')?.value || 'auto';
              const raw = row.querySelector('[data-report-filter-value]').value.trim();
              const valueOptional = ['this_month', 'previous_month', 'this_week', 'previous_week'].includes(op);
              if (!field || (!raw && !valueOptional)) return null;
              return {{ field, op, value_type: valueType, value: parseReportValue(op, raw, valueType) }};
            }})
            .filter(Boolean);
          const view = reportViewEl?.value || 'number';
          const isNumberView = view === 'number';
          const groupField = reportGroupEl?.value || 'pipeline_id';
          const sourceId = Number(reportSourceEl?.value || 0) || null;
          return {{
            view,
            formula,
            settings: widgetSettingsPayload(),
            query: {{
              entity: 'leads',
              source_id: sourceId,
              metrics,
              group_by: isNumberView ? [] : [groupField],
              filters,
              filter_logic: reportLogicEl?.value || 'and',
              order_by: view === 'line' ? groupField : (metrics[0] || 'count'),
              order_dir: view === 'line' ? 'asc' : 'desc',
              limit: isNumberView ? 1 : (view === 'line' ? 24 : 50),
            }},
          }};
        }};
        const renderWidgetContent = (container, widget, rows) => {{
          const body = document.createElement('div');
          container.innerHTML = `<h3>${{String(widget.title || 'Показатель').replace(/[&<>"']/g, '')}}</h3>`;
          container.appendChild(body);
          const previousTarget = reportResultEl.innerHTML;
          const originalTarget = reportResultEl;
          if (widget.view === 'number') {{
            const row = rows[0] || {{}};
            const metrics = widget.query.metrics || [];
            body.innerHTML = `
              <div class="number-grid">
                ${{metrics.map((metric) => `
                  <article class="number-card">
                    <span>${{metric}}</span>
                    <strong>${{row[metric] ?? 0}}</strong>
                  </article>
                `).join('')}}
              </div>
            `;
          }} else {{
            if (!rows.length) {{
              body.innerHTML = '<div class="report-empty">Нет данных</div>';
              return;
            }}
            const columns = Object.keys(rows[0]);
            body.innerHTML = `
              <div class="report-table-wrap">
                <table>
                  <thead><tr>${{columns.map((column) => `<th>${{column}}</th>`).join('')}}</tr></thead>
                  <tbody>${{rows.map((row) => `<tr>${{columns.map((column) => `<td>${{row[column] ?? ''}}</td>`).join('')}}</tr>`).join('')}}</tbody>
                </table>
              </div>
            `;
          }}
        }};
        const formulaSourceId = (node) => {{
          if (!node || typeof node !== 'object') return null;
          if (Number(node.source_id || 0)) return Number(node.source_id);
          for (const value of Object.values(node)) {{
            if (Array.isArray(value)) {{
              for (const item of value) {{
                const found = formulaSourceId(item);
                if (found) return found;
              }}
            }} else if (value && typeof value === 'object') {{
              const found = formulaSourceId(value);
              if (found) return found;
            }}
          }}
          return null;
        }};
        const widgetSizeOptions = [
          ['small', 'Маленький'],
          ['medium', 'Средний'],
          ['large', 'Большой'],
          ['wide', 'На всю ширину'],
        ];
        const widgetFontScaleOptions = [
          ['auto', 'Авто'],
          ['0.86', 'Мелкий'],
          ['1', 'Обычный'],
          ['1.14', 'Крупный'],
          ['1.28', 'Очень крупный'],
        ];
        const widgetSizeToColumns = {{ small: 3, medium: 6, large: 8, wide: 12 }};
        const widgetColumnsToSize = (columns) => {{
          if (columns <= 3) return 'small';
          if (columns <= 6) return 'medium';
          if (columns <= 9) return 'large';
          return 'wide';
        }};
        const widgetLayoutColumns = (widget) => {{
          const explicit = Number(widget.layout?.w || 0);
          if (Number.isFinite(explicit) && explicit > 0) return Math.max(1, Math.min(12, Math.round(explicit)));
          return widgetSizeToColumns[widget.size || 'medium'] || 6;
        }};
        const widgetFontScale = (widget) => {{
          const raw = String(widget.table_settings?.font_scale || 'auto');
          const manual = Number(raw);
          if (raw !== 'auto' && Number.isFinite(manual) && manual > 0) return Math.max(0.75, Math.min(1.4, manual));
          const columns = widgetLayoutColumns(widget);
          const height = Number(widget.layout?.height || 0);
          return autoWidgetFontScaleFor(columns, height);
        }};
        const autoWidgetFontScaleFor = (columns, height) => {{
          if (columns <= 3 || (height > 0 && height < 230)) return 0.88;
          if (columns >= 10 && height >= 420) return 1.12;
          if (columns >= 8 && height >= 320) return 1.05;
          return 1;
        }};
        const widgetById = (widgetId) => savedWidgetsCache.find((widget) => String(widget.id) === String(widgetId));
        const optionHtml = (value, label, selectedValue) => (
          `<option value="${{safeText(value)}}" ${{String(value) === String(selectedValue || '') ? 'selected' : ''}}>${{safeText(label)}}</option>`
        );
        const widgetPageOptions = (selectedValue) => dashboardPagesCache
          .map((page) => optionHtml(page.id, page.name, selectedValue || 'main'))
          .join('');
        const renderWidgetMenu = (widget, isSettingsOpen = false, isDetailsOpen = false) => `
          <div class="widget-menu-wrap">
            <button class="icon-button ${{isSettingsOpen || isDetailsOpen ? 'active' : ''}}" type="button" title="Действия" data-widget-action="menu" data-widget-id="${{safeText(widget.id || '')}}">⋯</button>
            <div class="widget-menu" role="menu">
              <button type="button" data-widget-action="details" data-widget-id="${{safeText(widget.id || '')}}"><span>${{isDetailsOpen ? 'Скрыть описание' : 'Описание'}}</span><span>i</span></button>
              <button type="button" data-widget-action="settings" data-widget-id="${{safeText(widget.id || '')}}"><span>${{isSettingsOpen ? 'Скрыть настройки' : 'Настройки'}}</span><span>⚙</span></button>
              <button type="button" data-widget-action="refresh" data-widget-id="${{safeText(widget.id || '')}}"><span>Обновить</span><span>↻</span></button>
              <button type="button" data-widget-action="duplicate" data-widget-id="${{safeText(widget.id || '')}}"><span>Дублировать</span><span>⧉</span></button>
              <button class="danger" type="button" data-widget-action="delete" data-widget-id="${{safeText(widget.id || '')}}"><span>Удалить</span><span>×</span></button>
            </div>
          </div>
        `;
        const renderWidgetMetaPanel = (widget, subtitle) => {{
          const view = widget.view ? ` · Вид: ${{widget.view}}` : '';
          const size = widget.size ? ` · Размер: ${{widget.size}}` : '';
          const formulaLabel = widget.widget_type === 'formula' || widget.formula_spec ? 'Формула показателя' : (widget.formula || 'Без формулы');
          return `
            <div class="widget-meta-panel" data-widget-meta-panel>
              <div><strong>Источник и актуальность:</strong> ${{safeText(subtitle || 'не указано')}}</div>
              <div><strong>Расчет:</strong> ${{safeText(formulaLabel)}}${{safeText(view)}}${{safeText(size)}}</div>
            </div>
          `;
        }};
        const renderWidgetControlPanel = (widget, resultOrRows) => {{
          const isFormula = widget.widget_type === 'formula' || widget.formula_spec;
          const fontScale = String(widget.table_settings?.font_scale || 'auto');
          if (!isFormula) {{
            return `
              <div class="widget-control-panel compact" data-widget-settings-panel>
                <label>
                  Название
                  <input data-widget-setting="title" value="${{safeText(widget.title || '')}}" placeholder="Название виджета">
                </label>
                <label>
                  Лист
                  <select data-widget-setting="page_id">
                    ${{widgetPageOptions(widget.page_id || 'main')}}
                  </select>
                </label>
                <label>
                  Размер
                  <select data-widget-setting="size">
                    ${{widgetSizeOptions.map(([value, label]) => optionHtml(value, label, widget.size || 'medium')).join('')}}
                  </select>
                </label>
                <label>
                  Размер текста
                  <select data-widget-setting="font_scale">
                    ${{widgetFontScaleOptions.map(([value, label]) => optionHtml(value, label, fontScale)).join('')}}
                  </select>
                </label>
                <div class="panel-actions">
                  <button type="button" data-widget-action="close-settings" data-widget-id="${{safeText(widget.id || '')}}">Готово</button>
                </div>
              </div>
            `;
          }}
          const result = isFormula ? resultOrRows : null;
          const rows = isFormula
            ? (Array.isArray(result?.rows) ? result.rows : [])
            : (Array.isArray(resultOrRows) ? resultOrRows : []);
          const columns = isFormula ? formulaTableColumns(result || {{ kind: 'table', rows }}, rows) : visibleColumns(Object.keys(rows[0] || {{}}));
          const settings = normalizeTableSettings(widget.table_settings || {{}}, columns);
          const sortOptions = ['', tableLabelColumn].concat(columns);
          const zeroOptions = [''].concat(columns);
          const orderedPanelColumns = settings.visible_columns.length
            ? settings.visible_columns.concat(columns.filter((column) => !settings.visible_columns.includes(column)))
            : columns;
          const hiddenPanelColumns = new Set(settings.hidden_columns);
          const columnsBlock = columns.length > 1 ? `
              <div class="widget-columns-block wide-field" data-widget-columns-block>
                <span class="widget-columns-title">Колонки: порядок и видимость</span>
                <div class="widget-columns-list">
                  <div class="widget-column-row" data-widget-column="${{safeText(tableLabelColumn)}}" data-widget-column-fixed>
                    <label class="widget-column-toggle" title="Колонка группировки — всегда видима и всегда первая">
                      <input type="checkbox" checked disabled>
                      <span class="widget-column-name">${{safeText(settings.column_titles[tableLabelColumn] || 'Строка')}}</span>
                    </label>
                    <input type="text" class="widget-column-title-input" data-widget-column-title
                      value="${{safeText(settings.column_titles[tableLabelColumn] || '')}}"
                      placeholder="Строка" title="Своё название (пусто — «Строка»)">
                    <input type="number" class="widget-column-width-input" data-widget-column-width
                      value="${{safeText(settings.column_widths[tableLabelColumn] || '')}}"
                      placeholder="авто" min="60" max="600" step="10" title="Ширина колонки, px">
                    <div class="widget-column-move">
                      <button type="button" title="Колонка группировки всегда первая" disabled>&#8593;</button>
                      <button type="button" title="Колонка группировки всегда первая" disabled>&#8595;</button>
                    </div>
                  </div>
                  ${{orderedPanelColumns.map((column, index) => `
                    <div class="widget-column-row" data-widget-column="${{safeText(column)}}">
                      <label class="widget-column-toggle" title="${{safeText(column)}}">
                        <input type="checkbox" data-widget-column-toggle ${{hiddenPanelColumns.has(column) ? '' : 'checked'}}>
                        <span class="widget-column-name">${{safeText(settings.column_titles[column] || column)}}</span>
                      </label>
                      <input type="text" class="widget-column-title-input" data-widget-column-title
                        value="${{safeText(settings.column_titles[column] || '')}}"
                        placeholder="${{safeText(column)}}" title="Своё название (пусто — оригинальное)">
                      <input type="number" class="widget-column-width-input" data-widget-column-width
                        value="${{safeText(settings.column_widths[column] || '')}}"
                        placeholder="авто" min="60" max="600" step="10" title="Ширина колонки, px">
                      <div class="widget-column-move">
                        <button type="button" data-widget-column-move="up" title="Выше" ${{index === 0 ? 'disabled' : ''}}>&#8593;</button>
                        <button type="button" data-widget-column-move="down" title="Ниже" ${{index === orderedPanelColumns.length - 1 ? 'disabled' : ''}}>&#8595;</button>
                      </div>
                    </div>
                  `).join('')}}
                </div>
              </div>` : '';
          return `
            <div class="widget-control-panel" data-widget-settings-panel>
              <label class="wide-field">
                Название
                <input data-widget-setting="title" value="${{safeText(widget.title || '')}}" placeholder="Название виджета">
              </label>
              <label>
                Лист
                <select data-widget-setting="page_id">
                  ${{widgetPageOptions(widget.page_id || 'main')}}
                </select>
              </label>
              <label>
                Сортировать по
                <select data-widget-setting="sort_by">
                  ${{sortOptions.map((column) => optionHtml(column, tableColumnLabel(column, settings.column_titles), settings.sort_by)).join('')}}
                </select>
              </label>
              <label>
                Направление сортировки
                <select data-widget-setting="sort_dir">
                  ${{optionHtml('desc', 'По убыванию', settings.sort_dir)}}
                  ${{optionHtml('asc', 'По возрастанию', settings.sort_dir)}}
                </select>
              </label>
              <label>
                Скрывать нули по
                <select data-widget-setting="zero_column">
                  ${{zeroOptions.map((column) => optionHtml(column, column ? (settings.column_titles[column] || column) : 'Любой числовой колонке', settings.zero_column)).join('')}}
                </select>
              </label>
              <label>
                Показать строк
                <input type="number" min="0" max="500" step="1" value="${{safeText(settings.row_limit || '')}}" placeholder="Все" data-widget-setting="row_limit">
              </label>
              <label>
                Размер
                <select data-widget-setting="size">
                  ${{widgetSizeOptions.map(([value, label]) => optionHtml(value, label, widget.size || 'medium')).join('')}}
                </select>
              </label>
              <label>
                Размер текста
                <select data-widget-setting="font_scale">
                  ${{widgetFontScaleOptions.map(([value, label]) => optionHtml(value, label, fontScale)).join('')}}
                </select>
              </label>
              <label>
                Размер заголовков
                <select data-widget-setting="header_font_size">
                  ${{optionHtml('small', 'Мелкий', settings.header_font_size)}}
                  ${{optionHtml('normal', 'Обычный', settings.header_font_size)}}
                  ${{optionHtml('large', 'Крупный', settings.header_font_size)}}
                </select>
              </label>
              <label class="checkbox-control">
                <input type="checkbox" data-widget-setting="hide_zero_rows" ${{settings.hide_zero_rows ? 'checked' : ''}}>
                <span>Скрыть нулевые строки</span>
              </label>
              ${{columnsBlock}}
              <div class="panel-actions">
                <button type="button" data-widget-action="close-settings" data-widget-id="${{safeText(widget.id || '')}}">Готово</button>
              </div>
              <p class="widget-control-help">“Показать строк” — это число: 5, 10, 20. Пусто или 0 показывает все строки. Нулевые строки скрываются только когда включен чекбокс.</p>
            </div>
          `;
        }};
        const updateWidgetViewSettings = async (widgetId, patch, keepOpen = true) => {{
          const widget = widgetById(widgetId);
          if (!widget) return;
          const card = [...(savedDashboardEl?.querySelectorAll('.saved-widget') || [])]
            .find((item) => String(item.dataset.widgetId) === String(widgetId));
          const rows = card?.__widgetRows || [];
          const formulaResult = card?.__formulaResult || null;
          const resultMeta = card?.__widgetResultMeta || {{}};
          const nextWidgets = savedWidgetsCache.map((item) => {{
            if (String(item.id) !== String(widgetId)) return item;
            const next = {{ ...item }};
            if (patch.title !== undefined) next.title = String(patch.title || '').trim() || item.title || 'Показатель';
            if (patch.size) next.size = patch.size;
            if (patch.page_id) next.page_id = patch.page_id;
            next.table_settings = {{ ...(item.table_settings || {{}}), ...(patch.table_settings || {{}}) }};
            next.layout = {{ ...(item.layout || {{}}), ...(patch.layout || {{}}) }};
            return next;
          }});
          await saveDashboardWidgets(nextWidgets);
          const nextWidget = widgetById(widgetId);
          if (nextWidget && String(nextWidget.page_id || 'main') !== String(activeDashboardPageId || 'main')) {{
            await loadSavedDashboard(false);
            return;
          }}
          if (card && nextWidget) {{
            card.classList.remove('small', 'medium', 'large', 'wide');
            card.classList.add(nextWidget.size || 'medium');
            renderWidgetContentV2(card, nextWidget, rows, formulaResult, resultMeta);
            if (keepOpen) card.classList.add('settings-open');
          }} else {{
            await loadSavedDashboard(false);
          }}
        }};
        const renderWidgetContentV2 = (container, widget, rows, formulaResult = null, resultMeta = {{}}) => {{
          const body = document.createElement('div');
          body.className = 'widget-body';
          container.dataset.widgetId = widget.id || '';
          container.__widgetRows = rows;
          container.__formulaResult = formulaResult;
          container.__widgetResultMeta = resultMeta || {{}};
          container.draggable = false;
          const widgetHeight = Number(widget.layout?.height || 0);
          const widgetColumns = widgetLayoutColumns(widget);
          container.style.gridColumn = `span ${{widgetColumns}}`;
          container.style.height = widgetHeight > 0 ? `${{widgetHeight}}px` : '';
          container.style.setProperty('--widget-font-scale', String(widgetFontScale(widget)));
          const widgetSourceId = widget.query?.source_id || formulaSourceId(widget.formula_spec);
          const sourceLine = widget.widget_type === 'formula' ? 'Формула · ' + sourceSubtitle(widgetSourceId) : sourceSubtitle(widgetSourceId);
          const calculatedLine = resultMeta?.cached_at ? ` · рассчитано ${{formatDateTime(resultMeta.cached_at)}}` : '';
          const staleLine = resultMeta?.stale ? ' · обновляется в фоне' : '';
          const refreshLine = resultMeta?.auto_refreshed ? ' · автообновлено' : '';
          const widgetSubtitle = `${{sourceLine}}${{calculatedLine}}${{staleLine}}${{refreshLine}}`;
          const isSettingsOpen = container.classList.contains('settings-open');
          const isDetailsOpen = container.classList.contains('details-open');
          container.innerHTML = `
            <div class="saved-widget-header">
              <div class="saved-widget-title">
                <h3 title="${{safeText(widget.title || 'Показатель')}}">${{safeText(widget.title || 'Показатель')}}</h3>
              </div>
              <div class="widget-actions">
                <span class="icon-button widget-drag-handle" title="Перетащить виджет" data-widget-drag-handle draggable="true">↕</span>
                ${{renderWidgetMenu(widget, isSettingsOpen, isDetailsOpen)}}
              </div>
            </div>
            ${{renderWidgetMetaPanel(widget, widgetSubtitle)}}
            ${{renderWidgetControlPanel(widget, formulaResult || rows)}}
            <button class="widget-resize-grip right" type="button" title="Потяни вправо или влево, чтобы изменить ширину" data-widget-resize-grip data-resize-mode="width" aria-label="Изменить ширину"></button>
            <button class="widget-resize-grip bottom" type="button" title="Потяни вверх или вниз, чтобы изменить высоту" data-widget-resize-grip data-resize-mode="height" aria-label="Изменить высоту"></button>
            <button class="widget-resize-grip corner" type="button" title="Потяни по диагонали, чтобы изменить ширину и высоту" data-widget-resize-grip data-resize-mode="both" aria-label="Изменить размер по диагонали">↘</button>
          `;
          container.appendChild(body);
          if (widget.widget_type === 'formula' || widget.formula_spec) {{
            body.innerHTML = renderFormulaResultHtml(formulaResult || {{ kind: 'table', rows }}, widget.view || 'table', widget.table_settings || {{}}, widget.id || '', widget.formula_spec || null);
            return;
          }}
          if (widget.view === 'number') {{
            const row = rows[0] || {{}};
            const metrics = widget.query.metrics || [];
            const formula = calculateFormula(widget.formula, row, widget.settings || {{}}, metrics[0] || 'count');
            const visibleMetrics = widget.formula === 'plan_fact' ? metrics.slice(1) : metrics;
            body.innerHTML = `
              <div class="number-grid">
                ${{formula ? renderMetricCard(formula.label, formula.value, formula.suffix) : ''}}
                ${{formula?.extra ? formula.extra.map((item) => renderMetricCard(item.label, item.value, item.suffix)).join('') : ''}}
                ${{visibleMetrics.map((metric) => renderMetricCard(metricLabels[metric] || metric, row[metric] ?? 0)).join('')}}
              </div>
            `;
            return;
          }}
          if (!rows.length) {{
            body.innerHTML = '<div class="report-empty">Нет данных</div>';
            return;
          }}
          body.innerHTML = renderVisualHtml(widget.view, rows, primaryMetric(widget));
        }};
        const saveDashboardWidgets = async (widgets) => {{
          const response = await fetch(apiUrl('/api/dashboard-widgets'), {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ widgets }}),
          }});
          const data = await response.json();
          if (!response.ok || !data.ok) throw new Error(data.error || 'widgets save failed');
          savedWidgetsCache = data.widgets || [];
          return savedWidgetsCache;
        }};
        const deleteWidget = async (widgetId) => {{
          if (!widgetId) return;
          savedDashboardEl.innerHTML = '<div class="report-empty">Удаляю показатель...</div>';
          await saveDashboardWidgets(savedWidgetsCache.filter((widget) => widget.id !== widgetId));
          await loadSavedDashboard();
        }};
        const duplicateWidget = async (widgetId) => {{
          const source = savedWidgetsCache.find((widget) => widget.id === widgetId);
          if (!source) return;
          savedDashboardEl.innerHTML = '<div class="report-empty">Дублирую показатель...</div>';
          const copy = {{
            ...source,
            id: undefined,
            title: `${{source.title || 'Показатель'}} копия`,
          }};
          await saveDashboardWidgets([...savedWidgetsCache, copy]);
          await loadSavedDashboard();
        }};
        const loadSavedDashboard = async (forceRefresh = false, showLoader = true, cacheOnly = !forceRefresh) => {{
          if (!savedDashboardEl) return;
          if (showLoader || !savedDashboardEl.querySelector('.saved-widget')) {{
            savedDashboardEl.innerHTML = `<div class="report-empty">${{forceRefresh ? 'Обновляю кэш виджетов...' : 'Загружаю сохраненные показатели...'}}</div>`;
          }}
          const requestParams = forceRefresh ? {{ refresh: '1' }} : (cacheOnly ? {{ cache_only: '1' }} : {{}});
          const response = await fetch(apiUrl('/api/dashboard-widget-results', requestParams));
          const data = await response.json();
          savedWidgetsCache = data.widgets || [];
          dashboardPagesCache = normalizeDashboardPages(data.pages);
          renderDashboardPages();
          if (!response.ok || !data.ok || !savedWidgetsCache.length) {{
            savedDashboardEl.innerHTML = `
              <div class="report-empty" style="grid-column: 1 / -1;">
                <strong>Пока нет сохраненных показателей</strong>
                Открой <a href="${{apiUrl('/settings')}}">настройки</a>, собери отчет и нажми “Добавить на дашборд”.
                <div class="actions" style="margin-top: 14px;">
                  <button type="button" data-widget-action="create-starter">Создать базовый дашборд</button>
                </div>
              </div>
            `;
            return;
          }}
          const visibleWidgets = savedWidgetsCache.filter((widget) => String(widget.page_id || 'main') === String(activeDashboardPageId || 'main'));
          if (!visibleWidgets.length) {{
            savedDashboardEl.innerHTML = `
              <div class="report-empty" style="grid-column: 1 / -1;">
                <strong>${{safeText(pageNameById(activeDashboardPageId))}}</strong>
                На этом листе пока нет показателей. Открой конструктор, выбери этот лист и отправь сюда виджет.
              </div>
            `;
            return;
          }}
          savedDashboardEl.innerHTML = '';
          for (const widget of visibleWidgets) {{
            const card = document.createElement('article');
            card.className = `saved-widget ${{widget.size || 'medium'}}`;
            card.innerHTML = '<div class="report-empty">Загружаю сохраненный результат...</div>';
            savedDashboardEl.appendChild(card);
            const result = data.results?.[widget.id];
            if (result?.ok) {{
              renderWidgetContentV2(card, widget, result.rows || [], result.formula_result || null, result);
            }} else {{
              const message = result?.error || 'нет сохраненного результата';
              card.innerHTML = `
                <div class="saved-widget-header">
                  <div class="saved-widget-title">
                    <h3 title="${{safeText(widget.title || 'Показатель')}}">${{safeText(widget.title || 'Показатель')}}</h3>
                  </div>
                  <div class="widget-actions">
                    ${{renderWidgetMenu(widget)}}
                  </div>
                </div>
                ${{renderWidgetMetaPanel(widget, sourceSubtitle(widget.query?.source_id))}}
                <div class="widget-body"><div class="report-empty">Ошибка: ${{safeText(message)}}</div></div>
              `;
            }}
          }}
          if (data.refresh_pending && cacheOnly) {{
            window.setTimeout(() => {{
              if (document.hidden || resizingWidget || savedDashboardEl.querySelector('.settings-open')) return;
              loadSavedDashboard(false, false, true);
            }}, 6000);
          }}
        }};
        const collectWidgetColumnSettings = (panel) => {{
          const visible = [];
          const hidden = [];
          const columnTitles = {{}};
          const columnWidths = {{}};
          panel.querySelectorAll('[data-widget-column]').forEach((row) => {{
            const name = row.dataset.widgetColumn;
            if (!name) return;
            if (!row.hasAttribute('data-widget-column-fixed')) {{
              // Колонка группировки в visible/hidden не участвует — только
              // имя и ширина.
              const checked = row.querySelector('[data-widget-column-toggle]')?.checked;
              (checked ? visible : hidden).push(name);
            }}
            const title = String(row.querySelector('[data-widget-column-title]')?.value || '').trim();
            if (title) columnTitles[name] = title;
            const width = Number(row.querySelector('[data-widget-column-width]')?.value || 0);
            if (Number.isFinite(width) && width > 0) columnWidths[name] = Math.max(60, Math.min(600, Math.round(width)));
          }});
          // Объекты пишутся ЦЕЛИКОМ: merge table_settings поверхностный, частичная
          // запись затёрла бы переименования соседних колонок.
          return {{ visible_columns: visible, hidden_columns: hidden, column_titles: columnTitles, column_widths: columnWidths }};
        }};
        let widgetColumnFieldTimer = null;
        const persistWidgetColumnSettings = async (panel, widgetId, focusRef = null) => {{
          const patch = {{ table_settings: collectWidgetColumnSettings(panel) }};
          try {{
            await updateWidgetViewSettings(widgetId, patch);
          }} catch (error) {{
            savedDashboardEl.innerHTML = `<div class="report-empty">Ошибка настроек виджета: ${{safeText(error.message)}}</div>`;
            return;
          }}
          if (!focusRef?.column) return;
          // Ре-рендер пересоздал панель — возвращаем фокус в поле, где печатали.
          const card = [...(savedDashboardEl?.querySelectorAll('.saved-widget') || [])]
            .find((item) => String(item.dataset.widgetId) === String(focusRef.widgetId));
          const input = card?.querySelector(`[data-widget-column="${{CSS.escape(focusRef.column)}}"] [${{focusRef.attr}}]`);
          if (input) {{
            input.focus();
            try {{
              if (focusRef.caret !== null && focusRef.caret !== undefined) input.setSelectionRange(focusRef.caret, focusRef.caret);
            }} catch (error) {{ /* type=number не поддерживает каретку */ }}
          }}
        }};
        savedDashboardEl?.addEventListener('click', async (event) => {{
          const moveButton = event.target.closest('[data-widget-column-move]');
          if (moveButton) {{
            if (!dashboardEditMode()) return;
            const row = moveButton.closest('[data-widget-column]');
            const panel = moveButton.closest('[data-widget-settings-panel]');
            const moveWidgetId = moveButton.closest('.saved-widget')?.dataset.widgetId;
            if (!row || !panel || !moveWidgetId) return;
            const sibling = moveButton.dataset.widgetColumnMove === 'up'
              ? row.previousElementSibling
              : row.nextElementSibling;
            if (!sibling || !sibling.hasAttribute('data-widget-column') || sibling.hasAttribute('data-widget-column-fixed')) return;
            // Переставляем строку в DOM и сохраняем порядок целиком —
            // updateWidgetViewSettings перерисует виджет и панель.
            if (moveButton.dataset.widgetColumnMove === 'up') sibling.before(row); else sibling.after(row);
            await persistWidgetColumnSettings(panel, moveWidgetId);
            return;
          }}
          const button = event.target.closest('[data-widget-action]');
          if (!button) {{
            if (!event.target.closest('.widget-menu-wrap')) {{
              savedDashboardEl.querySelectorAll('.saved-widget.menu-open').forEach((item) => item.classList.remove('menu-open'));
            }}
            if (!event.target.closest('.saved-widget')) {{
              savedDashboardEl.querySelectorAll('.saved-widget.settings-open, .saved-widget.details-open').forEach((item) => item.classList.remove('settings-open', 'details-open'));
            }}
            return;
          }}
          const action = button.dataset.widgetAction;
          const widgetId = button.dataset.widgetId;
          const card = button.closest('.saved-widget');
          const editOnlyActions = new Set(['menu', 'settings', 'close-settings', 'details', 'delete', 'duplicate']);
          if (editOnlyActions.has(action) && !dashboardEditMode()) return;
          try {{
            if (action === 'menu') {{
              const isOpen = card?.classList.contains('menu-open');
              savedDashboardEl.querySelectorAll('.saved-widget.menu-open').forEach((item) => {{
                if (item !== card) item.classList.remove('menu-open');
              }});
              card?.classList.toggle('menu-open', !isOpen);
              return;
            }}
            if (action !== 'menu') card?.classList.remove('menu-open');
            if (action === 'refresh') await loadSavedDashboard(true);
            if (action === 'settings') {{
              card?.classList.toggle('settings-open');
            }}
            if (action === 'close-settings') {{
              card?.classList.remove('settings-open', 'details-open');
            }}
            if (action === 'details') {{
              card?.classList.toggle('details-open');
            }}
            if (action === 'create-starter') {{
              savedDashboardEl.innerHTML = '<div class="report-empty">Создаю базовый дашборд...</div>';
              await saveDashboardWidgets(starterWidgets());
              await loadSavedDashboard();
            }}
            if (action === 'delete') await deleteWidget(widgetId);
            if (action === 'duplicate') await duplicateWidget(widgetId);
          }} catch (error) {{
            savedDashboardEl.innerHTML = `<div class="report-empty">Ошибка управления виджетом: ${{safeText(error.message)}}</div>`;
          }}
        }});
        document.addEventListener('click', (event) => {{
          if (!savedDashboardEl || savedDashboardEl.contains(event.target)) return;
          savedDashboardEl.querySelectorAll('.saved-widget.menu-open, .saved-widget.settings-open, .saved-widget.details-open').forEach((item) => {{
            item.classList.remove('menu-open', 'settings-open', 'details-open');
          }});
        }});
        savedDashboardEl?.addEventListener('input', (event) => {{
          const field = event.target.closest('[data-widget-column-title], [data-widget-column-width]');
          if (!field) return;
          if (!dashboardEditMode()) return;
          const panel = field.closest('[data-widget-settings-panel]');
          const widgetId = field.closest('.saved-widget')?.dataset.widgetId;
          if (!panel || !widgetId) return;
          let caret = null;
          try {{
            caret = field.selectionStart;
          }} catch (error) {{ /* type=number */ }}
          const focusRef = {{
            widgetId,
            column: field.closest('[data-widget-column]')?.dataset.widgetColumn,
            attr: field.hasAttribute('data-widget-column-title') ? 'data-widget-column-title' : 'data-widget-column-width',
            caret,
          }};
          window.clearTimeout(widgetColumnFieldTimer);
          widgetColumnFieldTimer = window.setTimeout(() => {{
            persistWidgetColumnSettings(panel, widgetId, focusRef);
          }}, 300);
        }});
        savedDashboardEl?.addEventListener('change', async (event) => {{
          const columnToggle = event.target.closest('[data-widget-column-toggle]');
          if (columnToggle) {{
            if (!dashboardEditMode()) return;
            const panel = columnToggle.closest('[data-widget-settings-panel]');
            const toggleWidgetId = columnToggle.closest('.saved-widget')?.dataset.widgetId;
            if (!panel || !toggleWidgetId) return;
            if (!collectWidgetColumnSettings(panel).visible_columns.length) {{
              // Последнюю видимую колонку скрыть нельзя — откатываем чекбокс.
              columnToggle.checked = true;
              return;
            }}
            await persistWidgetColumnSettings(panel, toggleWidgetId);
            return;
          }}
          const control = event.target.closest('[data-widget-setting]');
          if (!control) return;
          if (!dashboardEditMode()) return;
          const card = control.closest('.saved-widget');
          const widgetId = card?.dataset.widgetId;
          if (!widgetId) return;
          const key = control.dataset.widgetSetting;
          const value = control.type === 'checkbox' ? control.checked : control.value;
          const patch = key === 'title'
            ? {{ title: value }}
            : key === 'size'
              ? {{ size: value }}
              : key === 'page_id'
                ? {{ page_id: value }}
                : {{ table_settings: {{ [key]: key === 'row_limit' ? Number(value || 0) : value }} }};
          try {{
            await updateWidgetViewSettings(widgetId, patch);
          }} catch (error) {{
            savedDashboardEl.innerHTML = `<div class="report-empty">Ошибка настроек виджета: ${{safeText(error.message)}}</div>`;
          }}
        }});
        savedDashboardEl?.addEventListener('dragstart', (event) => {{
          if (!dashboardEditMode()) {{
            event.preventDefault();
            return;
          }}
          const handle = event.target.closest('[data-widget-drag-handle]');
          const card = handle?.closest('.saved-widget');
          if (!card) {{
            event.preventDefault();
            return;
          }}
          draggedWidgetId = card.dataset.widgetId || null;
          card.classList.add('dragging');
          event.dataTransfer.effectAllowed = 'move';
          event.dataTransfer.setData('text/plain', draggedWidgetId || '');
        }});
        savedDashboardEl?.addEventListener('dragend', () => {{
          savedDashboardEl.querySelectorAll('.saved-widget').forEach((card) => card.classList.remove('dragging', 'drag-over'));
          draggedWidgetId = null;
        }});
        savedDashboardEl?.addEventListener('dragover', (event) => {{
          if (!dashboardEditMode()) return;
          const card = event.target.closest('.saved-widget');
          if (!card || !draggedWidgetId || card.dataset.widgetId === draggedWidgetId) return;
          event.preventDefault();
          card.classList.add('drag-over');
        }});
        savedDashboardEl?.addEventListener('dragleave', (event) => {{
          const card = event.target.closest('.saved-widget');
          card?.classList.remove('drag-over');
        }});
        savedDashboardEl?.addEventListener('drop', async (event) => {{
          if (!dashboardEditMode()) return;
          const targetCard = event.target.closest('.saved-widget');
          if (!targetCard || !draggedWidgetId) return;
          event.preventDefault();
          targetCard.classList.remove('drag-over');
          const targetId = targetCard.dataset.widgetId;
          if (!targetId || targetId === draggedWidgetId) return;
          const sourceIndex = savedWidgetsCache.findIndex((widget) => String(widget.id) === String(draggedWidgetId));
          const targetIndex = savedWidgetsCache.findIndex((widget) => String(widget.id) === String(targetId));
          if (sourceIndex < 0 || targetIndex < 0) return;
          const nextWidgets = [...savedWidgetsCache];
          const [moved] = nextWidgets.splice(sourceIndex, 1);
          nextWidgets.splice(targetIndex, 0, moved);
          try {{
            await saveDashboardWidgets(nextWidgets);
            await loadSavedDashboard(false);
          }} catch (error) {{
            savedDashboardEl.innerHTML = `<div class="report-empty">Ошибка перемещения виджета: ${{safeText(error.message)}}</div>`;
          }}
        }});
        savedDashboardEl?.addEventListener('mousedown', (event) => {{
          const grip = event.target.closest('[data-widget-resize-grip]');
          if (!grip) return;
          if (!dashboardEditMode()) return;
          const card = grip.closest('.saved-widget');
          const widget = widgetById(card?.dataset.widgetId);
          if (!card || !widget) return;
          event.preventDefault();
          const rect = card.getBoundingClientRect();
          const gridRect = savedDashboardEl.getBoundingClientRect();
          const gridStyles = window.getComputedStyle(savedDashboardEl);
          const gap = parseFloat(gridStyles.columnGap || gridStyles.gap || '0') || 0;
          const columnWidth = Math.max(48, (gridRect.width - gap * 11) / 12);
          resizingWidget = {{
            widgetId: widget.id,
            card,
            mode: grip.dataset.resizeMode || 'both',
            autoFont: String(widget.table_settings?.font_scale || 'auto') === 'auto',
            startX: event.clientX,
            startY: event.clientY,
            startSize: widget.size || 'medium',
            startColumns: widgetLayoutColumns(widget),
            columnWidth,
            startHeight: rect.height,
          }};
          card.classList.add('resizing');
        }});
        window.addEventListener('mousemove', (event) => {{
          if (!resizingWidget) return;
          const mode = resizingWidget.mode;
          let liveHeight = resizingWidget.startHeight;
          let liveColumns = resizingWidget.startColumns;
          if (mode === 'height' || mode === 'both') {{
            const nextHeight = Math.max(170, Math.min(1000, resizingWidget.startHeight + event.clientY - resizingWidget.startY));
            liveHeight = Math.round(nextHeight);
            resizingWidget.card.style.height = `${{Math.round(nextHeight)}}px`;
          }}
          if (mode === 'width' || mode === 'both') {{
            const columnDelta = Math.round((event.clientX - resizingWidget.startX) / resizingWidget.columnWidth);
            const nextColumns = Math.max(1, Math.min(12, resizingWidget.startColumns + columnDelta));
            liveColumns = nextColumns;
            resizingWidget.card.style.gridColumn = `span ${{nextColumns}}`;
          }}
          if (resizingWidget.autoFont) {{
            resizingWidget.card.style.setProperty('--widget-font-scale', String(autoWidgetFontScaleFor(liveColumns, liveHeight)));
          }}
        }});
        window.addEventListener('mouseup', async (event) => {{
          if (!resizingWidget) return;
          const {{ widgetId, card, mode, startX, startY, startColumns, columnWidth, startHeight }} = resizingWidget;
          resizingWidget = null;
          savedDashboardEl?.querySelectorAll('.saved-widget').forEach((card) => card.classList.remove('resizing'));
          const delta = event.clientX - startX;
          const deltaY = event.clientY - startY;
          if (Math.abs(delta) < 20 && Math.abs(deltaY) < 18) {{
            card.style.height = `${{Math.round(startHeight)}}px`;
            card.style.gridColumn = `span ${{startColumns}}`;
            return;
          }}
          const columnDelta = Math.round(delta / columnWidth);
          const nextColumns = Math.max(1, Math.min(12, startColumns + columnDelta));
          const nextHeight = Math.max(170, Math.min(1000, Math.round(startHeight + deltaY)));
          const patch = {{
            layout: {{}},
          }};
          if (mode === 'height' || mode === 'both') patch.layout.height = nextHeight;
          if (mode === 'width' || mode === 'both') {{
            patch.layout.w = nextColumns;
            patch.size = widgetColumnsToSize(nextColumns);
          }}
          try {{
            await updateWidgetViewSettings(widgetId, patch, false);
          }} catch (error) {{
            savedDashboardEl.innerHTML = `<div class="report-empty">Ошибка изменения размера: ${{safeText(error.message)}}</div>`;
          }}
        }});
        saveWidgetBtn?.addEventListener('click', async () => {{
          const built = buildReportPayload();
          const title = widgetTitleEl.value.trim() || 'Новый показатель';
          const response = await fetch(apiUrl('/api/dashboard-widgets'), {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ title, view: built.view, size: widgetSizeEl?.value || 'medium', page_id: selectedWidgetPageId(), formula: built.formula, query: built.query, settings: built.settings }}),
          }});
          const data = await response.json();
          if (!response.ok || !data.ok) {{
            reportStatusEl.textContent = 'Ошибка сохранения виджета';
            reportStatusEl.classList.add('error');
            return;
          }}
          reportStatusEl.classList.remove('error');
          reportStatusEl.textContent = 'Показатель сохранен. Считаю результат для дашборда...';
          await refreshDashboardWidgetResults();
          reportStatusEl.innerHTML = `Показатель добавлен и рассчитан. <a href="${{apiUrl('/dashboard')}}">Открыть дашборд</a>`;
          await loadSavedDashboard(false, false, true);
        }});
        formulaCopyBtn?.addEventListener('click', async () => {{
          const built = buildReportPayload();
          const payload = {{
            title: widgetTitleEl?.value?.trim() || 'Новый показатель',
            view: built.view,
            size: widgetSizeEl?.value || 'medium',
            page_id: selectedWidgetPageId(),
            formula: built.formula,
            settings: built.settings,
            query: built.query,
          }};
          try {{
            await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
            reportStatusEl.classList.remove('error');
            reportStatusEl.textContent = 'Формула показателя скопирована. Можно вставить ее в заметки или использовать как шаблон.';
          }} catch (error) {{
            reportStatusEl.textContent = 'Не удалось скопировать формулу: ' + error.message;
            reportStatusEl.classList.add('error');
          }}
        }});
        reportBtn?.addEventListener('click', async () => {{
          const built = buildReportPayload();
          const payload = built.query;
          const metrics = payload.metrics;
          const isNumberView = built.view === 'number';
          reportBtn.disabled = true;
          reportStatusEl.textContent = 'Строю отчет...';
          try {{
            const response = await fetch(apiUrl('/api/analytics/query'), {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify(payload),
            }});
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || 'query failed');
            if (isNumberView) {{
              renderReportNumbers(data.result.rows, metrics);
            }} else {{
              renderReportRows(data.result.rows);
            }}
            if (data.freshness?.scope === 'source' && data.freshness?.source_id) {{
              const sourceId = Number(data.freshness.source_id);
              const source = syncSourcesIndex[sourceId] || {{}};
              syncSourcesIndex[sourceId] = {{
                ...source,
                count: Number(data.freshness.count || source.count || 0),
                fresh_at: data.freshness.fresh_at || source.fresh_at,
                checked_at: data.freshness.checked_at || source.checked_at,
                hub_fresh_at: data.freshness.hub_fresh_at || source.hub_fresh_at,
              }};
              updateReportSourceNote();
              renderWorkSources();
            }}
            const primaryMetric = metrics[0] || 'count';
            const primaryValue = data.result.rows?.[0]?.[primaryMetric];
            const readyPrefix = isNumberView && primaryMetric === 'count'
              ? `Массив: ${{formatNumber(primaryValue)}} сделок`
              : `Готово: ${{data.result.row_count}} строк`;
            const sourceStatus = data.freshness?.scope === 'source' ? ` · ${{sourceActualityLabel(data.freshness)}}` : '';
            reportStatusEl.textContent = `${{readyPrefix}} · ${{freshnessLabel(data.freshness?.fresh_at)}}${{sourceStatus}}`;
          }} catch (error) {{
            reportStatusEl.textContent = 'Ошибка: ' + error.message;
            reportStatusEl.classList.add('error');
          }} finally {{
            reportBtn.disabled = false;
          }}
        }});
        dashboardRefreshBtn?.addEventListener('click', async () => {{
          await loadSavedDashboard(true);
        }});
        dashboardEditToggleBtn?.addEventListener('click', () => {{
          setDashboardEditMode(!dashboardEditMode());
        }});
        dashboardPagesEl?.addEventListener('click', async (event) => {{
          const button = event.target.closest('[data-dashboard-page-id]');
          if (!button) return;
          await setActiveDashboardPage(button.dataset.dashboardPageId || 'main');
        }});
        dashboardPagePrevBtn?.addEventListener('click', async () => {{
          const index = dashboardPagesCache.findIndex((page) => page.id === activeDashboardPageId);
          const nextIndex = index <= 0 ? dashboardPagesCache.length - 1 : index - 1;
          await setActiveDashboardPage(dashboardPagesCache[nextIndex]?.id || 'main');
        }});
        dashboardPageNextBtn?.addEventListener('click', async () => {{
          const index = dashboardPagesCache.findIndex((page) => page.id === activeDashboardPageId);
          const nextIndex = index < 0 || index >= dashboardPagesCache.length - 1 ? 0 : index + 1;
          await setActiveDashboardPage(dashboardPagesCache[nextIndex]?.id || 'main');
        }});
        dashboardPageAddBtn?.addEventListener('click', async () => {{
          if (!dashboardEditMode()) return;
          const name = window.prompt('Название нового листа', 'Новый лист');
          if (!name || !name.trim()) return;
          const page = {{ id: makeDashboardPageId(name), name: name.trim() }};
          await saveDashboardPages([...dashboardPagesCache, page]);
          await setActiveDashboardPage(page.id);
        }});
        dashboardPageRenameBtn?.addEventListener('click', async () => {{
          if (!dashboardEditMode()) return;
          const page = dashboardPagesCache.find((item) => item.id === activeDashboardPageId);
          if (!page) return;
          const name = window.prompt('Новое название листа', page.name);
          if (!name || !name.trim()) return;
          await saveDashboardPages(dashboardPagesCache.map((item) => item.id === page.id ? {{ ...item, name: name.trim() }} : item));
        }});
        dashboardPageDeleteBtn?.addEventListener('click', async () => {{
          if (!dashboardEditMode()) return;
          if (activeDashboardPageId === 'main') {{
            window.alert('Основной лист удалить нельзя, его можно переименовать.');
            return;
          }}
          const page = dashboardPagesCache.find((item) => item.id === activeDashboardPageId);
          if (!page) return;
          if (!window.confirm(`Удалить лист "${{page.name}}"? Виджеты с него переедут на “Основной”.`)) return;
          await saveDashboardPages(dashboardPagesCache.filter((item) => item.id !== page.id));
          const movedWidgets = savedWidgetsCache.map((widget) => String(widget.page_id || 'main') === String(page.id) ? {{ ...widget, page_id: 'main' }} : widget);
          await saveDashboardWidgets(movedWidgets);
          await setActiveDashboardPage('main');
        }});
        let initialDashboardEditMode = false;
        try {{
          initialDashboardEditMode = window.localStorage.getItem(dashboardEditStorageKey) === '1';
        }} catch (error) {{}}
        try {{
          activeDashboardPageId = window.localStorage.getItem('amo-dashboard-active-page') || 'main';
        }} catch (error) {{}}
        setDashboardEditMode(initialDashboardEditMode);
        loadSavedDashboard();
        if (savedDashboardEl) {{
          window.setInterval(() => {{
            if (document.hidden || resizingWidget || savedDashboardEl.querySelector('.settings-open')) return;
            loadSavedDashboard(false, false, true);
          }}, 120000);
        }}
      </script>
    </body>
    </html>
    """


def _render_pipeline_sections(pipelines: list[dict[str, Any]]) -> str:
    if not pipelines:
        return """
        <article class="pipeline">
          <header>
            <h3>Нет данных по выбранному фильтру</h3>
            <div class="pipeline-total"><strong>0</strong></div>
          </header>
        </article>
        """

    rows = []
    for pipeline in pipelines:
        statuses = "".join(
            f"""
            <tr>
              <td class="status">{html.escape(status['status_name'])}</td>
              <td>{status['leads_count']}</td>
              <td>{status['open_count']}</td>
              <td>{status['won_count']}</td>
              <td>{status['lost_count']}</td>
              <td>{fmt_money(status['total_price'])}</td>
            </tr>
            """
            for status in pipeline["statuses"]
        )
        rows.append(f"""
        <article class="pipeline">
          <header>
            <h3>{html.escape(pipeline['pipeline_name'])}</h3>
            <div class="pipeline-total">
              <span>{pipeline['leads_count']} сделок</span>
              <strong>{fmt_money(pipeline['total_price'])}</strong>
            </div>
          </header>
          <table>
            <thead>
              <tr>
                <th>Этап</th>
                <th>Всего</th>
                <th>Открыто</th>
                <th>Успешно</th>
                <th>Потеряно</th>
                <th>Сумма</th>
              </tr>
            </thead>
            <tbody>{statuses}</tbody>
          </table>
        </article>
        """)
    return "".join(rows)


def _render_sync_controls(sync_result: list[dict[str, Any]] | None) -> str:
    options = "".join(
        f"""
        <label class="sync-option">
          <input type="checkbox" data-sync-entity value="{html.escape(entity)}" {'checked' if checked else ''}>
          <span>{html.escape(label)}</span>
        </label>
        """
        for entity, label, checked in SYNC_OPTIONS
    )
    result = ""
    if sync_result:
        rows = ", ".join(
            f"{html.escape(item['entity_type'])}: {item.get('items_count', 0)}"
            for item in sync_result
        )
        result = f'<div class="sync-result">Последняя синхронизация: {rows}</div>'
    return f"""
    <section class="tool-panel">
      <div class="eyebrow">Источник данных</div>
      <h2>Что тянуть из amoCRM</h2>
      <div class="sync-grid">{options}</div>
      <div class="actions">
        <button type="button" data-sync-button>Синхронизировать</button>
        <button type="button" class="secondary" data-select-core>Базовый набор</button>
        <button type="button" class="secondary" data-select-all>Выбрать все</button>
        <span class="sync-status" data-sync-status>Выбери сущности и запусти обновление</span>
      </div>
      {result}
    </section>
    """


def _render_analytics_filter(
    filter_options: list[dict[str, Any]],
    active_filter: dict[str, list[int]],
) -> str:
    if not filter_options:
        return ""

    selected_pipelines = {int(item) for item in active_filter.get("pipeline_ids", [])}
    selected_statuses = {int(item) for item in active_filter.get("status_ids", [])}
    all_enabled = not selected_pipelines and not selected_statuses
    preset_candidates = sorted(
        filter_options,
        key=lambda item: (
            "ремонт" not in str(item["pipeline_name"]).lower(),
            int(item.get("pipeline_sort") or 0),
            str(item["pipeline_name"]).lower(),
        ),
    )[:8]
    preset_buttons = "".join(
        f"""
        <button type="button" class="preset-button {'primary' if 'ремонт' in str(pipeline['pipeline_name']).lower() else ''}" data-preset-pipeline="{int(pipeline['pipeline_id'])}">
          {html.escape(str(pipeline['pipeline_name']))}
        </button>
        """
        for pipeline in preset_candidates
    )

    blocks = []
    for pipeline in filter_options:
        pipeline_id = int(pipeline["pipeline_id"])
        pipeline_checked = all_enabled or pipeline_id in selected_pipelines
        status_rows = []
        for status in pipeline["statuses"]:
            status_id = int(status["status_id"])
            status_checked = all_enabled or status_id in selected_statuses
            status_rows.append(f"""
              <label class="stage-option">
                <input type="checkbox" data-filter-status-id data-pipeline-id="{pipeline_id}" value="{status_id}" {'checked' if status_checked else ''}>
                <span>{html.escape(status['status_name'])}</span>
              </label>
            """)
        search_text = " ".join(
            [str(pipeline["pipeline_name"])]
            + [str(status["status_name"]) for status in pipeline["statuses"]]
        ).lower()
        blocks.append(f"""
          <details class="filter-pipeline" data-filter-block data-pipeline-id="{pipeline_id}" data-filter-text="{html.escape(search_text)}" {'open' if pipeline_checked else ''}>
            <summary>
              <label>
                <input type="checkbox" data-filter-pipeline value="{pipeline_id}" {'checked' if pipeline_checked else ''}>
                <span>{html.escape(pipeline['pipeline_name'])}</span>
              </label>
            </summary>
            <div class="stage-grid">{''.join(status_rows)}</div>
          </details>
        """)

    return f"""
    <section class="tool-panel">
      <div class="eyebrow">Правила отчета</div>
      <h2>Что включать в аналитику</h2>
      <div class="filter-toolbar">
        <input class="search-input" type="search" data-filter-search placeholder="Найти воронку или этап: ремонт, замер, оплата">
        <div class="preset-row">
          {preset_buttons}
          <button type="button" class="preset-button" data-filter-only-visible>Выбрать найденное</button>
        </div>
      </div>
      <div class="filter-list">{''.join(blocks)}</div>
      <div class="actions">
        <button type="button" data-save-filter>Сохранить фильтр</button>
        <button type="button" class="secondary" data-filter-all>Выбрать все</button>
        <button type="button" class="secondary" data-filter-none>Снять все</button>
        <span class="sync-status" data-filter-status>Выбери нужные воронки и этапы</span>
      </div>
    </section>
    """


def _render_query_builder() -> str:
    return """
    <section class="section-card">
      <div class="section-head">
        <div>
          <div class="eyebrow">Массив данных</div>
          <h2>Гибкая аналитика как сводная таблица</h2>
          <p>Выбери группировку, метрики и условие. Конструктор отправит JSON-запрос в аналитический движок и покажет результат таблицей.</p>
        </div>
        <div class="actions">
          <button type="button" class="secondary" data-report-preset="sources">По источникам</button>
          <button type="button" class="secondary" data-report-preset="created_month">По созданию</button>
          <button type="button" class="secondary" data-report-preset="contract_month">По дате договора</button>
        </div>
      </div>

      <div class="builder-grid">
        <div class="builder-field">
          <label for="report-group">Группировать</label>
          <select id="report-group" data-report-group>
            <option value="pipeline_id">Воронка</option>
            <option value="status_id">Этап</option>
            <option value="created_month">Месяц создания</option>
            <option value="updated_month">Месяц обновления</option>
            <option value="closed_month">Месяц закрытия</option>
            <option value="cf_127785" selected>Рекламная площадка</option>
            <option value="cf_month_127845">Месяц даты договора</option>
          </select>
        </div>
        <div class="builder-field">
          <label for="report-filter-field">Поле условия</label>
          <select id="report-filter-field" data-report-filter-field>
            <option value="">Без условия</option>
            <option value="pipeline_id">Воронка ID</option>
            <option value="status_id">Этап ID</option>
            <option value="created_at">Дата создания</option>
            <option value="updated_at">Дата обновления</option>
            <option value="closed_at">Дата закрытия</option>
            <option value="price">Бюджет</option>
            <option value="cf_127785">Рекламная площадка</option>
            <option value="cf_127845">Дата договора</option>
          </select>
        </div>
        <div class="builder-field">
          <label for="report-filter-op">Условие</label>
          <select id="report-filter-op" data-report-filter-op>
            <option value="eq">равно</option>
            <option value="in">в списке</option>
            <option value="not_in">не в списке</option>
            <option value="gte">больше или равно</option>
            <option value="lte">меньше или равно</option>
            <option value="date_between">дата между</option>
            <option value="this_month">текущий месяц</option>
            <option value="previous_month">прошлый месяц</option>
            <option value="this_week">текущая неделя</option>
            <option value="previous_week">прошлая неделя</option>
            <option value="last_days">последние N дней</option>
            <option value="between">число между</option>
            <option value="like">содержит</option>
          </select>
        </div>
        <div class="builder-field">
          <label for="report-filter-value">Значение</label>
          <input id="report-filter-value" data-report-filter-value placeholder="2026-01-01..2026-06-30 или ID через запятую">
        </div>
      </div>

      <fieldset class="metric-picker">
        <legend>Метрики</legend>
        <label class="sync-option"><input type="checkbox" data-report-metric value="count" checked><span>Количество сделок</span></label>
        <label class="sync-option"><input type="checkbox" data-report-metric value="sum_price" checked><span>Сумма сделок</span></label>
        <label class="sync-option"><input type="checkbox" data-report-metric value="avg_price" checked><span>Средний чек</span></label>
        <label class="sync-option"><input type="checkbox" data-report-metric value="open_count"><span>Открыто</span></label>
        <label class="sync-option"><input type="checkbox" data-report-metric value="won_count"><span>Успешно</span></label>
        <label class="sync-option"><input type="checkbox" data-report-metric value="lost_count"><span>Потеряно</span></label>
      </fieldset>

      <div class="actions">
        <button type="button" data-report-run>Построить отчет</button>
        <span class="sync-status" data-report-status>Можно начать с пресета “По источникам”</span>
      </div>
      <div data-report-result></div>
    </section>
    """


def _sync_source_display_name(source: dict[str, Any]) -> str:
    source_id = int(source["id"])
    raw_name = str(source.get("name") or "")
    pipeline_name = ", ".join(str(name) for name in source.get("pipeline_names") or [] if name)
    display_name = pipeline_name if raw_name.startswith("Источник #20") and "T" in raw_name and pipeline_name else raw_name
    return display_name or f"Источник {source_id}"


def _sync_source_option_label(source: dict[str, Any]) -> str:
    count = int(source.get("linked_leads_count") or source.get("linked_count") or 0)
    return f"{_sync_source_display_name(source)} · {count} сделок"


def _render_source_options(
    sync_sources: list[dict[str, Any]],
    *,
    selected_source_id: int | None = None,
    source_ids: list[int] | None = None,
    include_hub: bool = True,
) -> str:
    sources_by_id = {int(source["id"]): source for source in sync_sources}
    if source_ids:
        sources = [sources_by_id[source_id] for source_id in source_ids if source_id in sources_by_id]
    else:
        sources = sync_sources
    options = '<option value="">Весь хаб</option>' if include_hub else ""
    options += "".join(
        f'<option value="{int(source["id"])}"{" selected" if selected_source_id and int(source["id"]) == int(selected_source_id) else ""}>{html.escape(_sync_source_option_label(source))}</option>'
        for source in sources
    )
    return options


def _render_query_builder_v2(
    sync_sources: list[dict[str, Any]] | None = None,
    selected_source_id: int | None = None,
    filter_options: list[dict[str, Any]] | None = None,
) -> str:
    sync_sources = sync_sources or []
    filter_options = filter_options or []

    source_options = _render_source_options(sync_sources, selected_source_id=selected_source_id)
    source_create_blocks = "".join(
        f"""
        <details class="filter-pipeline" data-create-source-block data-create-source-pipeline-id="{int(pipeline['pipeline_id'])}" data-create-source-text="{html.escape((str(pipeline['pipeline_name']) + ' ' + ' '.join(str(status['status_name']) for status in pipeline['statuses'])).lower())}">
          <summary>
            <label>
              <input type="checkbox" data-create-source-pipeline value="{int(pipeline['pipeline_id'])}">
              <span>{html.escape(str(pipeline['pipeline_name']))}</span>
            </label>
          </summary>
          <div class="stage-grid">
            {''.join(
                f'''
                <label class="stage-option">
                  <input type="checkbox" data-create-source-status data-pipeline-id="{int(pipeline["pipeline_id"])}" value="{int(status["status_id"])}">
                  <span>{html.escape(str(status["status_name"]))}</span>
                </label>
                '''
                for status in pipeline["statuses"]
            )}
          </div>
        </details>
        """
        for pipeline in filter_options
    ) or '<div class="work-source-empty">Воронки появятся после выгрузки справочника “Воронки и этапы”.</div>'
    return f"""
    <section class="section-card">
      <div class="section-head">
        <div>
          <div class="eyebrow">Конструктор отчета</div>
          <h2>Начинаем с массива данных</h2>
          <p>Сейчас фиксируем только базу: какой источник amoCRM берем в работу и сколько сделок в нем лежит. Следующие шаги будем добавлять по одному.</p>
        </div>
      </div>

      <div class="report-builder">
        <div class="builder-step primary">
          <div class="step-head">
            <span class="step-number">1</span>
            <div>
              <h3>Источник данных</h3>
              <p>Выбери выгрузку, с которой будет работать будущий дашборд.</p>
            </div>
          </div>
          <div class="source-choice-grid">
            <div class="builder-field">
              <label for="report-source">Источник данных</label>
              <select id="report-source" data-report-source>
                {source_options}
              </select>
            </div>
            <div class="source-note" data-report-source-note>
              <div class="source-note-head">
                <div class="source-note-title">Весь хаб</div>
                <div class="source-status-pill">живой</div>
              </div>
              <div class="source-note-grid">
                <div class="source-note-item"><span>Источник</span><strong>Все данные хаба</strong></div>
                <div class="source-note-item"><span>Обновление</span><strong>Через webhook и очередь</strong></div>
              </div>
            </div>
          </div>
          <div class="builder-actions">
            <button type="button" class="secondary" data-source-create-open>Создать источник</button>
            <button type="button" data-report-run>Проверить массив</button>
            <button type="button" class="secondary" data-source-work-add>Добавить в работу</button>
            <button type="button" class="secondary" data-source-refresh>Обновить источник</button>
            <a class="button-link secondary" href="/constructor" data-constructor-link>Открыть конструктор</a>
            <span class="sync-status" data-report-status>Выбери источник и проверь, сколько сделок попадает в базовый массив</span>
          </div>
          <div class="work-source-panel">
            <h4>Источники в работе</h4>
            <p>Здесь фиксируем источники, из которых дальше будем собирать показатели и дашборды.</p>
            <div class="work-source-list" data-work-sources></div>
          </div>
          <div class="source-create-modal" data-create-source-modal hidden>
            <div class="source-create-dialog" role="dialog" aria-modal="true" aria-labelledby="create-source-title">
              <div class="source-create-head">
              <div>
                  <h3 id="create-source-title">Создать новый источник</h3>
                  <p>Собери массив: название, воронка, этапы, затем выгрузи его из amoCRM.</p>
              </div>
                <button type="button" class="modal-close" data-create-source-close aria-label="Закрыть">×</button>
              </div>
            <div class="source-create-body">
              <div class="source-create-tools">
                <div class="builder-field">
                  <label for="create-source-name">Название источника</label>
                  <input id="create-source-name" data-create-source-name placeholder="Например: Розыгрыш · активные этапы">
                </div>
                <div class="builder-field">
                  <label for="create-source-search">Поиск воронки или этапа</label>
                  <input id="create-source-search" class="search-input" data-create-source-search placeholder="Например: NEW, розыгрыш, замер">
                </div>
              </div>
              <div class="source-create-summary" data-create-source-summary>Выбери одну или несколько воронок. Этапы внутри выбранной воронки отметятся автоматически.</div>
              <div class="source-create-list">{source_create_blocks}</div>
              <div class="builder-actions">
                <button type="button" data-create-source-run>Создать источник и выгрузить</button>
                <button type="button" class="secondary" data-create-source-clear>Сбросить выбор</button>
                <button type="button" class="secondary" data-create-source-close>Закрыть</button>
                <span class="sync-status" data-create-source-status>Новый источник появится в выпадающем списке после выгрузки</span>
              </div>
            </div>
            </div>
          </div>
        </div>
      </div>
      <div data-report-result></div>
    </section>
    """
    field_options = """
            <option value="">Без условия</option>
            <option value="pipeline_id">Воронка ID</option>
            <option value="status_id">Этап ID</option>
            <option value="created_at">Дата создания</option>
            <option value="updated_at">Дата обновления</option>
            <option value="closed_at">Дата закрытия</option>
            <option value="price">Бюджет</option>
            <option value="cf_127785">Рекламная площадка</option>
            <option value="cf_127845">Дата договора</option>
    """
    op_options = """
            <option value="eq">равно</option>
            <option value="in">в списке</option>
            <option value="not_in">не в списке</option>
            <option value="gte">больше или равно</option>
            <option value="lte">меньше или равно</option>
            <option value="date_between">дата между</option>
            <option value="between">число между</option>
            <option value="like">содержит</option>
            <option value="this_month">текущий месяц</option>
            <option value="previous_month">прошлый месяц</option>
            <option value="this_week">текущая неделя</option>
            <option value="previous_week">прошлая неделя</option>
            <option value="last_days">последние N дней</option>
    """
    condition_rows = "".join(
        f"""
        <div class="condition-row" data-report-condition>
          <div class="builder-field">
            <label>Поле условия {index}</label>
            <select data-report-filter-field>{field_options}</select>
          </div>
          <div class="builder-field">
            <label>Оператор</label>
            <select data-report-filter-op>{op_options}</select>
          </div>
          <div class="builder-field">
            <label>Формат</label>
            <select data-report-value-type>
              <option value="auto">Авто</option>
              <option value="text">Текст</option>
              <option value="number">Число</option>
              <option value="date">Дата</option>
              <option value="datetime">Дата и время</option>
            </select>
          </div>
          <div class="builder-field">
            <label>Значение</label>
            <input data-report-filter-value list="report-values-{index}" placeholder="ID, текст, список через запятую или 2026-01-01..2026-06-30">
            <datalist id="report-values-{index}"></datalist>
          </div>
        </div>
        """
        for index in range(1, 4)
    )
    return f"""
    <section class="section-card">
      <div class="section-head">
        <div>
          <div class="eyebrow">Конструктор отчета</div>
          <h2>Собери показатель как в сводной таблице</h2>
          <p>Идем по шагам: выбираем формат, фильтруем сделки, задаем метрики и сохраняем виджет на дашборд.</p>
        </div>
        <div class="builder-presets">
          <button type="button" class="secondary" data-report-preset="sources">По источникам</button>
          <button type="button" class="secondary" data-report-preset="created_month">По созданию</button>
          <button type="button" class="secondary" data-report-preset="contract_month">По дате договора</button>
          <button type="button" class="secondary" data-report-preset="kpi_created">KPI за период</button>
        </div>
      </div>

      <div class="report-builder">
        <div class="builder-step primary">
          <div class="step-head">
            <span class="step-number">1</span>
            <div>
              <h3>Источник данных</h3>
              <p>Выбери конкретную выгрузку или считай по всему хабу.</p>
            </div>
          </div>
          <div class="source-choice-grid">
            <div class="builder-field">
              <label for="report-source">Источник данных</label>
              <select id="report-source" data-report-source>
                {source_options}
              </select>
            </div>
            <div class="source-note" data-report-source-note>Источник: весь хаб</div>
          </div>
        </div>

        <div class="builder-step">
          <div class="step-head">
            <span class="step-number">2</span>
            <div>
              <h3>Вид отчета</h3>
              <p>Настрой формат, группировку и логику условий.</p>
            </div>
          </div>
          <div class="builder-grid result">
            <div class="builder-field">
              <label for="report-view">Формат</label>
              <select id="report-view" data-report-view>
                <option value="table">Таблица</option>
                <option value="number">Блоки чисел</option>
                <option value="bar">Горизонтальный график</option>
                <option value="line">Линия динамики</option>
                <option value="list">Топ-список</option>
              </select>
            </div>
            <div class="builder-field">
              <label for="report-group">Группировка</label>
              <select id="report-group" data-report-group>
                <option value="pipeline_id">Воронка</option>
                <option value="status_id">Этап</option>
                <option value="created_month">Месяц создания</option>
                <option value="updated_month">Месяц обновления</option>
                <option value="closed_month">Месяц закрытия</option>
                <option value="cf_127785" selected>Рекламная площадка</option>
                <option value="cf_month_127845">Месяц даты договора</option>
              </select>
            </div>
            <div class="builder-field">
              <label for="report-logic">Логика фильтров</label>
              <select id="report-logic" data-report-logic>
                <option value="and">И: все условия</option>
                <option value="or">ИЛИ: любое условие</option>
              </select>
            </div>
          </div>
        </div>

        <details class="builder-step filter-step" open>
          <summary class="step-head">
            <span class="step-number">3</span>
            <div>
              <h3>Фильтры сделок</h3>
              <p>Пустые строки не учитываются.</p>
            </div>
          </summary>
          <div class="condition-list">
            {condition_rows}
          </div>
        </details>

        <div class="builder-step">
          <div class="step-head">
            <span class="step-number">4</span>
            <div>
              <h3>Сбор показателя</h3>
              <p>Укажи, что именно считать в этом блоке. Основной показатель управляет графиком, списком и сортировкой.</p>
            </div>
          </div>
          <div class="builder-grid two">
            <div class="metric-builder">
              <div class="builder-field">
                <label>Основной показатель</label>
                <select data-report-metric-select>
                  <option value="count">Количество сделок</option>
                  <option value="sum_price">Сумма сделок</option>
                  <option value="avg_price">Средний чек</option>
                  <option value="open_count">Открытые сделки</option>
                  <option value="won_count">Успешные сделки</option>
                  <option value="lost_count">Потерянные сделки</option>
                </select>
              </div>
              <div class="builder-field">
                <label>Доп. показатель</label>
                <select data-report-metric-select>
                  <option value="">Не добавлять</option>
                  <option value="sum_price">Сумма сделок</option>
                  <option value="avg_price">Средний чек</option>
                  <option value="count">Количество сделок</option>
                  <option value="open_count">Открытые сделки</option>
                  <option value="won_count">Успешные сделки</option>
                  <option value="lost_count">Потерянные сделки</option>
                </select>
              </div>
              <div class="builder-field">
                <label>Доп. показатель</label>
                <select data-report-metric-select>
                  <option value="">Не добавлять</option>
                  <option value="avg_price">Средний чек</option>
                  <option value="sum_price">Сумма сделок</option>
                  <option value="count">Количество сделок</option>
                  <option value="open_count">Открытые сделки</option>
                  <option value="won_count">Успешные сделки</option>
                  <option value="lost_count">Потерянные сделки</option>
                </select>
              </div>
            </div>
            <div class="builder-field">
              <label for="widget-formula">Формула KPI</label>
              <select id="widget-formula" data-widget-formula>
                <option value="none">Без формулы</option>
                <option value="conversion">Конверсия = успешно / всего</option>
                <option value="lost_rate">Потери = потеряно / всего</option>
                <option value="open_rate">Открытые = открыто / всего</option>
                <option value="delta_won_lost">Успешно - потеряно</option>
                <option value="plan_fact">План / факт / прогноз</option>
              </select>
            </div>
          </div>
        </div>

        <div class="builder-step">
          <div class="step-head">
            <span class="step-number">5</span>
            <div>
              <h3>Проверка и сохранение</h3>
              <p>Построй отчет, потом сохрани его как блок дашборда.</p>
            </div>
          </div>
          <div class="builder-grid two">
            <div class="builder-field">
              <label for="widget-size">Размер на дашборде</label>
              <select id="widget-size" data-widget-size>
                <option value="small">Маленький</option>
                <option value="medium" selected>Средний</option>
                <option value="large">Большой</option>
                <option value="wide">Широкий</option>
              </select>
            </div>
            <div class="builder-field">
              <label>Название блока</label>
              <input class="search-input" data-widget-title placeholder="Например: Заявки по источникам">
            </div>
            <div class="builder-field">
              <label>Лист дашборда</label>
              <select data-widget-page>
                <option value="main">Основной</option>
              </select>
            </div>
          </div>
          <div class="builder-actions">
            <button type="button" data-report-run>Построить отчет</button>
            <button type="button" class="secondary" data-report-save-widget>Добавить на дашборд</button>
            <a class="button-link secondary" href="/dashboard" data-dashboard-link>Открыть дашборд</a>
            <span class="sync-status" data-report-status>Можно начать с пресета “По источникам”</span>
          </div>
        </div>
      </div>
      <div data-report-result></div>
    </section>
    """


def _render_constructor_shell(
    sync_sources: list[dict[str, Any]] | None = None,
    work_source_ids: list[int] | None = None,
    *,
    selected_source_id: int | None = None,
) -> str:
    sync_sources = sync_sources or []
    work_source_ids = work_source_ids or []
    sources_by_id = {int(source["id"]): source for source in sync_sources}
    active_source_ids = [source_id for source_id in work_source_ids if source_id in sources_by_id]
    source_options = _render_source_options(
        sync_sources,
        selected_source_id=selected_source_id,
        source_ids=active_source_ids or None,
    )
    work_source_summary = "".join(
        f"""
        <span class="constructor-source-pill">
          {html.escape(_sync_source_display_name(sources_by_id[source_id]))}
          <b>{int(sources_by_id[source_id].get("linked_leads_count") or sources_by_id[source_id].get("linked_count") or 0)}</b>
        </span>
        """
        for source_id in active_source_ids
    )
    if not work_source_summary:
        work_source_summary = """
        <div class="work-source-empty compact">
          Рабочий набор пока пустой. Можно собрать показатель по всему хабу или вернуться в массив данных и добавить конкретные источники.
        </div>
        """
    return f"""
    <section class="section-card constructor-workbench formula-workbench">
      <div class="section-head">
        <div>
          <div class="eyebrow">Конструктор формул</div>
          <h2>Собираем показатель из массива amoCRM</h2>
          <p>Здесь показатель хранится как формула: берем строки из источника, выбираем столбцы и условия, считаем число, ряд или таблицу, потом отправляем результат на дашборд.</p>
        </div>
        <a class="button-link secondary" href="/dashboard" data-dashboard-link>Открыть дашборд</a>
      </div>

      <div class="constructor-source-strip">{work_source_summary}</div>

      <div class="formula-lab-layout">
        <section class="formula-editor-panel">
          <div class="formula-toolbar">
            <div class="builder-field">
              <label for="formula-source">Источник данных</label>
              <select id="formula-source" data-formula-source>{source_options}</select>
            </div>
            <div class="builder-field">
              <label for="formula-title">Название блока</label>
              <input id="formula-title" data-formula-title placeholder="Например: Конверсия замерщиков">
            </div>
          </div>

          <div class="formula-template-grid">
            <button type="button" class="secondary" data-formula-template="count">Количество сделок</button>
            <button type="button" class="secondary" data-formula-template="sum">Сумма сделок</button>
            <button type="button" class="secondary" data-formula-template="responsible">По ответственным</button>
            <button type="button" class="secondary" data-formula-template="table">Таблица KPI</button>
          </div>

          <div class="ai-formula-box">
            <div>
              <div class="eyebrow">AI-помощник</div>
              <h3 style="margin: 4px 0 6px;">Опиши показатель обычными словами</h3>
              <p style="margin: 0; color: var(--muted);">AI соберет черновик формулы, а мы проверим его через ядро перед добавлением на дашборд.</p>
            </div>
            <textarea data-ai-formula-prompt placeholder="Например: Посчитай заявки за текущий месяц по рекламным площадкам, покажи количество и сумму"></textarea>
            <div class="ai-formula-actions">
              <button type="button" class="secondary" data-ai-formula-run>Сгенерировать формулу</button>
              <button type="button" class="secondary" data-ai-formula-apply disabled>Применить черновик</button>
              <span class="sync-status" data-ai-formula-status>Можно писать как ТЗ: период, источник, поля, группировку.</span>
            </div>
            <div class="ai-formula-result" data-ai-formula-result></div>
          </div>

          <div class="ai-formula-box">
            <div>
              <div class="eyebrow">Импорт фильтра amoCRM</div>
              <h3 style="margin: 4px 0 6px;">Собери показатель из готового фильтра</h3>
              <p style="margin: 0; color: var(--muted);">Вставь ссылку из amoCRM: конструктор разберет воронку, этапы, поля и период, потом перенесет это в условия формулы.</p>
            </div>
            <textarea data-amo-filter-url placeholder="Например: https://donpotolok.amocrm.ru/leads/pipeline/.../?filter%5Bcf%5D..."></textarea>
            <div class="ai-formula-actions">
              <button type="button" class="secondary" data-amo-filter-parse>Разобрать ссылку</button>
              <button type="button" class="secondary" data-amo-filter-apply disabled>Применить к формуле</button>
              <span class="sync-status" data-amo-filter-status>Можно вставить ссылку с фильтрами amoCRM.</span>
            </div>
            <div class="ai-formula-result" data-amo-filter-result></div>
          </div>

          <div class="ai-formula-box" data-column-builder>
            <div>
              <div class="eyebrow">Конструктор колонок</div>
              <h3 style="margin: 4px 0 6px;">Собери таблицу из колонок</h3>
              <p style="margin: 0; color: var(--muted);">Каждая колонка — отдельное описание словами. AI собирает их по одной, потом склеиваем в таблицу.</p>
            </div>
            <div class="column-builder-toolbar">
              <label class="builder-field">
                Группировать по
                <select data-column-builder-group><option value="">(из первой колонки)</option></select>
              </label>
              <button type="button" class="secondary" data-column-builder-assemble>Собрать таблицу</button>
              <span class="sync-status" data-column-builder-status>Добавь колонки и собери таблицу.</span>
            </div>
            <div class="column-builder-live" data-column-builder-live></div>
            <div class="column-builder-list" data-column-builder-list></div>
            <div class="ai-formula-actions">
              <button type="button" class="secondary" data-column-builder-add>Добавить колонку</button>
            </div>
          </div>

          <details class="formula-panel manual-mode">
            <summary>
              <strong>Ручной режим (маска и JSON)</strong>
              <span>для точной ручной настройки — обычно хватает AI-помощника и конструктора колонок</span>
            </summary>
            <div class="manual-mode-body">
          <div class="formula-human-builder" data-formula-builder>
            <div class="formula-mask-grid">
              <div class="builder-field">
                <label for="formula-entity">Что берем</label>
                <select id="formula-entity" data-formula-entity></select>
              </div>
              <div class="builder-field">
                <label for="formula-op">Как считаем</label>
                <select id="formula-op" data-formula-op>
                  <option value="count">Считаем количество строк</option>
                  <option value="sum">Складываем значения</option>
                  <option value="avg">Считаем среднее</option>
                  <option value="min">Минимальное значение</option>
                  <option value="max">Максимальное значение</option>
                </select>
              </div>
              <div class="builder-field">
                <label for="formula-value-field">Какой столбец</label>
                <select id="formula-value-field" data-formula-value-field></select>
              </div>
              <div class="builder-field">
                <label for="formula-group-field">Разбить по</label>
                <select id="formula-group-field" data-formula-group-field></select>
              </div>
            </div>

            <div>
              <div class="eyebrow">Условия</div>
              <div class="formula-filter-list" data-formula-filter-list>
                <div class="formula-filter-row" data-formula-filter>
                  <select data-formula-filter-field></select>
                  <select data-formula-filter-op>
                    <option value="eq">равно</option>
                    <option value="neq">не равно</option>
                    <option value="like">содержит текст</option>
                    <option value="in">в списке</option>
                    <option value="not_in">не в списке</option>
                    <option value="gt">больше</option>
                    <option value="gte">больше или равно</option>
                    <option value="lt">меньше</option>
                    <option value="lte">меньше или равно</option>
                    <option value="between">число между</option>
                    <option value="date_between">дата между</option>
                    <option value="this_month">текущий месяц</option>
                    <option value="previous_month">прошлый месяц</option>
                    <option value="this_week">текущая неделя</option>
                    <option value="previous_week">прошлая неделя</option>
                    <option value="last_days">последние N дней</option>
                  </select>
                  <input data-formula-filter-value placeholder="Например: Конкин или 2026-07-01..2026-07-31">
                </div>
                <div class="formula-filter-row" data-formula-filter>
                  <select data-formula-filter-field></select>
                  <select data-formula-filter-op>
                    <option value="eq">равно</option>
                    <option value="neq">не равно</option>
                    <option value="like">содержит текст</option>
                    <option value="in">в списке</option>
                    <option value="not_in">не в списке</option>
                    <option value="gt">больше</option>
                    <option value="gte">больше или равно</option>
                    <option value="lt">меньше</option>
                    <option value="lte">меньше или равно</option>
                    <option value="between">число между</option>
                    <option value="date_between">дата между</option>
                    <option value="this_month">текущий месяц</option>
                    <option value="previous_month">прошлый месяц</option>
                    <option value="this_week">текущая неделя</option>
                    <option value="previous_week">прошлая неделя</option>
                    <option value="last_days">последние N дней</option>
                  </select>
                  <input data-formula-filter-value placeholder="Можно оставить пустым">
                </div>
                <div class="formula-filter-row" data-formula-filter>
                  <select data-formula-filter-field></select>
                  <select data-formula-filter-op>
                    <option value="eq">равно</option>
                    <option value="neq">не равно</option>
                    <option value="like">содержит текст</option>
                    <option value="in">в списке</option>
                    <option value="not_in">не в списке</option>
                    <option value="gt">больше</option>
                    <option value="gte">больше или равно</option>
                    <option value="lt">меньше</option>
                    <option value="lte">меньше или равно</option>
                    <option value="between">число между</option>
                    <option value="date_between">дата между</option>
                    <option value="this_month">текущий месяц</option>
                    <option value="previous_month">прошлый месяц</option>
                    <option value="this_week">текущая неделя</option>
                    <option value="previous_week">прошлая неделя</option>
                    <option value="last_days">последние N дней</option>
                  </select>
                  <input data-formula-filter-value placeholder="Можно оставить пустым">
                </div>
              </div>
              <button type="button" class="secondary formula-add-filter" data-formula-filter-add>Добавить условие</button>
            </div>

            <div class="formula-readable" data-formula-readable>
              Формула еще собирается.
            </div>
          </div>

          <details class="formula-panel">
            <summary>
              <strong>Технический вид</strong>
              <span>JSON хранится для точной отладки, руками его трогать не обязательно</span>
            </summary>
            <div class="builder-field">
              <label for="formula-editor">Формула JSON</label>
              <textarea id="formula-editor" class="formula-editor" data-formula-editor spellcheck="false"></textarea>
            </div>
          </details>
            </div>
          </details>

          <details class="formula-panel">
            <summary>
              <strong>Словарь полей</strong>
              <span>пополняется автоматически из amoCRM и выгруженных кастомных полей</span>
            </summary>
            <div class="formula-dictionary" data-formula-dictionary>
              Загружаю поля amoCRM...
            </div>
          </details>
        </section>

        <aside class="formula-preview-panel">
          <div class="constructor-preview-head">
            <div>
              <div class="eyebrow">Предпросмотр</div>
              <h3>Результат формулы</h3>
            </div>
            <div class="builder-field compact-size">
              <label for="formula-widget-size">Размер</label>
              <select id="formula-widget-size" data-formula-size>
                <option value="small">Маленький</option>
                <option value="medium" selected>Средний</option>
                <option value="large">Большой</option>
                <option value="wide">Широкий</option>
              </select>
            </div>
          </div>

          <label class="formula-preview-title">
            <span class="eyebrow">Лист дашборда</span>
            <select data-widget-page>
              <option value="main">Основной</option>
            </select>
          </label>

          <label class="formula-preview-title">
            <span class="eyebrow">Название на дашборде</span>
            <input data-formula-preview-title placeholder="Например: Сделки за сегодня по фильтру amoCRM">
          </label>

          <div class="constructor-preview-actions">
            <button type="button" data-formula-run>Посчитать</button>
            <button type="button" class="secondary" data-formula-save>Отправить на дашборд</button>
          </div>
          <div class="sync-status" data-formula-status>Собери показатель из понятных полей и нажми “Посчитать”.</div>
          <div class="constructor-preview-result" data-formula-result>
            <div class="report-empty">Здесь появится результат: число, список или таблица.</div>
          </div>
        </aside>
      </div>
    </section>
    """
    field_options = """
            <option value="">Без условия</option>
            <option value="pipeline_id">Воронка</option>
            <option value="status_id">Этап</option>
            <option value="responsible_user_id">Ответственный</option>
            <option value="created_at">Дата создания</option>
            <option value="updated_at">Дата обновления</option>
            <option value="closed_at">Дата закрытия</option>
            <option value="price">Бюджет</option>
            <option value="cf_127785">Рекламная площадка</option>
            <option value="cf_127845">Дата договора</option>
    """
    op_options = """
            <option value="eq">равно</option>
            <option value="in">в списке</option>
            <option value="not_in">не в списке</option>
            <option value="gte">больше или равно</option>
            <option value="lte">меньше или равно</option>
            <option value="date_between">дата между</option>
            <option value="between">число между</option>
            <option value="like">содержит</option>
            <option value="this_month">текущий месяц</option>
            <option value="previous_month">прошлый месяц</option>
            <option value="this_week">текущая неделя</option>
            <option value="previous_week">прошлая неделя</option>
            <option value="last_days">последние N дней</option>
    """
    condition_rows = "".join(
        f"""
        <div class="condition-row compact" data-report-condition>
          <div class="builder-field">
            <label>Поле {index}</label>
            <select data-report-filter-field>{field_options}</select>
          </div>
          <div class="builder-field">
            <label>Условие</label>
            <select data-report-filter-op>{op_options}</select>
          </div>
          <div class="builder-field slim">
            <label>Формат</label>
            <select data-report-value-type>
              <option value="auto">Авто</option>
              <option value="text">Текст</option>
              <option value="number">Число</option>
              <option value="date">Дата</option>
              <option value="datetime">Дата и время</option>
            </select>
          </div>
          <div class="builder-field">
            <label>Значение</label>
            <input data-report-filter-value list="report-values-{index}" placeholder="Например: 2026-07-01..2026-07-31">
            <datalist id="report-values-{index}"></datalist>
          </div>
        </div>
        """
        for index in range(1, 4)
    )
    return f"""
    <section class="section-card constructor-workbench formula-workbench">
      <div class="section-head">
        <div>
          <div class="eyebrow">Формула показателя</div>
          <h2>Собери показатель как цепочку условий</h2>
          <p>Показатель читается слева направо: название, что считаем, откуда берем, какие условия применяем, какая формула и как показываем.</p>
        </div>
        <div class="builder-presets">
          <button type="button" class="secondary" data-report-preset="sources">Источник</button>
          <button type="button" class="secondary" data-report-preset="created_month">Период</button>
          <button type="button" class="secondary" data-report-preset="contract_month">Договор</button>
          <button type="button" class="secondary" data-report-preset="kpi_created">KPI</button>
        </div>
      </div>

      <div class="formula-layout">
        <div class="formula-main">
          <div class="constructor-source-strip">{work_source_summary}</div>
          <div class="formula-chain" aria-label="Формула показателя">
            <article class="formula-block title-block">
              <span>Показатель</span>
              <input data-widget-title placeholder="Например: Договора · Конкин">
            </article>
            <div class="formula-arrow">→</div>
            <article class="formula-block">
              <span>Берем</span>
              <select data-report-metric-select>
                <option value="count">Количество сделок</option>
                <option value="sum_price">Сумма сделок</option>
                <option value="avg_price">Средний чек</option>
                <option value="open_count">Открытые сделки</option>
                <option value="won_count">Успешные сделки</option>
                <option value="lost_count">Потерянные сделки</option>
              </select>
            </article>
            <div class="formula-arrow">→</div>
            <article class="formula-block source-block">
              <span>Из массива</span>
              <select id="report-source" data-report-source>
                {source_options}
              </select>
            </article>
            <div class="formula-arrow">→</div>
            <article class="formula-block">
              <span>Формула</span>
              <select id="widget-formula" data-widget-formula>
                <option value="none">Без формулы</option>
                <option value="plan_fact">План / факт / прогноз</option>
                <option value="conversion">Конверсия в успех</option>
                <option value="lost_rate">Доля потерь</option>
                <option value="open_rate">Доля открытых</option>
                <option value="delta_won_lost">Успешно - потеряно</option>
              </select>
            </article>
            <div class="formula-arrow">→</div>
            <article class="formula-block">
              <span>Показываем</span>
              <select id="report-view" data-report-view>
                <option value="number">Карточками</option>
                <option value="table">Таблицей</option>
                <option value="bar">Полосами</option>
                <option value="line">Линией</option>
                <option value="list">Топ-списком</option>
              </select>
            </article>
          </div>

          <div class="formula-details">
            <details class="formula-panel" open>
              <summary>
                <strong>Условия отбора</strong>
                <span>это часть “ГДЕ” в формуле</span>
              </summary>
              <div class="builder-field logic-field">
                <label for="report-logic">Как объединять условия</label>
                <select id="report-logic" data-report-logic>
                  <option value="and">Все условия сразу</option>
                  <option value="or">Любое из условий</option>
                </select>
              </div>
              <div class="condition-list compact">
                {condition_rows}
              </div>
            </details>

            <details class="formula-panel" open>
              <summary>
                <strong>Дополнительные части формулы</strong>
                <span>добавляются к тому же показателю</span>
              </summary>
              <div class="formula-inline-grid">
                <div class="builder-field">
                  <label>Добавить число</label>
                  <select data-report-metric-select>
                    <option value="">Не добавлять</option>
                    <option value="sum_price">Сумма сделок</option>
                    <option value="avg_price">Средний чек</option>
                    <option value="count">Количество сделок</option>
                    <option value="open_count">Открытые сделки</option>
                    <option value="won_count">Успешные сделки</option>
                    <option value="lost_count">Потерянные сделки</option>
                  </select>
                </div>
                <div class="builder-field">
                  <label>Еще число</label>
                  <select data-report-metric-select>
                    <option value="">Не добавлять</option>
                    <option value="avg_price">Средний чек</option>
                    <option value="sum_price">Сумма сделок</option>
                    <option value="count">Количество сделок</option>
                    <option value="open_count">Открытые сделки</option>
                    <option value="won_count">Успешные сделки</option>
                    <option value="lost_count">Потерянные сделки</option>
                  </select>
                </div>
                <div class="builder-field">
                  <label for="report-group">Разбить по</label>
                  <select id="report-group" data-report-group>
                    <option value="pipeline_id">Воронка</option>
                    <option value="status_id">Этап</option>
                    <option value="responsible_user_id">Ответственный</option>
                    <option value="created_month">Месяц создания</option>
                    <option value="updated_month">Месяц обновления</option>
                    <option value="closed_month">Месяц закрытия</option>
                    <option value="cf_127785" selected>Рекламная площадка</option>
                    <option value="cf_month_127845">Месяц договора</option>
                  </select>
                </div>
                <div class="builder-field">
                  <label for="widget-size">Размер на дашборде</label>
                  <select id="widget-size" data-widget-size>
                    <option value="small">Маленький</option>
                    <option value="medium" selected>Средний</option>
                    <option value="large">Большой</option>
                    <option value="wide">Широкий</option>
                  </select>
                </div>
              </div>
            </details>

            <details class="formula-panel">
              <summary>
                <strong>План и прогноз</strong>
                <span>используется формулой “План / факт / прогноз”</span>
              </summary>
              <div class="plan-settings">
                <div class="builder-field">
                  <label>План</label>
                  <input type="number" min="0" step="1" data-widget-plan placeholder="Например: 280">
                </div>
                <div class="builder-field">
                  <label>Дней в периоде</label>
                  <input type="number" min="1" step="1" data-widget-period-days value="30">
                </div>
                <div class="builder-field">
                  <label>Дней прошло</label>
                  <input type="number" min="1" step="1" data-widget-days-passed value="3">
                </div>
              </div>
            </details>

            <div class="source-note small" data-report-source-note>Источник: весь хаб</div>
          </div>
        </div>

        <aside class="constructor-preview-panel formula-preview">
          <div class="constructor-preview-head">
            <div>
              <div class="eyebrow">Результат формулы</div>
              <h3>Предпросмотр показателя</h3>
            </div>
            <a class="button-link secondary" href="/dashboard" data-dashboard-link>Дашборд</a>
          </div>
          <label class="formula-preview-title">
            <span class="eyebrow">Лист дашборда</span>
            <select data-widget-page>
              <option value="main">Основной</option>
            </select>
          </label>
          <div class="constructor-preview-actions">
            <button type="button" data-report-run>Посчитать</button>
            <button type="button" class="secondary" data-report-save-widget>Отправить на дашборд</button>
            <button type="button" class="secondary" data-formula-copy>Скопировать формулу</button>
          </div>
          <div class="sync-status" data-report-status>Собери цепочку слева и нажми “Посчитать”.</div>
          <div class="constructor-preview-result" data-report-result>
            <div class="report-empty">
              Здесь появится результат формулы. Потом такой же блок можно отправить на дашборд, скопировать и поменять одно условие.
            </div>
          </div>
        </aside>
      </div>
    </section>
    """


def _render_saved_dashboard() -> str:
    return """
    <section class="section-card">
      <div class="section-head">
        <div>
          <div class="eyebrow">Мой дашборд</div>
          <h2>Сохраненные показатели</h2>
          <p>Здесь собираются KPI-блоки и таблицы, которые ты добавляешь из конструктора отчетов.</p>
        </div>
        <div class="saved-dashboard-toolbar">
          <button type="button" class="preset-button" data-dashboard-refresh>Обновить</button>
          <button type="button" class="preset-button primary" data-dashboard-edit-toggle aria-pressed="false">Настроить виджеты</button>
          <a class="preset-button" href="/settings" data-settings-link>Массив данных</a>
        </div>
      </div>
      <div class="dashboard-edit-hint" data-dashboard-edit-hint hidden>Режим настройки включен: можно двигать, растягивать и менять виджеты.</div>
      <div class="dashboard-pages-bar">
        <div class="dashboard-page-tabs" data-dashboard-pages></div>
        <div class="dashboard-page-controls">
          <button class="icon-button" type="button" title="Предыдущий лист" data-dashboard-page-prev>‹</button>
          <button class="icon-button" type="button" title="Следующий лист" data-dashboard-page-next>›</button>
          <button class="icon-button edit-only" type="button" title="Добавить лист" data-dashboard-page-add>+</button>
          <button class="icon-button edit-only" type="button" title="Переименовать лист" data-dashboard-page-rename>✎</button>
          <button class="icon-button danger edit-only" type="button" title="Удалить лист" data-dashboard-page-delete>×</button>
        </div>
      </div>
      <div class="saved-dashboard-grid" data-saved-dashboard>
        <div class="report-empty">Загружаю виджеты...</div>
      </div>
    </section>
    """


def write_dashboard(path: Path, summary: dict[str, Any], tasks: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(summary, tasks), encoding="utf-8")
    return path
