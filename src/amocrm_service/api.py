from __future__ import annotations

import sqlite3
from urllib.parse import parse_qs

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from amocrm_service.amocrm import AmoCRMClient
from amocrm_service.analytics import AnalyticsService
from amocrm_service.config import load_settings
from amocrm_service.conversations import ConversationPipeline
from amocrm_service.dashboard import render_dashboard
from amocrm_service.db import connect, init_db
from amocrm_service.filters import load_analytics_filter
from amocrm_service.repository import Repository
from amocrm_service.site_forms import parse_site_lead_payload
from amocrm_service.sync import SyncService
from amocrm_service.tenancy import (
    admin_connections,
    load_account_settings,
    load_user_settings,
    save_account_settings,
    save_user_settings,
    set_connection_status,
)


app = FastAPI(title="amoCRM Analytics and Control Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Form-Secret"],
)


def _repo() -> Repository:
    settings = load_settings()
    init_db(settings.db_path)
    return Repository(connect(settings.db_path))


def _sync_service() -> SyncService:
    settings = load_settings()
    repo = _repo()
    return SyncService(AmoCRMClient(settings), repo)


def _active_filter_value(value: str = "true") -> bool | None:
    text = str(value or "true").strip().lower()
    if text in {"", "1", "true", "yes", "active"}:
        return True
    if text in {"0", "false", "no", "inactive"}:
        return False
    if text in {"all", "any", "*"}:
        return None
    raise HTTPException(status_code=400, detail="active must be one of: true, false, all")


def _optional_bool(payload: dict, key: str) -> bool | None:
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
    raise HTTPException(status_code=400, detail=f"{key} must be boolean")


async def _request_payload(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        return await request.json()
    raw = (await request.body()).decode("utf-8")
    return {key: values[-1] if values else "" for key, values in parse_qs(raw, keep_blank_values=True).items()}


@app.get("/health")
def health() -> dict:
    settings = load_settings()
    return {
        "ok": True,
        "subdomain_configured": bool(settings.subdomain),
        "token_configured": bool(settings.access_token),
        "db_path": str(settings.db_path),
    }


@app.post("/api/site/form", status_code=201)
async def create_site_form_lead(
    request: Request,
    secret: str = Query(default=""),
    x_form_secret: str = Header(default=""),
) -> dict:
    settings = load_settings()
    if settings.form_secret and (x_form_secret or secret) != settings.form_secret:
        raise HTTPException(status_code=401, detail="invalid form secret")

    try:
        form = parse_site_lead_payload(await _request_payload(request))
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "lead_id": lead.get("id"),
        "contact_id": contact.get("id"),
    }


@app.post("/sync/{entity_type}")
def sync_entity(entity_type: str) -> dict:
    try:
        service = _sync_service()
        try:
            return service.sync_entity(entity_type)
        finally:
            service.client.close()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/sync/all")
def sync_all() -> list[dict]:
    service = _sync_service()
    try:
        return service.sync_all()
    finally:
        service.client.close()


