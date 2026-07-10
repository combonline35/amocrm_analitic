# Auto Sync Hub

## Цель

Держать локальный хаб amoCRM актуальным в фоне, чтобы дашборды и контроль качества читали свежие данные из локальной базы, а не ходили в amoCRM на каждый экран.

## Аккаунт

Текущий рабочий профиль:

```text
user=admin
account=donpotolok-control
amoCRM=donpotolok.amocrm.ru
```

Настройки лежат здесь:

```text
data/users/admin/accounts/donpotolok-control/account_settings.json
```

## Группы обновления

```text
hot
  interval: 30 минут
  entities: leads, tasks, events

communications
  interval: 90 минут
  entities: lead_notes, contact_notes, company_notes, customer_notes

directory
  interval: 720 минут
  entities: pipelines, users, lead_custom_fields, contact_custom_fields, company_custom_fields
```

Одновременно запускается только один sync job на аккаунт. Если предыдущая выгрузка еще идет, следующий автосинк пропускается до следующего прохода worker.

## Как работает

1. Сервер запускает `sync-queue-worker`.
2. Worker каждые 30 секунд проходит по активным подключениям.
3. Для аккаунтов с `auto_sync.enabled=true` он проверяет due-группы.
4. Если группа просрочена и нет активной sync job, создается фоновая задача `auto_<group>`.
5. Статус задачи пишется в `sync_jobs`, данные пишутся в `raw_entities`.

## Проверка статуса

```text
http://127.0.0.1:8022/api/hub/background?user=admin&account=donpotolok-control
http://127.0.0.1:8022/api/connection/status?user=admin&account=donpotolok-control
```

Экран контроля:

```text
http://127.0.0.1:8023/quality?user=admin&account=donpotolok-control
```

Экран актуальности данных:

```text
http://127.0.0.1:8023/freshness?user=admin&account=donpotolok-control
```

Экран фильтров контроля:

```text
http://127.0.0.1:8023/quality-settings?user=admin&account=donpotolok-control
```

## Важно

Webhook-очередь остается отдельным быстрым механизмом точечных обновлений. Автосинк нужен как страховка и регулярное выравнивание локального зеркала с amoCRM.
