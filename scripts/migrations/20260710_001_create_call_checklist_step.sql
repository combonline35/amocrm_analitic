-- Raw SQLite migration for call checklist step dictionary.
-- Apply manually to the target hub database; do not run from Codex.

CREATE TABLE IF NOT EXISTS call_checklist_step (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    slug TEXT NOT NULL,
    label TEXT NOT NULL,
    hint TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(account_key, slug)
);

CREATE INDEX IF NOT EXISTS idx_call_checklist_step_account_active
ON call_checklist_step(account_key, active, order_index);

INSERT INTO call_checklist_step(account_key, slug, label, hint, order_index, active, created_at, updated_at)
VALUES
    (
        'donpotolok',
        'kontakt',
        'Контакт: поздоровался, представился, назвал компанию',
        'Считать выполненным, если менеджер в начале разговора поздоровался, представился или обозначил себя и назвал компанию/контекст заявки. Не засчитывать, если разговор начинается без понятного контакта.',
        1,
        1,
        datetime('now'),
        datetime('now')
    ),
    (
        'donpotolok',
        'imya',
        'Имя: узнал и обращался по имени',
        'Считать выполненным, если менеджер узнал имя клиента и использовал его в разговоре. Частично засчитывать, если имя только спросили, но дальше не применяли.',
        2,
        1,
        datetime('now'),
        datetime('now')
    ),
    (
        'donpotolok',
        'potrebnost',
        'Потребность: площадь / кол-во / помещение / сроки',
        'Считать выполненным, если менеджер выяснил ключевые параметры: площадь, количество потолков или комнат, тип помещения и сроки. Частично засчитывать, если уточнена только часть параметров.',
        3,
        1,
        datetime('now'),
        datetime('now')
    ),
    (
        'donpotolok',
        'cennost',
        'Ценность замера: проговорил, что бесплатно и ни к чему не обязывает',
        'Считать выполненным, если менеджер объяснил пользу замера и явно сказал, что замер бесплатный и не обязывает клиента к покупке. Частично засчитывать, если прозвучала только одна из частей.',
        4,
        1,
        datetime('now'),
        datetime('now')
    ),
    (
        'donpotolok',
        'sloty',
        'Слоты: предложил 2 конкретные даты/времени на выбор',
        'Считать выполненным, если менеджер предложил клиенту два конкретных варианта даты или времени в формате выбора без выбора. Не засчитывать общий вопрос "когда удобно?" без вариантов.',
        5,
        1,
        datetime('now'),
        datetime('now')
    ),
    (
        'donpotolok',
        'vozrazhenie',
        'Возражение: отработал и проверил, снято ли',
        'Считать выполненным, если менеджер ответил на сомнение клиента и затем проверил, стало ли понятно или снято ли возражение. Частично засчитывать, если менеджер только ответил, но не проверил результат.',
        6,
        1,
        datetime('now'),
        datetime('now')
    ),
    (
        'donpotolok',
        'fiksaciya',
        'Фиксация: записал адрес / время / следующий шаг',
        'Считать выполненным, если менеджер зафиксировал конкретный следующий шаг: адрес, дату/время замера, ответственного или понятную договоренность. Не засчитывать, если разговор закончился без конкретики.',
        7,
        1,
        datetime('now'),
        datetime('now')
    )
ON CONFLICT(account_key, slug) DO UPDATE SET
    label = excluded.label,
    hint = excluded.hint,
    order_index = excluded.order_index,
    active = excluded.active,
    updated_at = excluded.updated_at;
