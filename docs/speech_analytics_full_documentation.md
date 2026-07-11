# Речевая аналитика amocrm_service

Документ фиксирует, где лежит речевая аналитика, как она работает, какие файлы за что отвечают и какие команды нужны для проверки/пересчета. Пакет не включает сырые БД, `.env`, аудиозаписи и транскрипты, чтобы не разнести токены и клиентские данные. Пути к ним указаны отдельно.

## Корень проекта

```text
C:\Users\User\Documents\amoCRM\amocrm_service
```

Код:

```text
C:\Users\User\Documents\amoCRM\amocrm_service\src\amocrm_service
```

Скрипты:

```text
C:\Users\User\Documents\amoCRM\amocrm_service\scripts
```

Миграции:

```text
C:\Users\User\Documents\amoCRM\amocrm_service\scripts\migrations
```

Рабочая БД аккаунта `default/donpotolok`:

```text
C:\Users\User\Documents\amoCRM\amocrm_service\data\users\default\accounts\donpotolok\hub.sqlite3
```

Настройки аккаунта:

```text
C:\Users\User\Documents\amoCRM\amocrm_service\data\users\default\accounts\donpotolok\account_settings.json
```

## Главные файлы речевой аналитики

### `src/amocrm_service/config.py`

Грузит `.env`, `account.env`, выбирает `user_key`, `account_key`, `db_path`. Через него все CLI и сервер понимают, какую БД аккаунта открывать.

### `src/amocrm_service/db.py`

Raw SQLite схема проекта. Для речевой аналитики важны таблицы:

- `conversation_records`
- `conversation_analysis`
- `call_checklist_step`
- `raw_entities`
- `sync_queue`
- `webhook_events`

Миграций Alembic/Django нет. Стиль проекта - raw SQL.

### `src/amocrm_service/repository.py`

Слой доступа к SQLite. Для речевой аналитики важны методы:

- `upsert_conversation_records`
- `list_conversation_records`
- `set_conversation_transcript`
- `update_conversation_record_status`
- `upsert_conversation_analyses`
- `list_conversation_analyses`
- `list_call_checklist_steps`
- `create_call_checklist_step`
- `update_call_checklist_step`
- `deactivate_call_checklist_step`

### `src/amocrm_service/conversations.py`

Главный файл анализа разговоров.

Что внутри:

- `ConversationRecord` - структура звонка.
- `ConversationAnalysis` - структура результата анализа.
- `extract_conversation_records` - вытаскивает звонки из amoCRM notes.
- `ConversationPipeline.discover_from_hub` - находит звонки в локальном hub.
- `ConversationPipeline.analyze_transcribed` - анализирует звонки со статусом `transcribed`.
- `build_call_prompt` - собирает v2-промпт из активных шагов `call_checklist_step`.
- `parse_call_analysis_v2` - парсит компактный JSON ответа LLM.
- `_call_analysis_v2_to_analysis` - превращает v2 JSON в `conversation_analysis`.
- `OpenRouterConversationAnalyzer` - отправляет запрос в OpenRouter.
- `_qa_json_to_analysis` - старый v1/fallback путь.
- `format_transcript_with_roles` и `repair_role_transcript` - форматирование диалога на роли.

### `src/amocrm_service/conversation_automation.py`

Фоновая автообработка разговоров: находит подходящие звонки, проверяет аудио, скачивает, транскрибирует, запускает анализ, экспортирует CSV, пишет заметки в amoCRM.

### `src/amocrm_service/conversation_audio.py`

Проверка доступности записи и скачивание аудио.

### `src/amocrm_service/conversation_transcription.py`

Транскрибация скачанного аудио в текст. Итог сохраняется в `conversation_records.transcript_text`.

### `src/amocrm_service/conversation_settings.py`

Нормализация настроек модуля `conversation_intelligence`: фильтры, действия автообработки, OpenRouter mode/model, старые scoring-настройки.

### `src/amocrm_service/conversation_notes.py`

Генерация заметки в сделку amoCRM по результату анализа.

### `src/amocrm_service/conversation_export.py`

CSV-экспорт результатов анализа.

### `src/amocrm_service/server.py`

Локальная админка. Важные участки:

