from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SiteLeadRequest:
    name: str
    phone: str = ""
    email: str = ""
    message: str = ""
    source: str = "site"
    page_url: str = ""
    price: int | None = None

    @property
    def contact_name(self) -> str:
        return self.name or self.phone or self.email or "Заявка с сайта"

    @property
    def lead_name(self) -> str:
        return f"Заявка с сайта: {self.contact_name}"

    @property
    def note_text(self) -> str:
        rows = [
            ("Имя", self.name),
            ("Телефон", self.phone),
            ("Email", self.email),
            ("Сообщение", self.message),
            ("Источник", self.source),
            ("Страница", self.page_url),
        ]
        return "\n".join(f"{label}: {value}" for label, value in rows if value)


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_price(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        price = int(float(str(value).replace(",", ".").strip()))
    except ValueError as exc:
        raise ValueError("price must be a number") from exc
    if price < 0:
        raise ValueError("price must be greater than or equal to zero")
    return price


def parse_site_lead_payload(payload: dict[str, Any]) -> SiteLeadRequest:
    name = _clean_string(payload.get("name") or payload.get("contact_name"))
    phone = _clean_string(payload.get("phone") or payload.get("tel"))
    email = _clean_string(payload.get("email"))
    message = _clean_string(payload.get("message") or payload.get("comment"))
    source = _clean_string(payload.get("source")) or "site"
    page_url = _clean_string(payload.get("page_url") or payload.get("url"))

    if not any([name, phone, email]):
        raise ValueError("name, phone or email is required")

    return SiteLeadRequest(
        name=name,
        phone=phone,
        email=email,
        message=message,
        source=source,
        page_url=page_url,
        price=_optional_price(payload.get("price")),
    )
