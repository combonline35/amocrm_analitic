from __future__ import annotations

from types import SimpleNamespace

from amocrm_service.db import connect, init_db
from amocrm_service.repository import Repository
from amocrm_service.server import _condition_to_amo_filter, _formula_amo_filter_export


SETTINGS = SimpleNamespace(account_base_url="https://test.amocrm.ru", account_key="test")


def _repo(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    init_db(db_path)
    return Repository(connect(db_path))


def test_amo_url_maps_manager_and_month(tmp_path):
    repo = _repo(tmp_path)
    formula = {
        "op": "count",
        "from": "leads",
        "where": [
            {"field": "responsible_user_id", "op": "eq", "value": 123},
            {"field": "created_at", "op": "this_month", "value": None},
        ],
    }
    export = _formula_amo_filter_export(repo, SETTINGS, formula)
    assert export["unmapped"] == []
    assert "filter%5Bmain_user%5D%5B%5D=123" in export["url"]
    assert "filter%5Bdate_preset%5D=current_month" in export["url"]
    assert "filter_date_switch=created" in export["url"]
    assert export["url"].startswith("https://test.amocrm.ru/leads/list/")
    assert "useFilter=y" in export["url"]


def test_amo_url_unmapped_on_not_empty(tmp_path):
    repo = _repo(tmp_path)
    formula = {
        "op": "count",
        "from": "leads",
        "where": [
            {"field": "cf_555", "op": "not_empty", "value": None},
            {"field": "created_at", "op": "this_month", "value": None},
        ],
    }
    export = _formula_amo_filter_export(repo, SETTINGS, formula)
    # not_empty в amo-URL не переводится — ссылка невалидна для показа.
    assert export["unmapped"] == ["cf_555 not_empty"]


def test_amo_url_cf_still_works(tmp_path):
    repo = _repo(tmp_path)
    parts, mapped = _condition_to_amo_filter(repo, {"field": "cf_10", "op": "eq", "value": "1"})
    assert mapped
    assert parts == ["filter%5Bcf%5D%5B10%5D%5B%5D=1"]