- `/conversations` - страница речевой аналитики.
- `_render_conversations_page` - рендер страницы.
- `v2_score_cell` - компактная v2-ячейка оценки в таблице.
- `v2_manager_call_card` - карточка звонка менеджера по v2.
- `_handle_conversation_settings` - сохранить настройки и запустить анализ.
- `_handle_conversation_auto` - автообработка.
- `_handle_conversation_post_note` - записать заметку в amoCRM.
- API для чек-листа: `/api/call-checklist-steps`.

## Скрипты

### `scripts/recalc_one.py`

Одноразовый CLI для проверки одного звонка через v2.

Dry-run, без записи в БД:

```powershell
cd C:\Users\User\Documents\amoCRM\amocrm_service
& 'C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe' scripts\recalc_one.py donpotolok contact_notes:291410055
```

Запись результата в `conversation_analysis`:

```powershell
& 'C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe' scripts\recalc_one.py donpotolok contact_notes:291410055 --write
```

Скрипт ищет звонок по:

- точному `conversation_id`
- `source_id`
- `contact_notes:<id>`
- `lead_notes:<id>`

Он не скачивает аудио и не транскрибирует заново. Берет уже сохраненный `transcript_text`.

### `scripts/migrations/20260710_001_create_call_checklist_step.sql`

Создает таблицу `call_checklist_step` и сидит 7 шагов для `donpotolok`.

Накатить на рабочую БД:

```powershell
cd C:\Users\User\Documents\amoCRM\amocrm_service
& 'C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe' -c "from pathlib import Path; import sqlite3; db=Path('data/users/default/accounts/donpotolok/hub.sqlite3'); sql=Path('scripts/migrations/20260710_001_create_call_checklist_step.sql').read_text(encoding='utf-8'); conn=sqlite3.connect(db); conn.executescript(sql); conn.commit(); conn.close(); print('ok')"
```

### `scripts/diagnose_conversations.py`

Диагностика настроек, очередей, webhook events и локальных contact_notes.

### `scripts/diagnose_call_snapshots.py`

Проверяет события звонков, привязку к лидам и snapshot этапа на момент звонка.

### `scripts/start_donpotolok_external_server.ps1`

Старт локального сервера для `donpotolok` с настройками внешнего анализа. Сервером управляет Stan.

### `scripts/run_external_analysis_once.ps1`

Разовый запуск внешнего анализа старым CLI-путем.

### `scripts/enable_external_analysis.ps1`

Меняет `conversation_intelligence.external_analysis` в настройках аккаунта.

## Таблицы SQLite

### `conversation_records`

Один звонок.

Ключевые поля:

- `account_key`
- `conversation_id`
- `source_type`
- `source_id`
- `lead_id`
- `contact_id`
- `direction`
- `recording_url`
- `transcript_text`
- `duration_seconds`
- `occurred_at`
- `status`
- `metadata_json`

Типичный `conversation_id`:

```text
contact_notes:291410055
```

### `conversation_analysis`

Один результат анализа звонка.

Ключевые поля:

- `summary`
- `sentiment`
- `score`
- `next_step`
- `objections_json`
- `recommendations_json`
- `metrics_json`
- `analysis_json`

### `call_checklist_step`

Справочник v2-чек-листа.

Ключевые поля:

- `account_key`
- `slug`
- `label`
- `hint`
- `order_index`
- `active`

Индекс уникальности:

```text
(account_key, slug)
```

## V2 чек-лист donpotolok

Сейчас сидится 7 шагов:

1. `kontakt` - Контакт: поздоровался, представился, назвал компанию.
2. `imya` - Имя: узнал и обращался по имени.
3. `potrebnost` - Потребность: площадь / кол-во / помещение / сроки.
4. `cennost` - Ценность замера: проговорил, что бесплатно и ни к чему не обязывает.
5. `sloty` - Слоты: предложил 2 конкретные даты/времени на выбор.
6. `vozrazhenie` - Возражение: отработал и проверил, снято ли.
7. `fiksaciya` - Фиксация: записал адрес / время / следующий шаг.

## V2 поток анализа

1. Звонок должен быть в `conversation_records`.
2. У звонка должен быть `transcript_text`.
3. Активные шаги берутся из `call_checklist_step`.
4. `build_call_prompt(account_key, repository)` собирает промпт.
5. Транскрипт добавляется в конец промпта.
6. Запрос уходит в OpenRouter.
7. OpenRouter должен вернуть строгий JSON:

