-- Raw SQLite migration: harden call checklist step hints for donpotolok.
-- Tightens pass thresholds for v2 checklist scoring; labels stay untouched.
-- Idempotent: plain UPDATE by (account_key, slug), safe to re-apply.

UPDATE call_checklist_step
SET hint = 'Менеджер поздоровался, назвал СВОЁ имя И название компании. НЕ засчитывать, если пропущено название компании или имя менеджера.',
    updated_at = datetime('now')
WHERE account_key = 'donpotolok' AND slug = 'kontakt';

UPDATE call_checklist_step
SET hint = 'Менеджер узнал имя клиента И хотя бы раз обратился по имени дальше в разговоре. НЕ засчитывать, если только спросил имя, но ни разу по нему не обратился.',
    updated_at = datetime('now')
WHERE account_key = 'donpotolok' AND slug = 'imya';

UPDATE call_checklist_step
SET hint = 'Менеджер выяснил КОНКРЕТИКУ и получил ответ: метраж/площадь ИЛИ количество потолков/помещений, город. НЕ засчитывать за общий вопрос без полученного ответа.',
    updated_at = datetime('now')
WHERE account_key = 'donpotolok' AND slug = 'potrebnost';

UPDATE call_checklist_step
SET hint = 'Менеджер объяснил ВЫГОДУ замера для клиента (бесплатно И ни к чему не обязывает, или точная цена на месте, приедет специалист). НЕ засчитывать за простое упоминание слова ''замер'' или ''бесплатно'' без объяснения, зачем это клиенту.',
    updated_at = datetime('now')
WHERE account_key = 'donpotolok' AND slug = 'cennost';

UPDATE call_checklist_step
SET hint = 'Менеджер предложил ДВА конкретных варианта времени на выбор (конкретные дни и/или время: ''суббота или понедельник'', ''завтра до обеда или послезавтра вечером''). НЕ засчитывать за размытое ''когда вам удобно'', ''на неделе'', один вариант или ''завтра-послезавтра'' без привязки.',
    updated_at = datetime('now')
WHERE account_key = 'donpotolok' AND slug = 'sloty';

UPDATE call_checklist_step
SET hint = 'Засчитывать ТОЛЬКО если ОБА условия: (а) клиент высказал конкретное возражение или сомнение (цена, район, надо подумать, посоветоваться, сделаю сам) И (б) менеджер дал содержательный ОТВЕТ именно на это возражение. НЕ засчитывать, если возражение прозвучало от клиента без ответа менеджера, или менеджер лишь задал встречный вопрос. Если возражения в разговоре не было вовсе — ok:false.',
    updated_at = datetime('now')
WHERE account_key = 'donpotolok' AND slug = 'vozrazhenie';

UPDATE call_checklist_step
SET hint = 'Менеджер зафиксировал КОНКРЕТНУЮ договорённость: назван адрес И/ИЛИ конкретные дата/время визита замерщика, подтверждён следующий шаг. НЕ засчитывать за ''я подумаю'', ''перезвоним позже'', отсутствие адреса и времени.',
    updated_at = datetime('now')
WHERE account_key = 'donpotolok' AND slug = 'fiksaciya';
