from __future__ import annotations

from amocrm_service.tenancy import (
    admin_connections,
    create_connection,
    list_connections,
    load_account_settings,
    load_user_settings,
    save_account_settings,
    save_user_settings,
    set_connection_status,
)


def test_admin_can_disable_connection_without_losing_settings(tmp_path):
    create_connection(
        user_key="owner",
        account_key="client-a",
        subdomain="client-a",
        access_token="token",
        data_root=tmp_path,
    )

    save_account_settings(
        user_key="owner",
        account_key="client-a",
        settings={"conversation_ai": {"enabled": True}},
        data_root=tmp_path,
    )
    save_user_settings(
        user_key="owner",
        account_key="client-a",
        crm_user_id="42",
        settings={"dashboard": {"compact": True}},
        data_root=tmp_path,
    )

    assert len(list_connections(tmp_path, include_metrics=False)) == 1

    result = set_connection_status(
        user_key="owner",
        account_key="client-a",
        status="disabled",
        data_root=tmp_path,
    )

    assert result["status"] == "disabled"
    assert list_connections(tmp_path, include_metrics=False) == []

    admin_rows = admin_connections(tmp_path)
    assert len(admin_rows) == 1
    assert admin_rows[0]["status"] == "disabled"
    assert load_account_settings(
        user_key="owner",
        account_key="client-a",
        data_root=tmp_path,
    ) == {"conversation_ai": {"enabled": True}}
    assert load_user_settings(
        user_key="owner",
        account_key="client-a",
        crm_user_id="42",
        data_root=tmp_path,
    ) == {"dashboard": {"compact": True}}
