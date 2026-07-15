"""Профилирование формул: где уходит время — LLM, SQL движка или diagnose.

Замеряет на реальном словаре donpotolok:
  а) build_formula_draft (вызов LLM) для простого и табличного запроса;
  б) engine.evaluate для простой и табличной формулы (2 прогона: холодный/тёплый);
  в) engine.diagnose табличной формулы (его зовёт /api/ai/formula/draft и evaluate-роут);
  г) SQL-профиль табличной формулы: число запросов, самый медленный + EXPLAIN;
  д) сравнение live-фильтра источника (json_extract IN) со старым JOIN-снапшотом.

Запуск: .venv\\Scripts\\python.exe scripts\\perf_probe.py [--no-llm]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amocrm_service.config import _load_env_file
from amocrm_service.db import connect
from amocrm_service.formula_engine import FormulaDictionaryService, FormulaEngine
from amocrm_service.repository import Repository
import amocrm_service.ai_formula as af

_load_env_file(Path(".env"))

DB = "data/users/default/accounts/donpotolok/hub.sqlite3"
SOURCE_ID = 1
GROUP_CF = "cf_297945"   # Менеджер
FLAG_CF = "cf_668657"    # Целевой

THIS_MONTH = {"field": "created_at", "op": "this_month", "value": None, "value_type": "date"}
PREV_MONTH = {"field": "created_at", "op": "previous_month", "value": None, "value_type": "date"}
FLAG_EQ_1 = {"field": FLAG_CF, "op": "eq", "value": "1"}


def _count(where, **extra):
    node = {"op": "count", "from": "leads", "source_id": SOURCE_ID, "group_by": GROUP_CF, "where": [dict(c) for c in where]}
    node.update(extra)
    return node


def _sum_price(where):
    return {"op": "sum", "from": "leads", "field": "price", "source_id": SOURCE_ID, "group_by": GROUP_CF, "where": [dict(c) for c in where]}


SIMPLE_FORMULA = {"op": "count", "from": "leads", "source_id": SOURCE_ID, "where": [dict(THIS_MONTH)]}

TABLE_FORMULA = {
    "op": "table",
    "from": "leads",
    "source_id": SOURCE_ID,
    "group_by": GROUP_CF,
    "columns": {
        "Всего": _count([THIS_MONTH]),
        "Целевые": _count([THIS_MONTH, FLAG_EQ_1]),
        "Конверсия": {"op": "divide", "left": _count([THIS_MONTH, FLAG_EQ_1]), "right": _count([THIS_MONTH])},
        "Бюджет": _sum_price([THIS_MONTH]),
        "Средний чек": {"op": "divide", "left": _sum_price([THIS_MONTH]), "right": _count([THIS_MONTH])},
        "Прошлый месяц": _count([PREV_MONTH]),
    },
}


class _Cursor:
    def __init__(self, cursor, record):
        self._cursor = cursor
        self._record = record

    def fetchall(self):
        started = time.perf_counter()
        rows = self._cursor.fetchall()
        self._record["time"] += time.perf_counter() - started
        return rows

    def fetchone(self):
        started = time.perf_counter()
        row = self._cursor.fetchone()
        self._record["time"] += time.perf_counter() - started
        return row

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class TracingConn:
    def __init__(self, conn):
        self._conn = conn
        self.calls: list[dict] = []

    def execute(self, sql, params=()):
        record = {"sql": sql, "params": params, "time": 0.0}
        started = time.perf_counter()
        cursor = self._conn.execute(sql, params)
        record["time"] = time.perf_counter() - started
        self.calls.append(record)
        return _Cursor(cursor, record)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _llm_time(dictionary, sources, default_source, users, prompt):
    for rule_name in (
        "_measurement_conversion_draft",
        "_lost_reason_ad_platform_current_month_draft",
        "_lost_reason_current_month_draft",
        "_measurement_assigned_count_draft",
        "_simple_count_draft",
    ):
        setattr(af, rule_name, lambda **kwargs: None)
    started = time.perf_counter()
    for attempt in range(3):
        try:
            af.build_formula_draft(user_prompt=prompt, dictionary=dictionary, sources=sources,
                                   default_source=default_source, users=users)
            break
        except af.AiFormulaError as exc:
            if "недоступен" not in str(exc) or attempt == 2:
                raise
    return time.perf_counter() - started


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")
    no_llm = "--no-llm" in sys.argv

    repo = Repository(connect(Path(DB)))
    engine = FormulaEngine(repo)
    timings: list[tuple[str, float]] = []

    # --- а) LLM ---
    if not no_llm and af.ai_provider_config().get("api_key"):
        dictionary = FormulaDictionaryService(repo).build()
        sources = repo.list_sync_sources("donpotolok")
        default_source = next((s for s in sources if "прода" in str(s.get("name") or "").casefold()), None)
        users = repo.all_payloads("users")
        timings.append(("LLM: простой запрос (build_formula_draft)", _llm_time(
            dictionary, sources, default_source, users, "сколько сделок за текущий месяц")))
        timings.append(("LLM: табличный запрос (build_formula_draft)", _llm_time(
            dictionary, sources, default_source, users,
            "Таблица по полю Менеджер за текущий месяц: всего, целевых, конверсия, бюджет, средний чек, прошлый месяц")))

    # --- б) evaluate ---
    for label, formula in (("простой count", SIMPLE_FORMULA), ("таблица 6 колонок", TABLE_FORMULA)):
        for run in ("холодный", "тёплый"):
            started = time.perf_counter()
            engine.evaluate(json.loads(json.dumps(formula)))
            timings.append((f"evaluate: {label} ({run})", time.perf_counter() - started))

    # --- в) diagnose ---
    started = time.perf_counter()
    engine.diagnose(json.loads(json.dumps(TABLE_FORMULA)))
    timings.append(("diagnose: таблица 6 колонок", time.perf_counter() - started))

    print("=" * 78)
    print("ЭТАП -> СЕКУНДЫ")
    for label, seconds in timings:
        print(f"  {label:<52} {seconds:8.2f}s")

    # --- г) SQL-профиль таблицы ---
    plain_conn = repo.conn
    tracing = TracingConn(plain_conn)
    repo.conn = tracing
    engine_traced = FormulaEngine(repo)
    started = time.perf_counter()
    engine_traced.evaluate(json.loads(json.dumps(TABLE_FORMULA)))
    total = time.perf_counter() - started
    repo.conn = plain_conn
    calls = tracing.calls
    sql_total = sum(c["time"] for c in calls)
    print("\n" + "=" * 78)
    print(f"SQL-ПРОФИЛЬ evaluate(таблица): запросов={len(calls)}, SQL-время={sql_total:.2f}s из {total:.2f}s")
    slowest = max(calls, key=lambda c: c["time"])
    print(f"\nСамый медленный запрос ({slowest['time']:.2f}s):")
    print(" ".join(slowest["sql"].split())[:800])
    print("params:", list(slowest["params"])[:10])
    print("\nEXPLAIN QUERY PLAN:")
    for row in plain_conn.execute("EXPLAIN QUERY PLAN " + slowest["sql"], slowest["params"]).fetchall():
        print("  ", tuple(row))

    # --- д) live-источник vs JOIN-снапшот ---
    print("\n" + "=" * 78)
    print("ИСТОЧНИК: live-фильтр vs старый JOIN")
    started = time.perf_counter()
    live = engine.evaluate({"op": "count", "from": "leads", "source_id": SOURCE_ID})
    live_time = time.perf_counter() - started
    started = time.perf_counter()
    join_row = plain_conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM raw_entities
        JOIN sync_source_entities sse
          ON sse.entity_type = raw_entities.entity_type
         AND sse.entity_id = raw_entities.entity_id
         AND sse.source_id = ?
        WHERE raw_entities.entity_type = 'leads'
        """,
        (SOURCE_ID,),
    ).fetchone()
    join_time = time.perf_counter() - started
    print(f"  live (json_extract pipeline/status IN): {live_time:6.2f}s -> {live.get('value')}")
    print(f"  JOIN sync_source_entities (снапшот):    {join_time:6.2f}s -> {join_row['c']}")


if __name__ == "__main__":
    main()
