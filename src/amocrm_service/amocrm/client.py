from __future__ import annotations

import time
from typing import Any

import httpx

from amocrm_service.config import Settings
from amocrm_service.amocrm.errors import AmoCRMEntityNotFound


class AmoCRMClient:
    def __init__(self, settings: Settings):
        if not settings.subdomain and not settings.api_base_url:
            raise ValueError("AMO_SUBDOMAIN or AMO_API_BASE_URL is required")
        if not settings.access_token:
            raise ValueError("AMO_ACCESS_TOKEN is required")
        self.settings = settings
        self._client = httpx.Client(timeout=settings.request_timeout)
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AmoCRMClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.access_token}",
            "Accept": "application/json",
            "User-Agent": "amocrm-service/0.1",
        }

    def _ajax_headers(self) -> dict[str, str]:
        headers = self._headers()
        headers.update({
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
        })
        return headers

    def _wait_rate_limit(self) -> None:
        min_interval = 0.22
        now = time.monotonic()
        sleep_for = min_interval - (now - self._last_request)
        if sleep_for > 0:
            time.sleep(sleep_for)
        self._last_request = time.monotonic()

    def get_v4(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._wait_rate_limit()
        response = self._client.get(
            f"{self.settings.api_v4_url}{path}",
            headers=self._headers(),
            params=params,
        )
        if response.status_code in {204, 404}:
            raise AmoCRMEntityNotFound(f"amoCRM entity is not available: {path}")
        response.raise_for_status()
        if not response.content.strip():
            raise AmoCRMEntityNotFound(f"amoCRM returned an empty response: {path}")
        return response.json()

    def post_v4(self, path: str, payload: Any) -> dict[str, Any]:
        self._wait_rate_limit()
        response = self._client.post(
            f"{self.settings.api_v4_url}{path}",
            headers=self._headers(),
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    def paged_v4(
        self,
        path: str,
        embedded_key: str,
        params: dict[str, Any] | None = None,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for batch in self.iter_paged_v4(path, embedded_key, params, limit):
            items.extend(batch)
        return items

    def iter_paged_v4(
        self,
        path: str,
        embedded_key: str,
        params: dict[str, Any] | None = None,
        limit: int = 250,
    ):
        page = 1
        while True:
            page_params = dict(params or {})
            page_params.update({"page": page, "limit": limit})
            data = self.get_v4(path, page_params)
            yield data.get("_embedded", {}).get(embedded_key, [])
            if "next" not in data.get("_links", {}):
                return
            page += 1

    def _lead_params(
        self,
        *,
        pipeline_ids: list[int] | None = None,
        status_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"with": "contacts,companies,catalog_elements"}
        if pipeline_ids:
            params["filter[pipeline_id][]"] = [int(item) for item in pipeline_ids]
        if status_ids:
            params["filter[status_id][]"] = [int(item) for item in status_ids]
        return params

    def iter_leads(
        self,
        *,
        pipeline_ids: list[int] | None = None,
        status_ids: list[int] | None = None,
    ):
        return self.iter_paged_v4(
            "/leads",
            "leads",
            params=self._lead_params(pipeline_ids=pipeline_ids, status_ids=status_ids),
        )

    def iter_contacts(self):
        return self.iter_paged_v4("/contacts", "contacts")

    def iter_companies(self):
        return self.iter_paged_v4("/companies", "companies")

    def iter_tasks(self):
        return self.iter_paged_v4("/tasks", "tasks")

    def iter_customers(self):
        return self.iter_paged_v4("/customers", "customers")

    def iter_events(self):
        return self.iter_paged_v4("/events", "events")

    def iter_lead_notes(self):
        return self.iter_paged_v4("/leads/notes", "notes")

    def iter_contact_notes(self):
        return self.iter_paged_v4("/contacts/notes", "notes")

    def iter_company_notes(self):
        return self.iter_paged_v4("/companies/notes", "notes")

    def iter_customer_notes(self):
        return self.iter_paged_v4("/customers/notes", "notes")

    def get_leads(
        self,
        *,
        pipeline_ids: list[int] | None = None,
        status_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        return self.paged_v4(
            "/leads",
            "leads",
            params=self._lead_params(pipeline_ids=pipeline_ids, status_ids=status_ids),
        )

    def get_contacts(self) -> list[dict[str, Any]]:
        return self.paged_v4("/contacts", "contacts")

    def create_contact(
        self,
        *,
        name: str,
        phone: str = "",
        email: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        custom_fields_values: list[dict[str, Any]] = []
        if phone:
            custom_fields_values.append({
                "field_code": "PHONE",
                "values": [{"value": phone, "enum_code": "WORK"}],
            })
        if email:
            custom_fields_values.append({
                "field_code": "EMAIL",
                "values": [{"value": email, "enum_code": "WORK"}],
            })

        contact: dict[str, Any] = {"name": name}
        if custom_fields_values:
            contact["custom_fields_values"] = custom_fields_values
        if tags:
            contact["_embedded"] = {"tags": [{"name": tag} for tag in tags]}

        data = self.post_v4("/contacts", [contact])
        return data.get("_embedded", {}).get("contacts", [])[0]

    def create_lead(
        self,
        *,
        name: str,
        contact_id: int | None = None,
        price: int | None = None,
        pipeline_id: int | None = None,
        status_id: int | None = None,
        responsible_user_id: int | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        lead: dict[str, Any] = {"name": name}
        if price is not None:
            lead["price"] = price
        if pipeline_id is not None:
            lead["pipeline_id"] = pipeline_id
        if status_id is not None:
            lead["status_id"] = status_id
        if responsible_user_id is not None:
            lead["responsible_user_id"] = responsible_user_id

        embedded: dict[str, Any] = {}
        if contact_id is not None:
            embedded["contacts"] = [{"id": contact_id}]
        if tags:
            embedded["tags"] = [{"name": tag} for tag in tags]
        if embedded:
            lead["_embedded"] = embedded

        data = self.post_v4("/leads", [lead])
        return data.get("_embedded", {}).get("leads", [])[0]

    def add_lead_note(self, lead_id: int, text: str) -> dict[str, Any]:
        payload = [{"note_type": "common", "params": {"text": text}}]
        try:
            data = self.post_v4(f"/leads/{lead_id}/notes", payload)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            bulk_payload = [{
                "entity_id": int(lead_id),
                "note_type": "common",
                "params": {"text": text},
            }]
            data = self.post_v4("/leads/notes", bulk_payload)
        return data.get("_embedded", {}).get("notes", [])[0]

    def get_lead_notes_by_id(self, lead_id: int) -> list[dict[str, Any]]:
        return self.paged_v4(f"/leads/{lead_id}/notes", "notes")

    def get_contact_notes_by_id(self, contact_id: int) -> list[dict[str, Any]]:
        return self.paged_v4(f"/contacts/{contact_id}/notes", "notes")

    def get_contact_with_leads(self, contact_id: int) -> dict[str, Any]:
        return self.get_v4(f"/contacts/{contact_id}", {"with": "leads"})

    def get_recent_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        data = self.get_v4(
            "/events",
            {"order[created_at]": "desc", "limit": limit},
        )
        return (data.get("_embedded") or {}).get("events") or []

    def get_lead_events(self, lead_id: int, *, limit: int = 250) -> list[dict[str, Any]]:
        data = self.get_v4(
            "/events",
            {
                "filter[entity]": "lead",
                "filter[entity_id]": str(lead_id),
                "order[created_at]": "desc",
                "limit": limit,
            },
        )
        return (data.get("_embedded") or {}).get("events") or []

    def get_companies(self) -> list[dict[str, Any]]:
        return self.paged_v4("/companies", "companies")

    def get_tasks(self) -> list[dict[str, Any]]:
        return self.paged_v4("/tasks", "tasks")

    def get_entity_by_id(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        path_params = {
            "leads": (f"/leads/{entity_id}", {"with": "contacts,companies,catalog_elements"}),
            "contacts": (f"/contacts/{entity_id}", None),
            "companies": (f"/companies/{entity_id}", None),
            "customers": (f"/customers/{entity_id}", None),
            "tasks": (f"/tasks/{entity_id}", None),
        }
        if entity_type not in path_params:
            raise ValueError(f"Point refresh is not supported for entity_type: {entity_type}")
        path, params = path_params[entity_type]
        return self.get_v4(path, params)

    def get_customers(self) -> list[dict[str, Any]]:
        return self.paged_v4("/customers", "customers")

    def get_events(self) -> list[dict[str, Any]]:
        return self.paged_v4("/events", "events")

    def get_lead_notes(self) -> list[dict[str, Any]]:
        return self.paged_v4("/leads/notes", "notes")

    def get_contact_notes(self) -> list[dict[str, Any]]:
        return self.paged_v4("/contacts/notes", "notes")

    def get_company_notes(self) -> list[dict[str, Any]]:
        return self.paged_v4("/companies/notes", "notes")

    def get_customer_notes(self) -> list[dict[str, Any]]:
        return self.paged_v4("/customers/notes", "notes")

    def get_users(self) -> list[dict[str, Any]]:
        return self.paged_v4("/users", "users")

    def get_pipelines(self) -> list[dict[str, Any]]:
        return self.paged_v4("/leads/pipelines", "pipelines")

    def get_lead_custom_fields(self) -> list[dict[str, Any]]:
        return self.paged_v4("/leads/custom_fields", "custom_fields")

    def get_contact_custom_fields(self) -> list[dict[str, Any]]:
        return self.paged_v4("/contacts/custom_fields", "custom_fields")

    def get_company_custom_fields(self) -> list[dict[str, Any]]:
        return self.paged_v4("/companies/custom_fields", "custom_fields")

    def get_customer_custom_fields(self) -> list[dict[str, Any]]:
        return self.paged_v4("/customers/custom_fields", "custom_fields")

    def get_salesbots(self) -> list[dict[str, Any]]:
        return self.paged_v4("/bots", "items", limit=50)

    def get_catalogs(self) -> list[dict[str, Any]]:
        return self.paged_v4("/catalogs", "catalogs")

    def iter_catalog_elements(self):
        for catalog in self.get_catalogs():
            catalog_id = catalog.get("id")
            if catalog_id is None:
                continue
            for batch in self.iter_paged_v4(f"/catalogs/{catalog_id}/elements", "elements"):
                for item in batch:
                    item.setdefault("catalog_id", catalog_id)
                yield batch

    def get_catalog_elements(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for batch in self.iter_catalog_elements():
            items.extend(batch)
        return items

    def get_salesbot_full(self, bot_id: int) -> dict[str, Any]:
        if not self.settings.subdomain:
            raise ValueError("AMO_SUBDOMAIN is required for private salesbot endpoints")
        self._wait_rate_limit()
        response = self._client.get(
            f"{self.settings.account_base_url}/api/v1/salesbot/{bot_id}",
            headers=self._ajax_headers(),
        )
        response.raise_for_status()
        return response.json().get("_embedded", {}).get("items", [])[0]

    def get_digital_pipeline_actions(self, pipeline_id: int) -> list[dict[str, Any]]:
        if not self.settings.subdomain:
            raise ValueError("AMO_SUBDOMAIN is required for private digital pipeline endpoints")
        self._wait_rate_limit()
        response = self._client.get(
            f"{self.settings.account_base_url}/ajax/settings/pipeline/leads/{pipeline_id}",
            headers=self._ajax_headers(),
        )
        response.raise_for_status()
        return response.json().get("data", {}).get("actions", [])
