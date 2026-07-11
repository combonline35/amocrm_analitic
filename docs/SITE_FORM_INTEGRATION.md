# Интеграция формы сайта с amoCRM

Сервис принимает заявку с сайта на `POST /api/site/form`, создает контакт, сделку и добавляет к сделке заметку с данными формы.

## Запуск

```powershell
cd C:\Users\User\Documents\amoCRM\amocrm_service
.\.venv\Scripts\python -m amocrm_service.server --host 0.0.0.0 --port 8010
```

## Настройки `.env`

Минимально нужны:

```env
AMO_SUBDOMAIN=your_subdomain
AMO_ACCESS_TOKEN=your_access_token
```

Опционально можно указать, куда класть заявки:

```env
AMO_FORM_PIPELINE_ID=
AMO_FORM_STATUS_ID=
AMO_FORM_RESPONSIBLE_USER_ID=
AMO_FORM_TAGS=Сайт,Заявка с сайта
```

Если форму отправляет ваш backend, можно включить секрет:

```env
AMO_FORM_SECRET=change_me
```

Тогда отправляйте `X-Form-Secret: change_me` или `?secret=change_me`. Не вставляйте настоящий секрет в публичный JavaScript на сайте.

## Поля формы

Endpoint принимает `application/json` и обычную HTML-форму.

Поддерживаемые поля:

- `name`
- `phone`
- `email`
- `message` или `comment`
- `source`
- `page_url` или `url`
- `price`

Обязательно передать хотя бы одно из полей: `name`, `phone`, `email`.

## Пример запроса

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8010/api/site/form `
  -ContentType application/json `
  -Body '{"name":"Иван","phone":"+79990000000","email":"ivan@example.com","message":"Хочу консультацию","page_url":"https://example.com/"}'
```

Готовый пример формы лежит в `examples/site-form.html`.
