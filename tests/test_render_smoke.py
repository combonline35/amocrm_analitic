"""Смоук рендера: страницы собираются из гигантских f-строк — любые
одиночные { } в тексте/JS-комментариях Python принимает за подстановку и
падает NameError/KeyError ТОЛЬКО на рендере (py_compile это не ловит,
реальный прецедент — hotfix e4434ce). Тест дёргает render_dashboard для
всех страниц без сети и БД.
"""
from __future__ import annotations

import pytest

from amocrm_service.dashboard import render_dashboard


@pytest.mark.parametrize("page", ["dashboard", "constructor", "settings"])
def test_render_dashboard_all_pages(page):
    html = render_dashboard(
        summary={"totals": {"total_price": 0}},
        tasks={},
        page=page,
        user_key="u",
        account_key="a",
    )
    assert isinstance(html, str)
    # Непустой HTML с ключевыми маркерами страницы, а не огрызок.
    assert len(html) > 10000
    assert "<script>" in html
    assert "formula-data-table" in html
