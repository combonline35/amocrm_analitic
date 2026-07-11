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
- HTTP API и веб-интерфейс: реализованы в `src/amocrm_service/server.py` (стандартная библиотека, без внешних веб-фреймворков). Полный список маршрутов см. в `do_GET`/`do_POST` внутри `server.py`.
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
.\.venv\Scripts\python -m amocrm_service.server --host 127.0.0.1 --port 8010
```

## Важно

В проект не кладем токены, `.env`, OAuth secrets и экспорт персональных данных. База `data/amocrm_service.sqlite3` тоже локальная и не должна попадать в публичный репозиторий.

