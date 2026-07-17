# Правила работы

## Роли
Пользователь — голова (архитектура, решения, ТЗ). Claude Code — руки (исполняет, докладывает).

## Жёсткие правила
1. **Сервер не поднимать и не перезапускать** — это делает только пользователь. Никаких `python -m amocrm_service.server`.
2. Ритм: разведка (чтение) → правка → проверки → **СТОП на git diff** → ждать «ок» → коммит. Без явного «ок» не коммитить.
3. Каждая правка — на своей ветке от main. Влитие только по команде.
4. Если ТЗ расходится с кодом — **СТОП**, показать расхождение, не выполнять вслепую.
5. Не менять боевые данные, не трогать production-БД (`data/` в .gitignore — это рабочая база, руками не править).

## Окружение
- Windows / PowerShell. Python только через venv: `.\.venv\Scripts\python.exe`
- Тесты: `.\.venv\Scripts\python.exe -m pytest -q`
- Известное падение `test_conversations` (sentiment == 'error') — допустимо, нужен OPENROUTER_API_KEY. Остальные должны быть зелёные.
- Ключи (OPENROUTER_API_KEY и др.) — из `.env`, для probe-скриптов подгружаются через `amocrm_service.config._load_env_file(Path(".env"))`.

## Проверки после правки
- `py_compile` изменённых файлов: `.\.venv\Scripts\python.exe -m py_compile <файлы>`
- полный pytest
- `git diff --stat` — показать, что затронуто

## Опасность: f-строки в dashboard.py
Весь фронт (HTML/CSS/JS) рендерится через f-строки. Любые одиночные `{ }` в тексте/комментариях/JS-объектах Python примет за подстановку → NameError на рендере (реальный прецедент — hotfix e4434ce: `{widgets}` в JS-комментарии уронил все страницы).
- в JS-коде и комментариях внутри f-строк скобки удваивать: `{{ }}`
- py_compile такое НЕ ловит — только реальный рендер (см. `tests/test_render_smoke.py`, гоняется обычным pytest)

## Структура
- `src/amocrm_service/formula_engine.py` — движок формул: SQL-агрегации по `raw_entities` (SQLite, payload_json), словарь полей (`FormulaDictionaryService`), `FormulaValue` (scalar/series/table), diagnose.
- `src/amocrm_service/ai_formula.py` — AI-слой конструктора: системный промпт, компактный словарь для модели, rule-заготовки (`_simple_count_draft` и др.), авторемонты формул (temporal-условия, group_by, ×100), валидация.
- `src/amocrm_service/dashboard.py` — весь фронт BI-дашборда одним модулем: HTML/CSS/JS в Python format-строке (фигурные скобки в JS удваиваются `{{ }}`), рендер виджетов, конструктор формул.
- `src/amocrm_service/server.py` — HTTP-сервер и API-роуты (`/api/formula/*`, `/api/ai/*`, виджеты, drilldown). **Не запускать.**
- `src/amocrm_service/sync.py`, `auto_sync.py` — синхронизация amoCRM → `raw_entities`; `repository.py` + `db.py` — SQLite-хранилище и expression-индексы.
- `tests/` — pytest; `scripts/` — probe- и diagnose-скрипты.

## Полезные инструменты (scripts/*probe*.py)
Все probe читают ключ из `.env` и не меняют код — можно гонять свободно:
- `ai_formula_probe.py` — как build_formula_draft разбирает периоды: что перехватывают rule-заготовки, что уходит в модель (2 прохода, во втором заготовки отключены monkeypatch'ем).
- `ai_formula_probe_dates.py` — плавающие периоды («текущий месяц») → пресеты this_month/previous_month, названные («июль 2026») → фиксированные date_between/eq.
- `ai_formula_probe_group_by.py` — ремонт выдуманных group_by: `cf_источник` → реальный `cf_<id>` по label, мусорное поле → AiFormulaError; блок B — живой прогон «топ-5 источников заявок» с evaluate.
- `ai_formula_probe_phrasing.py` — 7 естественных формулировок одного фильтра по select-полю («заполнено X», «стоит X», падежные формы).
- `ai_formula_probe_select.py` — фильтр по кастомному select-полю значением из enum (на реальном словаре аккаунта).
- `ai_formula_probe_users.py` — фильтр по имени менеджера через справочник users (имя берётся из данных аккаунта, не хардкод).
- `ai_formula_probe_cross_period.py` — конверсия с двумя разными периодами (числитель по дате замера, знаменатель по дате создания): детерминированная проверка ремонтов + живой прогон с evaluate на мини-хабе.
- `ai_formula_probe_titles.py` — короткие человеческие title колонок и ширины от модели (запрос-«каша» по Менеджеру с 4 колонками).
- `perf_probe.py` — профилирование: где уходит время — LLM, SQL evaluate или diagnose (холодный/тёплый прогон).