```json
{
  "outcome": "записан",
  "refusal_reason": "",
  "steps": {
    "kontakt": {"ok": true, "quote": ""}
  },
  "summary": "",
  "next_step": "",
  "coach_tip": ""
}
```

8. `parse_call_analysis_v2` нормализует ответ.
9. `_call_analysis_v2_to_analysis` считает качество и собирает payload.
10. Результат пишется в `conversation_analysis`.

## V2 analysis_json

Пример структуры:

```json
{
  "source": "openrouter_v2_qa",
  "score_max": 0,
  "qa_json": {},
  "call_analysis_v2": {
    "outcome": "записан",
    "refusal_reason": "",
    "steps": {},
    "summary": "",
    "next_step": "",
    "coach_tip": ""
  },
  "analysis_prompt": "",
  "checklist_snapshot": {
    "version": "",
    "steps": [],
    "prompt": ""
  },
  "record": {}
}
```

## V2 metrics_json

Пример структуры:

```json
{
  "score_max": 0,
  "duration_seconds": 169,
  "outcome": "записан",
  "outcome_color": "green",
  "conversion_excluded": false,
  "quality_passed": 7,
  "quality_total": 7,
  "quality_display": "7/7",
  "call_analysis_v2": {},
  "transcript_chars": 1234
}
```

Outcome-маппинг:

- `записан` -> `positive`, `green`, не исключать из конверсии.
- `перезвон` -> `neutral`, `yellow`, не исключать из конверсии.
- `отказ` -> `negative`, `red`, не исключать из конверсии.
- `не_применимо` -> `neutral`, `gray`, исключать из конверсии.

## Админка

Страница:

```text
/conversations?user=default&account=donpotolok
```

Что показывает:

- последние звонки;
- статус записи/расшифровки;
- v2-ячейку оценки: `outcome · quality_display`;
- раскрытие звонка кнопкой `Открыть`;
- v2-карточку менеджера;
- заметку в сделку;
- диалог.

## API server.py

Основные маршруты:

- `GET /conversations`
- `GET /api/conversations`
- `POST /api/conversations/settings`
- `POST /api/conversations/auto-run`
- `POST /api/conversations/auto-dry-run`
- `POST /api/conversations/post-note`
- `POST /api/conversations/export`
- `GET /api/call-checklist-steps`
- `POST /api/call-checklist-steps`
- `POST /api/call-checklist-steps/{id}`
- `POST /api/call-checklist-steps/{id}/delete`

## Типовые команды

Проверить один звонок dry-run:

```powershell
cd C:\Users\User\Documents\amoCRM\amocrm_service
& 'C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe' scripts\recalc_one.py donpotolok contact_notes:291410055
```

Записать v2-анализ одного звонка:

```powershell
& 'C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe' scripts\recalc_one.py donpotolok contact_notes:291410055 --write
```

Если `python` не найден:

```powershell
& 'C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe' ...
```

Если пишет `чек-лист пуст`:

```powershell
& 'C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe' -c "from pathlib import Path; import sqlite3; db=Path('data/users/default/accounts/donpotolok/hub.sqlite3'); sql=Path('scripts/migrations/20260710_001_create_call_checklist_step.sql').read_text(encoding='utf-8'); conn=sqlite3.connect(db); conn.executescript(sql); conn.commit(); conn.close(); print('ok')"
```

## Что не включать в архивы

Не включать без отдельной причины:

- `.env`
- `.env.real`
- `account.env`
- `hub.sqlite3`
- `hub.sqlite3-wal`
- `hub.sqlite3-shm`
- папку `recordings`
- сырые транскрипты и аудио

Причина: там токены, телефоны, имена, адреса, клиентские данные и записи звонков.

## Текущий важный вывод проверки

После наката миграции dry-run звонка `contact_notes:291410055` дал:

```text
outcome: записан
quality_display: 7/7
summary: Клиент записан на бесплатный замер.
next_step: Подтвердить время и адрес замера с клиентом.
coach_tip: Убедитесь, что все ключевые параметры клиента уточнены.
```

Замечание: LLM засчитала `vozrazhenie` как `ok`, хотя цитата больше похожа на усиление ценности замера, а не на явное возражение. Это стоит уточнить в hint/логике следующего слайса.
