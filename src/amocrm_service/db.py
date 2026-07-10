from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS raw_entities (
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    name TEXT,
    payload_json TEXT NOT NULL,
    updated_at INTEGER,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_raw_entities_type ON raw_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_raw_entities_updated_at ON raw_entities(updated_at);

CREATE INDEX IF NOT EXISTS idx_raw_entities_type_synced
ON raw_entities(entity_type, synced_at);

CREATE INDEX IF NOT EXISTS idx_raw_entities_type_created
ON raw_entities(entity_type, CAST(json_extract(payload_json, '$.created_at') AS INTEGER));

CREATE INDEX IF NOT EXISTS idx_raw_entities_type_payload_updated
ON raw_entities(entity_type, CAST(json_extract(payload_json, '$.updated_at') AS INTEGER));

CREATE INDEX IF NOT EXISTS idx_raw_entities_type_responsible
ON raw_entities(entity_type, CAST(json_extract(payload_json, '$.responsible_user_id') AS INTEGER));

CREATE INDEX IF NOT EXISTS idx_raw_entities_type_status
ON raw_entities(entity_type, CAST(json_extract(payload_json, '$.status_id') AS INTEGER));

CREATE INDEX IF NOT EXISTS idx_raw_entities_type_pipeline
ON raw_entities(entity_type, CAST(json_extract(payload_json, '$.pipeline_id') AS INTEGER));

CREATE TABLE IF NOT EXISTS entity_relations (
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    payload_json TEXT,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (source_type, source_id, target_type, target_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_entity_relations_source
ON entity_relations(source_type, source_id);

CREATE INDEX IF NOT EXISTS idx_entity_relations_target
ON entity_relations(target_type, target_id);

CREATE TABLE IF NOT EXISTS entity_custom_field_values (
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    field_id INTEGER NOT NULL,
    field_code TEXT,
    field_name TEXT,
    value_index INTEGER NOT NULL,
    enum_id INTEGER,
    enum_code TEXT,
    value_text TEXT,
    value_num REAL,
    value_json TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, field_id, value_index)
);

CREATE INDEX IF NOT EXISTS idx_entity_custom_field_values_field
ON entity_custom_field_values(entity_type, field_id, value_text);

CREATE INDEX IF NOT EXISTS idx_entity_custom_field_values_entity
ON entity_custom_field_values(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_entity_custom_field_values_entity_field_name
ON entity_custom_field_values(entity_type, field_id, field_name);

CREATE TABLE IF NOT EXISTS amo_accounts (
    account_key TEXT PRIMARY KEY,
    account_id INTEGER,
    subdomain TEXT,
    base_domain TEXT,
    name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    payload_json TEXT NOT NULL,
    raw_body TEXT,
    received_at TEXT NOT NULL,
    processed_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_account_status
ON webhook_events(account_key, status, received_at);

CREATE INDEX IF NOT EXISTS idx_webhook_events_entity
ON webhook_events(account_key, entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_webhook_events_received
ON webhook_events(account_key, received_at);

CREATE TABLE IF NOT EXISTS sync_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'refresh',
    reason TEXT,
    payload_json TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    locked_at TEXT,
    last_error TEXT,
    UNIQUE(account_key, entity_type, entity_id, action)
);

CREATE INDEX IF NOT EXISTS idx_sync_queue_due
ON sync_queue(status, available_at);

CREATE INDEX IF NOT EXISTS idx_sync_queue_error
ON sync_queue(last_error, updated_at);

CREATE INDEX IF NOT EXISTS idx_sync_queue_status_updated
ON sync_queue(account_key, status, updated_at);

CREATE TABLE IF NOT EXISTS sync_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    entity_types_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    items_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_jobs_account_status
ON sync_jobs(account_key, status, started_at);

CREATE INDEX IF NOT EXISTS idx_sync_jobs_error
ON sync_jobs(error, finished_at);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    items_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_error
ON sync_runs(error, finished_at);

CREATE INDEX IF NOT EXISTS idx_sync_runs_status_finished
ON sync_runs(status, finished_at);

CREATE TABLE IF NOT EXISTS sync_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    name TEXT NOT NULL,
    entity_types_json TEXT NOT NULL,
    pipeline_ids_json TEXT NOT NULL DEFAULT '[]',
    status_ids_json TEXT NOT NULL DEFAULT '[]',
    last_job_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sync_sources_account
ON sync_sources(account_key, updated_at);

CREATE TABLE IF NOT EXISTS sync_source_entities (
    source_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (source_id, entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_sync_source_entities_entity
ON sync_source_entities(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_sync_source_entities_source_type_synced
ON sync_source_entities(source_id, entity_type, synced_at);

CREATE TABLE IF NOT EXISTS activity_daily_user (
    activity_date TEXT NOT NULL,
    slot_minutes INTEGER NOT NULL,
    user_key TEXT NOT NULL,
    user_id TEXT,
    user_name TEXT NOT NULL,
    activity_count INTEGER NOT NULL DEFAULT 0,
    activity_score INTEGER NOT NULL DEFAULT 0,
    active_minutes INTEGER NOT NULL DEFAULT 0,
    first_activity TEXT,
    last_activity TEXT,
    idle_minutes INTEGER NOT NULL DEFAULT 0,
    idle_periods_count INTEGER NOT NULL DEFAULT 0,
    idle_periods_json TEXT NOT NULL DEFAULT '[]',
    tasks_due INTEGER NOT NULL DEFAULT 0,
    tasks_completed INTEGER NOT NULL DEFAULT 0,
    tasks_overdue INTEGER NOT NULL DEFAULT 0,
    calls_out INTEGER NOT NULL DEFAULT 0,
    calls_in INTEGER NOT NULL DEFAULT 0,
    calls_missed INTEGER NOT NULL DEFAULT 0,
    notes INTEGER NOT NULL DEFAULT 0,
    leads_created INTEGER NOT NULL DEFAULT 0,
    stage_changes INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (activity_date, slot_minutes, user_key)
);

CREATE INDEX IF NOT EXISTS idx_activity_daily_user_date_score
ON activity_daily_user(activity_date, slot_minutes, activity_score);

CREATE TABLE IF NOT EXISTS activity_slots (
    activity_date TEXT NOT NULL,
    slot_minutes INTEGER NOT NULL,
    user_key TEXT NOT NULL,
    slot_index INTEGER NOT NULL,
    slot_label TEXT NOT NULL,
    activity_count INTEGER NOT NULL DEFAULT 0,
    activity_score INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (activity_date, slot_minutes, user_key, slot_index)
);

CREATE INDEX IF NOT EXISTS idx_activity_slots_date
ON activity_slots(activity_date, slot_minutes, slot_index);

CREATE TABLE IF NOT EXISTS lead_kpi_daily (
    activity_date TEXT NOT NULL,
    user_key TEXT NOT NULL,
    user_id TEXT,
    user_name TEXT NOT NULL,
    pipeline_id INTEGER NOT NULL DEFAULT 0,
    pipeline_name TEXT NOT NULL DEFAULT '',
    status_id INTEGER NOT NULL DEFAULT 0,
    status_name TEXT NOT NULL DEFAULT '',
    created_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    closed_count INTEGER NOT NULL DEFAULT 0,
    won_count INTEGER NOT NULL DEFAULT 0,
    lost_count INTEGER NOT NULL DEFAULT 0,
    open_count INTEGER NOT NULL DEFAULT 0,
    created_price INTEGER NOT NULL DEFAULT 0,
    closed_price INTEGER NOT NULL DEFAULT 0,
    open_price INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (activity_date, user_key, pipeline_id, status_id)
);

CREATE INDEX IF NOT EXISTS idx_lead_kpi_daily_date
ON lead_kpi_daily(activity_date, created_count, updated_count);

CREATE INDEX IF NOT EXISTS idx_lead_kpi_daily_user
ON lead_kpi_daily(user_key, activity_date);

CREATE TABLE IF NOT EXISTS conversation_records (
    account_key TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    lead_id TEXT,
    contact_id TEXT,
    direction TEXT NOT NULL,
    kind TEXT NOT NULL,
    recording_url TEXT,
    transcript_text TEXT,
    duration_seconds INTEGER,
    occurred_at INTEGER,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_key, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_records_lead
ON conversation_records(account_key, lead_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_conversation_records_status
ON conversation_records(account_key, status, updated_at);

CREATE INDEX IF NOT EXISTS idx_conversation_records_source
ON conversation_records(account_key, source_type, source_id);

CREATE TABLE IF NOT EXISTS conversation_analysis (
    account_key TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    sentiment TEXT NOT NULL,
    score INTEGER NOT NULL,
    next_step TEXT,
    objections_json TEXT NOT NULL,
    recommendations_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_key, conversation_id),
    FOREIGN KEY (account_key, conversation_id)
        REFERENCES conversation_records(account_key, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_analysis_score
ON conversation_analysis(account_key, score, updated_at);

CREATE TABLE IF NOT EXISTS call_checklist_step (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    slug TEXT NOT NULL,
    label TEXT NOT NULL,
    hint TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(account_key, slug)
);

CREATE INDEX IF NOT EXISTS idx_call_checklist_step_account_active
ON call_checklist_step(account_key, active, order_index);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
