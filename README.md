# amoCRM Analytics and Control Service

MVP-сервис для подключения к amoCRM, зеркалирования данных в локальную базу, аналитики и будущего управления CRM/Salesbot.

## Что уже заложено

- Официальный amoCRM API v4 клиент с пагинацией.
- Приватные amoCRM методы для Salesbot/digital pipeline, вынесенные отдельно.
- SQLite-хранилище `raw_entities` для зеркала данных.
- Sync-сервис для:
  - `leads`
  - `contacts`
  - `companies`
  - `tasks`
  - `users`
  - `pipelines`
  - `lead_custom_fields`
  - `salesbots`
- FastAPI ручки:
  - `GET /health`
  - `POST /sync/{entity_type}`
  - `POST /sync/all`
  - `GET /entities/{entity_type}`
  - `GET /analytics/leads-by-status`
  - `GET /analytics/tasks-summary`
- CLI:
  - `init-db`
  - `sync`
  - `summary`

## Быстрый старт

```powershell
cd C:\Users\User\Documents\amoCRM\amocrm_service
copy .env.example .env
```

Заполни `.env`:

```env
AMO_SUBDOMAIN=your_subdomain
AMO_ACCESS_TOKEN=your_long_lived_or_current_token
```

Установка:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e .
```

Инициализация БД:

```powershell
.\.venv\Scripts\python -m amocrm_service.cli init-db
```

Синхронизация:

```powershell
.\.venv\Scripts\python -m amocrm_service.cli sync --entity leads
.\.venv\Scripts\python -m amocrm_service.cli sync --all
```

API:

```powershell
.\.venv\Scripts\uvicorn amocrm_service.api:app --reload
```

## Важно

В проект не кладем токены, `.env`, OAuth secrets и экспорт персональных данных. База `data/amocrm_service.sqlite3` тоже локальная и не должна попадать в публичный репозиторий.

