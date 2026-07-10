# amoCRM Hub and Dashboards Architecture

## Целевая идея

Сервис делится на две большие части:

1. **Единый amoCRM-хаб**  
   Подключает аккаунты amoCRM, забирает данные, хранит локальное зеркало и дает единый слой запросов.

2. **Дашборды поверх хаба**  
   Используют одни и те же данные хаба, но показывают разные управленческие интерфейсы: активность менеджеров, аналитику продаж, кастомные отчеты, будущие финансовые или операционные панели.

Так мы не дублируем интеграции и не пишем отдельную синхронизацию под каждый экран.

## Слой 1. Hub

Hub отвечает только за подключение, сбор и хранение данных.

Основные модули:

- `config.py` - настройки аккаунта, пути к базе, токены.
- `amocrm/client.py` - клиент amoCRM API.
- `sync.py` - первичная выгрузка, перевыгрузка, пакетная синхронизация.
- `repository.py` - запись и чтение локального зеркала.
- `db.py` - схема SQLite.
- `tenancy.py` - несколько пользователей и аккаунтов.
- webhook `/api/amo/webhook` - быстрые сигналы об изменениях из amoCRM.
- queue `/api/sync-queue/*` - очередь точечных обновлений.

Главная единица данных хаба сейчас:

```text
raw_entities
entity_type
entity_id
payload_json
updated_at
synced_at
```

Дополнительные индексы:

```text
entity_relations
entity_custom_field_values
webhook_events
sync_queue
sync_jobs
sync_runs
```

## Слой 2. Shared Analytics

Это общий аналитический слой между хабом и дашбордами.

Основные модули:

- `analytics.py` - базовые показатели по сделкам, задачам, воронкам.
- `analytics_query.py` - универсальный query-builder для кастомных отчетов.
- `filters.py` - сохраненные фильтры аналитики.

Смысл слоя: дашборды не должны напрямую разбирать сырой JSON amoCRM, если показатель уже можно посчитать через общий запрос.

## Слой 3. Dashboard Modules

Каждый дашборд должен быть отдельным модулем поверх хаба.

Текущие дашборды:

### 1. Sales Analytics Dashboard

Страницы:

```text
/dashboard
/settings
```

Код:

- `dashboard.py` - HTML/JS интерфейс дашборда и конструктора.
- `widgets.py` - сохранение виджетов.
- `analytics_query.py` - расчет кастомных таблиц и KPI.
- server endpoints:
  - `GET /api/dashboard-widgets`
  - `POST /api/dashboard-widgets`
  - `POST /api/analytics/query`
  - `GET /api/analytics/fields`
  - `GET /api/analytics/field-values`

Назначение:

- настраиваемые KPI;
- таблицы по сделкам;
- группировки по полям;
- фильтры;
- сохранение виджетов на дашборд.

### 2. Activity Pulse Dashboard

Страница:

```text
/activity
```

Код:

- `activity.py` - нормализация действий, веса, звонки, простои, пульс по 15 минут.
- `_render_activity_page` в `server.py` - интерфейс панели активности.
- endpoint `GET /api/activity/summary`.

Назначение:

- активность менеджеров;
- звонки входящие/исходящие;
- выполненные задачи;
- смены этапов;
- заметки;
- простои;
- индекс активности.

## Как должны добавляться следующие дашборды

Новый дашборд не должен создавать собственную интеграцию amoCRM.

Правильный путь:

1. Берем данные из `Repository`.
2. Если показатель общий - добавляем его в `analytics.py` или `analytics_query.py`.
3. Если показатель специфический - создаем отдельный модуль, например:

```text
finance_dashboard.py
quality_dashboard.py
operator_dashboard.py
```

4. В `server.py` добавляем route и API endpoint.
5. Сохраняемые настройки кладем в отдельный JSON рядом с аккаунтом:

```text
data/users/{user}/accounts/{account}/dashboard_widgets.json
data/users/{user}/accounts/{account}/activity_settings.json
data/users/{user}/accounts/{account}/finance_widgets.json
```

## Важное правило

Хаб отвечает на вопрос:

```text
Что есть в amoCRM и когда это обновилось?
```

Дашборды отвечают на вопрос:

```text
Как это показать и какие управленческие выводы сделать?
```

Если это правило держать, проект не превратится в один большой файл с бизнес-логикой, UI и синхронизацией вперемешку.

## Ближайший план разделения

1. Вынести HTML/JS конструктора из `dashboard.py` на более мелкие функции или отдельный пакет `dashboards/sales`.
2. Вынести `_render_activity_page` из `server.py` в отдельный модуль `dashboards/activity.py`.
3. Оставить `server.py` как тонкий routing layer.
4. Добавить страницу `/hub` или `/connections`, где управляются только аккаунты, синхронизация, webhook и состояние базы.
5. Добавить навигацию:

```text
Hub
Sales Analytics
Activity Pulse
Settings
Admin
```

## Где что лежит сейчас

```text
src/amocrm_service/db.py
src/amocrm_service/repository.py
src/amocrm_service/sync.py
src/amocrm_service/amocrm/client.py
```

Это хаб.

```text
src/amocrm_service/dashboard.py
src/amocrm_service/analytics.py
src/amocrm_service/analytics_query.py
src/amocrm_service/widgets.py
```

Это настраиваемый аналитический дашборд.

```text
src/amocrm_service/activity.py
```

Это панель активности.

```text
src/amocrm_service/server.py
```

Сейчас содержит routing, страницы и API. Его нужно постепенно облегчать.
