# Conversation Intelligence Plan

## Цель

Собрать надежный контур для разговоров из amoCRM:

1. находить звонки и записи в карточках сделок;
2. сохранять нормализованные разговоры в локальный hub;
3. транскрибировать аудио;
4. анализировать разговоры;
5. отдавать результаты в Google Sheets, дашборды и обратно в amoCRM.

## Принцип

amoCRM является источником события и контекста сделки. Запись разговора может лежать в самой карточке как ссылка в примечании звонка или у подключенной телефонии. Поэтому ядро работает не с конкретной телефонией, а с нормализованным `recording_url`.

## Multi-tenant контур

Сервис сразу проектируем как multi-tenant:

- `user_key` - владелец/оператор в нашей системе;
- `account_key` - конкретный клиентский аккаунт amoCRM;
- `crm_user_id` - пользователь внутри amoCRM, для персональных настроек.

Каждый аккаунт amoCRM получает отдельную рабочую директорию:

```text
data/users/{user_key}/accounts/{account_key}/
  account.env
  hub.sqlite3
  account_settings.json
  users/{crm_user_id}/settings.json
```

Админский registry:

```text
data/users/registry.sqlite3
account_registry(user_key, account_key, subdomain, status, env_path, db_path)
```

Статусы подключения:

- `active` - клиент обслуживается worker-ами и виден в рабочих списках;
- `disabled` - доступ выключен, данные и настройки сохраняются;
- `archived` - клиент скрыт из операционного контура, но запись остается для аудита.

API для будущей админки:

```text
GET /admin/connections
POST /admin/connections/{user_key}/{account_key}/status
GET /admin/connections/{user_key}/{account_key}/settings
POST /admin/connections/{user_key}/{account_key}/settings
GET /admin/connections/{user_key}/{account_key}/users/{crm_user_id}/settings
POST /admin/connections/{user_key}/{account_key}/users/{crm_user_id}/settings
```

Правило для всех модулей: любая операция получает явный контекст `user_key/account_key`, а персональные настройки применяются поверх настроек аккаунта.

## Этап 1. Ядро

Готово:

- таблица `conversation_records` для разговоров;
- таблица `conversation_analysis` для результатов анализа;
- извлечение `call_in` и `call_out` из `lead_notes`;
- сохранение ссылки записи, длительности, телефона, направления, сделки;
- базовый анализатор для тестируемого контура без внешнего AI;
- CLI и API для запуска discovery/analyze.
- админский слой для включения/отключения клиентских аккаунтов и хранения настроек аккаунта/пользователя.
- UI `/admin` для управления подключениями: список клиентов, статусы, открыть аккаунт, включить, отключить, архивировать.
- UI `/account-settings` для настроек модулей компании и персональных настроек amoCRM-пользователей.

Команды:

```powershell
python -m amocrm_service.cli conversations discover
python -m amocrm_service.cli conversations import-lead --lead-id 27390977
python -m amocrm_service.cli conversations probe-recordings
python -m amocrm_service.cli conversations download-recordings
python -m amocrm_service.cli conversations transcribe
python -m amocrm_service.cli conversations analyze
python -m amocrm_service.cli conversations list
python -m amocrm_service.cli conversations analysis
```

API:

```text
POST /conversations/discover
POST /conversations/analyze
GET /conversations
GET /conversations/analysis
```

## Этап 2. Транскрибация

Готово:

- проверка доступности записи по `recording_url`;
- добавить адаптер скачивания аудио по `recording_url`;
- локальное сохранение скачанных записей в `recordings/`;
- OpenAI STT-адаптер через `/v1/audio/transcriptions`, модель по умолчанию `gpt-4o-transcribe`;
- сохранение `transcript_text` в `conversation_records`.

Следующий шаг:

- добавить таблицу/статусы попыток транскрибации;
- делать повторные попытки при временных ошибках.

Текущие статусы:

```text
recording_found
audio_accessible
recording_unavailable
audio_downloaded
audio_download_failed
transcribed
transcription_failed
```

## Этап 3. AI-анализ

После транскрибации:

- заменить rule-based анализатор на LLM-анализатор;
- выдавать summary, score, objections, next_step, recommendations;
- сохранять машинно-читаемые метрики для дашбордов;
- отдельно хранить версию промпта/модели.

## Этап 4. Выгрузка

Параллельные выходы:

- Google Sheets adapter для простой табличной аналитики;
- hub dashboards поверх `conversation_records` и `conversation_analysis`;
- запись короткой рекомендации в amoCRM note;
- обновление custom fields в сделке: score, next step, main objection.

## Этап 5. Автоматизация

Финальный режим:

- webhook amoCRM кладет событие в очередь;
- queue worker обновляет сделку/примечания;
- conversation discovery находит новый звонок;
- transcription worker обрабатывает аудио;
- analysis worker считает рекомендации;
- export worker синхронизирует Sheets и amoCRM.
