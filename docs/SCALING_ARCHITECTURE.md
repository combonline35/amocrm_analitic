# Scaling Architecture

## Target Shape

The hub is split into four layers:

1. **Connection registry**
   - Small central database: `data/users/registry.sqlite3`.
   - Stores active `user_key/account_key`, env path, DB path, subdomain and status.
   - Used by admin pages and background workers to find accounts without opening every account DB first.

2. **Account shards**
   - One operational SQLite database per amoCRM account:
     `data/users/<user>/accounts/<account>/hub.sqlite3`.
   - Raw amoCRM entities, webhooks, sync queue, sync jobs and indexes live inside the account shard.
   - This keeps tenants isolated and makes backup/delete/migration per client simple.

3. **Background workers**
   - Workers read the registry, then process each account shard independently.
   - A heavy account can fail or lag without blocking the whole service.
   - Workers must use `list_connections(include_metrics=False)` to avoid loading dashboard metrics for every account.

4. **Dashboard marts**
   - Dashboards should not scan raw JSON forever.
   - Current indexed tables are `entity_relations` and `entity_custom_field_values`.
   - Implemented account-local marts:
     - `activity_daily_user`
     - `activity_slots`
     - `lead_kpi_daily`
   - Next marts should include:
     - `sync_health_daily`

## Why Not One Big SQLite

One SQLite file is simple until many accounts start writing webhooks and sync jobs at the same time. Separate account shards give:

- smaller write locks;
- smaller files;
- easier account-level restore;
- safer tenant isolation;
- simpler move of one large client to Postgres later.

## When To Move A Shard To Postgres

Move a specific account or all accounts to Postgres when one of these becomes true:

- one shard grows beyond 5-10 GB;
- sync queue writes happen constantly across many workers;
- dashboard queries need cross-account analytics;
- backups/restores of SQLite become operationally painful;
- we need multi-process workers with high write concurrency.

The future Postgres schema should keep tenant keys explicit:

```sql
raw_entities(user_key, account_key, entity_type, entity_id, ...)
sync_queue(user_key, account_key, entity_type, entity_id, ...)
webhook_events(user_key, account_key, ...)
```

SQLite shards do not need `user_key` on every row because the file path is the tenant boundary. Postgres does.

## Retention Rules

Operational tables should not grow forever:

- `webhook_events`: keep raw bodies for 30-90 days, keep aggregates forever.
- `sync_queue`: keep `done` rows for 7-30 days, keep failed until resolved.
- `sync_jobs` and `sync_runs`: keep details for 90 days, keep daily summaries forever.
- `events` from amoCRM: keep raw if needed for audit, but dashboard queries should use summary marts.

## Current Implementation Step

Implemented now:

- central `account_registry`;
- account discovery through the registry;
- background workers use the fast connection list;
- existing filesystem accounts are auto-registered on discovery;
- activity pulse marts for daily user totals and 15-minute slots;
- lead KPI daily mart grouped by manager, pipeline and status;
- retry/ignore API and UI for failed sync queue items;
- operational cleanup for old done queue rows, webhook events and sync history.

Current mart endpoints:

- `POST /api/activity/rebuild-marts`
- `GET /api/activity/marts`
- `POST /api/kpi/rebuild`
- `GET /api/kpi/daily`
- `GET /api/kpi/marts`
- `POST /api/hub/cleanup`

Next step:

- move heavy rebuilds to explicit background jobs with job ids;
- add source/channel marts for leads and revenue;
- add daily sync health mart;
- add export/migration command: account SQLite shard -> Postgres.
