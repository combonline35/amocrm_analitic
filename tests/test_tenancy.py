from __future__ import annotations

from amocrm_service.tenancy import create_connection, list_connections, registry_path


def test_connection_registry_lists_account_without_opening_metrics(tmp_path):
    result = create_connection(
        user_key="Admin User",
        account_key="donpotolok",
        subdomain="donpotolok",
        access_token="token",
        data_root=tmp_path,
    )

    assert registry_path(tmp_path).exists()

    fast_connections = list_connections(tmp_path, include_metrics=False)
    assert fast_connections == [{
        "user_key": "Admin-User",
        "account_key": "donpotolok",
        "db_path": result["db_path"],
        "env_path": result["env_path"],
        "subdomain": "donpotolok",
        "base_domain": "amocrm.ru",
        "entities_count": 0,
        "entity_types": 0,
        "queue_pending": 0,
        "queue_running": 0,
        "queue_failed": 0,
        "latest_job": None,
        "latest_error": None,
        "entities": [],
        "queue": [],
    }]

    metric_connections = list_connections(tmp_path)
    assert metric_connections[0]["user_key"] == "Admin-User"
    assert metric_connections[0]["account_key"] == "donpotolok"
    assert metric_connections[0]["entities_count"] == 0
