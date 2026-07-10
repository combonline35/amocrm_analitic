# Architecture

## Цель

Сервис должен стать прослойкой между amoCRM и нашими командами/аналитикой:

```text
amoCRM API + webhooks
        ↓
sync workers
        ↓
local DB
        ↓
analytics + control commands
        ↓
API / UI / Telegram / natural language agent
```

## Слои

### `amocrm.client`

Тонкий клиент amoCRM:

- официальный API v4;
- пагинация;
- отдельные приватные методы для Salesbot и digital pipeline.

### `repository`

Пока хранит данные как raw JSON. Это осознанно: на MVP быстрее зеркалировать amoCRM без потерь, а нормализованные таблицы добавить поверх, когда станет ясно, какие отчеты нужны чаще всего.

### `sync`

Синхронизирует сущности и пишет `sync_runs`, чтобы видеть успешные/упавшие запуски.

### `analytics`

Считает первые отчеты по локальному зеркалу, без запросов в amoCRM на каждый отчет.

### `api`

FastAPI поверх sync/analytics.

## Следующие шаги

1. Добавить OAuth flow и хранение токенов без ручного `AMO_ACCESS_TOKEN`.
2. Добавить webhooks endpoint для инкрементального обновления.
3. Нормализовать ключевые сущности: leads, statuses, users, tasks.
4. Добавить команды управления: move lead, create task, set field, create Salesbot.
5. Подключить уже изученный Salesbot catalog.

