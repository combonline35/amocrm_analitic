"""Целевые тесты безопасности хранилища виджетов (Правка 1, слайс 1а).

Тесты описывают ЖЕЛАЕМОЕ поведение хранилища:
- запись делает бэкап предыдущей версии (.bak);
- чтение не пишет в файл;
- битый файл не затирается дефолтом молча;
- запись атомарна (tmp-файл + os.replace).

На текущем коде большинство тестов падает — код чинится в слайсе 1б.
"""
from __future__ import annotations

import json
import time

import pytest

from amocrm_service.widgets import (
    dashboard_pages_path,
    load_dashboard_pages,
    load_widgets,
    save_widgets,
    widgets_path,
)


def _widget(widget_id: str, title: str) -> dict:
    return {
        "id": widget_id,
        "title": title,
        "widget_type": "analytics",
        "view": "number",
        "size": "medium",
        "formula": "none",
        "formula_spec": {},
        "query": {},
        "settings": {},
    }


def _read_ids(path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [item.get("id") for item in data]


def test_save_widgets_creates_backup(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    path = widgets_path(db_path)
    backup = path.with_suffix(path.suffix + ".bak")

    save_widgets(db_path, [_widget("widget-a", "Виджет A")])
    save_widgets(db_path, [_widget("widget-b", "Виджет B")])

    assert _read_ids(path) == ["widget-b"], "текущий файл должен содержать вторую версию"
    assert backup.exists(), "рядом с dashboard_widgets.json должен появиться .bak с предыдущей версией"
    assert _read_ids(backup) == ["widget-a"], ".bak должен содержать ПРЕДЫДУЩУЮ версию (виджет A)"


def test_load_widgets_does_not_rewrite_file(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    path = widgets_path(db_path)

    save_widgets(db_path, [_widget("widget-a", "Виджет A")])
    original_content = path.read_text(encoding="utf-8")
    original_mtime = path.stat().st_mtime_ns

    time.sleep(0.05)  # чтобы возможная перезапись гарантированно сдвинула mtime
    for _ in range(3):
        loaded = load_widgets(db_path)
        assert [w["id"] for w in loaded] == ["widget-a"]

    assert path.read_text(encoding="utf-8") == original_content, "чтение не должно менять содержимое файла"
    assert path.stat().st_mtime_ns == original_mtime, "чтение не должно перезаписывать файл (mtime сдвинулся)"


def test_corrupt_pages_not_replaced_with_default(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    path = dashboard_pages_path(db_path)
    corrupt_content = "{невалидный json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(corrupt_content, encoding="utf-8")

    load_dashboard_pages(db_path)

    # Исходное битое содержимое не должно быть потеряно молча: либо файл
    # оставлен как есть, либо отложен рядом как .corrupt/.bak для восстановления.
    candidates = [
        path,
        path.with_suffix(path.suffix + ".corrupt"),
        path.with_suffix(path.suffix + ".bak"),
    ]
    preserved = any(
        candidate.exists() and candidate.read_text(encoding="utf-8") == corrupt_content
        for candidate in candidates
    )
    assert preserved, (
        "битый dashboard_pages.json затёрт дефолтом: исходное содержимое "
        "не найдено ни в самом файле, ни в .corrupt/.bak"
    )


def test_corrupt_widgets_does_not_crash_and_preserves(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    path = widgets_path(db_path)
    corrupt_content = "{невалидный json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(corrupt_content, encoding="utf-8")

    try:
        result = load_widgets(db_path)
    except json.JSONDecodeError:
        pytest.fail("load_widgets не должен выбрасывать необработанный JSONDecodeError на битом файле")

    assert isinstance(result, list), "на битом файле load_widgets должен вернуть осмысленный список"

    candidates = [
        path,
        path.with_suffix(path.suffix + ".corrupt"),
        path.with_suffix(path.suffix + ".bak"),
    ]
    preserved = any(
        candidate.exists() and candidate.read_text(encoding="utf-8") == corrupt_content
        for candidate in candidates
    )
    assert preserved, (
        "битый dashboard_widgets.json потерян молча: исходное содержимое "
        "не найдено ни в самом файле, ни в .corrupt/.bak"
    )


def test_atomic_write_leaves_no_partial(tmp_path):
    try:
        from amocrm_service.widgets import atomic_write_json
    except ImportError:
        pytest.skip("хелпер атомарной записи будет добавлен в слайсе 1б")

    target = tmp_path / "data" / "sample.json"
    payload = {"items": [1, 2, 3], "title": "Проверка"}

    atomic_write_json(target, payload)

    assert json.loads(target.read_text(encoding="utf-8")) == payload
    leftovers = [
        p for p in target.parent.iterdir()
        if p.name != target.name and not p.name.endswith(".bak")
    ]
    assert leftovers == [], f"после атомарной записи не должно оставаться временных файлов: {leftovers}"

    # Повторная запись поверх существующего файла тоже не оставляет мусора
    atomic_write_json(target, {"items": []})
    assert json.loads(target.read_text(encoding="utf-8")) == {"items": []}
    leftovers = [
        p for p in target.parent.iterdir()
        if p.name != target.name and not p.name.endswith(".bak")
    ]
    assert leftovers == [], f"повторная запись оставила временные файлы: {leftovers}"