@app.get("/entities/{entity_type}")
def list_entities(
    entity_type: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    return _repo().list_entities(entity_type, limit=limit, offset=offset)


@app.get("/analytics/leads-by-status")
def leads_by_status() -> list[dict]:
    return AnalyticsService(_repo()).leads_by_status()


@app.get("/analytics/tasks-summary")
def tasks_summary() -> dict:
    return AnalyticsService(_repo()).tasks_summary()


@app.post("/conversations/discover")
def discover_conversations() -> dict:
    settings = load_settings()
    return ConversationPipeline(_repo()).discover_from_hub(settings.account_key)


@app.post("/conversations/analyze")
def analyze_conversations(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    settings = load_settings()
    return ConversationPipeline(_repo()).analyze_transcribed(settings.account_key, limit=limit)


@app.get("/conversations")
def list_conversations(limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    settings = load_settings()
    return _repo().list_conversation_records(settings.account_key, limit=limit)


@app.get("/conversations/analysis")
def list_conversation_analysis(limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    settings = load_settings()
    return _repo().list_conversation_analyses(settings.account_key, limit=limit)


@app.get("/call-checklist-steps")
def list_call_checklist_steps(active: str = Query(default="true")) -> dict:
    settings = load_settings()
    return {
        "ok": True,
        "account_key": settings.account_key,
        "steps": _repo().list_call_checklist_steps(settings.account_key, active=_active_filter_value(active)),
    }


@app.post("/call-checklist-steps", status_code=201)
async def create_call_checklist_step(request: Request) -> dict:
    settings = load_settings()
    payload = await request.json()
    try:
        step = _repo().create_call_checklist_step(
            settings.account_key,
            slug=str(payload.get("slug") or "").strip(),
            label=str(payload.get("label") or "").strip(),
            hint=str(payload.get("hint") or "").strip(),
            order_index=int(payload.get("order_index") or 0),
            active=_optional_bool(payload, "active") if "active" in payload else True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "step": step}


@app.post("/call-checklist-steps/{step_id}")
async def update_call_checklist_step(step_id: int, request: Request) -> dict:
    settings = load_settings()
    payload = await request.json()
    try:
        step = _repo().update_call_checklist_step(
            settings.account_key,
            step_id,
            label=str(payload["label"]).strip() if "label" in payload else None,
            hint=str(payload["hint"]).strip() if "hint" in payload else None,
            order_index=int(payload["order_index"]) if "order_index" in payload else None,
            active=_optional_bool(payload, "active"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "step": step}


@app.post("/call-checklist-steps/{step_id}/delete")
def deactivate_call_checklist_step(step_id: int) -> dict:
    settings = load_settings()
    try:
        step = _repo().deactivate_call_checklist_step(settings.account_key, step_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "step": step}


@app.get("/admin/connections")
def list_admin_connections() -> list[dict]:
    return admin_connections(load_settings().data_root)


@app.post("/admin/connections/{user_key}/{account_key}/status")
async def update_admin_connection_status(user_key: str, account_key: str, request: Request) -> dict:
    payload = await request.json()
    return set_connection_status(
        user_key=user_key,
        account_key=account_key,
        status=str(payload.get("status") or ""),
        data_root=load_settings().data_root,
    )


@app.get("/admin/connections/{user_key}/{account_key}/settings")
def get_account_settings(user_key: str, account_key: str) -> dict:
    return load_account_settings(
        user_key=user_key,
        account_key=account_key,
        data_root=load_settings().data_root,
    )


@app.post("/admin/connections/{user_key}/{account_key}/settings")
async def update_account_settings(user_key: str, account_key: str, request: Request) -> dict:
    payload = await request.json()
    return save_account_settings(
        user_key=user_key,
        account_key=account_key,
        settings=payload,
        data_root=load_settings().data_root,
    )


@app.get("/admin/connections/{user_key}/{account_key}/users/{crm_user_id}/settings")
def get_user_settings(user_key: str, account_key: str, crm_user_id: str) -> dict:
    return load_user_settings(
        user_key=user_key,
        account_key=account_key,
        crm_user_id=crm_user_id,
        data_root=load_settings().data_root,
    )


@app.post("/admin/connections/{user_key}/{account_key}/users/{crm_user_id}/settings")
async def update_user_settings(user_key: str, account_key: str, crm_user_id: str, request: Request) -> dict:
    payload = await request.json()
    return save_user_settings(
        user_key=user_key,
        account_key=account_key,
        crm_user_id=crm_user_id,
        settings=payload,
        data_root=load_settings().data_root,
    )


@app.get("/analytics/pipeline-summary")
def pipeline_summary() -> dict:
    settings = load_settings()
    return AnalyticsService(_repo()).pipeline_summary(load_analytics_filter(settings.db_path))


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    settings = load_settings()
    analytics = AnalyticsService(_repo())
    analytics_filter = load_analytics_filter(settings.db_path)
    return render_dashboard(
        analytics.pipeline_summary(analytics_filter),
        analytics.tasks_summary(),
        filter_options=analytics.pipeline_filter_options(),
        active_filter=analytics_filter.to_json(),
    )
