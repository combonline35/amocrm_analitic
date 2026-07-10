from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any
import json

from amocrm_service.config import Settings, load_settings
from amocrm_service.db import connect, init_db
from amocrm_service.repository import Repository


SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")

REGISTRY_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS account_registry (
    user_key TEXT NOT NULL,
    account_key TEXT NOT NULL,
    subdomain TEXT,
    base_domain TEXT,
    env_path TEXT NOT NULL,
    db_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_key, account_key)
);

CREATE INDEX IF NOT EXISTS idx_account_registry_status
ON account_registry(status, updated_at);
"""


def safe_key(value: str, default: str = "default") -> str:
    cleaned = SAFE_KEY_RE.sub("-", value.strip()).strip("-._")
    return cleaned or default


def account_dir(data_root: Path, user_key: str, account_key: str) -> Path:
    return data_root / safe_key(user_key) / "accounts" / safe_key(account_key)


def account_env_path(data_root: Path, user_key: str, account_key: str) -> Path:
    return account_dir(data_root, user_key, account_key) / "account.env"


def account_settings_path(data_root: Path, user_key: str, account_key: str) -> Path:
    return account_dir(data_root, user_key, account_key) / "account_settings.json"


def user_settings_path(data_root: Path, user_key: str, account_key: str, crm_user_id: str) -> Path:
    return account_dir(data_root, user_key, account_key) / "users" / safe_key(crm_user_id) / "settings.json"


def registry_path(data_root: Path | None = None) -> Path:
    root = data_root or load_settings().data_root
    return root / "registry.sqlite3"


def connect_registry(data_root: Path | None = None) -> sqlite3.Connection:
    path = registry_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(REGISTRY_SCHEMA)
    conn.commit()
    return conn


def upsert_connection_registry(
    *,
    user_key: str,
    account_key: str,
    subdomain: str = "",
    base_domain: str = "amocrm.ru",
    env_path: Path,
    db_path: Path,
    data_root: Path | None = None,
) -> None:
    from amocrm_service.repository import utc_now

    now = utc_now()
    conn = connect_registry(data_root)
    try:
        conn.execute(
            """
            INSERT INTO account_registry(
                user_key, account_key, subdomain, base_domain, env_path, db_path, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(user_key, account_key) DO UPDATE SET
                subdomain = excluded.subdomain,
                base_domain = excluded.base_domain,
                env_path = excluded.env_path,
                db_path = excluded.db_path,
                status = 'active',
                updated_at = excluded.updated_at
            """,
            (
                safe_key(user_key),
                safe_key(account_key),
                subdomain.strip(),
                base_domain.strip() or "amocrm.ru",
                str(env_path),
                str(db_path),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _registered_connections(data_root: Path, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    conn = connect_registry(data_root)
    try:
        where = "" if include_inactive else "WHERE status = 'active'"
        rows = conn.execute(
            f"""
            SELECT user_key, account_key, subdomain, base_domain, env_path, db_path, status, created_at, updated_at
            FROM account_registry
            {where}
            ORDER BY user_key, account_key
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _filesystem_connections(data_root: Path) -> list[dict[str, Any]]:
    connections: list[dict[str, Any]] = []
    for db_path in data_root.glob("*/accounts/*/hub.sqlite3"):
        account_key = db_path.parent.name
        user_key = db_path.parent.parent.parent.name
        env_path = account_env_path(data_root, user_key, account_key)
        settings = load_settings(account_key=account_key, user_key=user_key, data_root=data_root)
        connections.append({
            "user_key": user_key,
            "account_key": account_key,
            "subdomain": settings.subdomain,
            "base_domain": settings.base_domain,
            "env_path": str(env_path),
            "db_path": str(db_path),
            "status": "active",
        })
    return connections


def create_connection(
    *,
    user_key: str,
    account_key: str,
    subdomain: str,
    access_token: str,
    base_domain: str = "amocrm.ru",
    data_root: Path | None = None,
) -> dict[str, Any]:
    root = data_root or load_settings().data_root
    user_key = safe_key(user_key)
    account_key = safe_key(account_key or subdomain)
    directory = account_dir(root, user_key, account_key)
    directory.mkdir(parents=True, exist_ok=True)
    env_path = directory / "account.env"
    lines = [
        f"AMO_SUBDOMAIN={subdomain.strip()}",
        f"AMO_BASE_DOMAIN={base_domain.strip() or 'amocrm.ru'}",
        f"AMO_ACCESS_TOKEN={access_token.strip()}",
    ]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    settings = load_settings(account_key=account_key, user_key=user_key, data_root=root)
    init_db(settings.db_path)
    conn = connect(settings.db_path)
    try:
        Repository(conn).upsert_account(
            settings.account_key,
            subdomain=settings.subdomain or None,
            base_domain=settings.base_domain,
        )
    finally:
        conn.close()
    upsert_connection_registry(
        user_key=user_key,
        account_key=account_key,
        subdomain=settings.subdomain,
        base_domain=settings.base_domain,
        env_path=env_path,
        db_path=settings.db_path,
        data_root=root,
    )
    return {
        "user_key": user_key,
        "account_key": account_key,
        "env_path": str(env_path),
        "db_path": str(settings.db_path),
    }


def set_connection_status(
    *,
    user_key: str,
    account_key: str,
    status: str,
    data_root: Path | None = None,
) -> dict[str, Any]:
    from amocrm_service.repository import utc_now

    allowed = {"active", "disabled", "archived"}
    if status not in allowed:
        raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
    root = data_root or load_settings().data_root
    conn = connect_registry(root)
    try:
        cursor = conn.execute(
            """
            UPDATE account_registry
            SET status = ?, updated_at = ?
            WHERE user_key = ? AND account_key = ?
            """,
            (status, utc_now(), safe_key(user_key), safe_key(account_key)),
        )
        conn.commit()
        if not cursor.rowcount:
            raise ValueError("connection is not registered")
        return {
            "user_key": safe_key(user_key),
            "account_key": safe_key(account_key),
            "status": status,
        }
    finally:
        conn.close()


def admin_connections(data_root: Path | None = None) -> list[dict[str, Any]]:
    root = data_root or load_settings().data_root
    registered = _registered_connections(root, include_inactive=True)
    if not registered:
        list_connections(root, include_metrics=False)
        registered = _registered_connections(root, include_inactive=True)
    return registered


def load_account_settings(
    *,
    user_key: str,
    account_key: str,
    data_root: Path | None = None,
) -> dict[str, Any]:
    root = data_root or load_settings().data_root
    path = account_settings_path(root, user_key, account_key)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig") or "{}")


def save_account_settings(
    *,
    user_key: str,
    account_key: str,
    settings: dict[str, Any],
    data_root: Path | None = None,
) -> dict[str, Any]:
    root = data_root or load_settings().data_root
    path = account_settings_path(root, user_key, account_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(path), "settings": settings}


def load_user_settings(
    *,
    user_key: str,
    account_key: str,
    crm_user_id: str,
    data_root: Path | None = None,
) -> dict[str, Any]:
    root = data_root or load_settings().data_root
    path = user_settings_path(root, user_key, account_key, crm_user_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig") or "{}")


def save_user_settings(
    *,
    user_key: str,
    account_key: str,
    crm_user_id: str,
    settings: dict[str, Any],
    data_root: Path | None = None,
) -> dict[str, Any]:
    root = data_root or load_settings().data_root
    path = user_settings_path(root, user_key, account_key, crm_user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(path), "settings": settings}


def list_connections(data_root: Path | None = None, *, include_metrics: bool = True) -> list[dict[str, Any]]:
    root = data_root or load_settings().data_root
    registered = _registered_connections(root)
    all_registered_keys = {
        (item["user_key"], item["account_key"])
        for item in _registered_connections(root, include_inactive=True)
    }
    discovered = _filesystem_connections(root)
    by_key = {
        (item["user_key"], item["account_key"]): item
        for item in registered
    }
    for item in discovered:
        key = (item["user_key"], item["account_key"])
        if key in all_registered_keys and key not in by_key:
            continue
        if key not in by_key:
            by_key[key] = item
            upsert_connection_registry(
                user_key=item["user_key"],
                account_key=item["account_key"],
                subdomain=str(item.get("subdomain") or ""),
                base_domain=str(item.get("base_domain") or "amocrm.ru"),
                env_path=Path(str(item["env_path"])),
                db_path=Path(str(item["db_path"])),
                data_root=root,
            )

    connections: list[dict[str, Any]] = []
    for item in by_key.values():
        account_key = item["account_key"]
        user_key = item["user_key"]
        db_path = Path(str(item["db_path"]))
        if not include_metrics:
            connections.append({
                "user_key": user_key,
                "account_key": account_key,
                "db_path": str(db_path),
                "env_path": str(item.get("env_path") or account_env_path(root, user_key, account_key)),
                "subdomain": item.get("subdomain") or "",
                "base_domain": item.get("base_domain") or "amocrm.ru",
                "entities_count": 0,
                "entity_types": 0,
                "queue_pending": 0,
                "queue_running": 0,
                "queue_failed": 0,
                "latest_job": None,
                "latest_error": None,
                "entities": [],
                "queue": [],
            })
            continue
        settings = load_settings(account_key=account_key, user_key=user_key, data_root=root)
        init_db(settings.db_path)
        conn = connect(settings.db_path)
        try:
            repo = Repository(conn)
            entities = repo.hub_entity_overview()
            total_entities = sum(int(row["items_count"] or 0) for row in entities)
            queue = repo.queue_summary(settings.account_key)
            queue_counts = repo.queue_status_counts(settings.account_key)
            jobs = repo.latest_sync_jobs(settings.account_key, 1)
            errors = repo.latest_errors(1)
            connections.append({
                "user_key": user_key,
                "account_key": account_key,
                "db_path": str(settings.db_path),
                "env_path": str(item.get("env_path") or account_env_path(root, user_key, account_key)),
                "subdomain": settings.subdomain,
                "base_domain": settings.base_domain,
                "entities_count": total_entities,
                "entity_types": len(entities),
                "queue_pending": queue_counts.get("pending", 0),
                "queue_running": queue_counts.get("running", 0),
                "queue_failed": queue_counts.get("failed", 0),
                "latest_job": jobs[0] if jobs else None,
                "latest_error": errors[0] if errors else None,
                "entities": entities,
                "queue": queue,
            })
        finally:
            conn.close()
    connections.sort(key=lambda item: (item["user_key"], item["account_key"]))
    return connections
