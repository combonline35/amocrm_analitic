from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    subdomain: str
    access_token: str
    user_key: str = "default"
    account_key_override: str = ""
    base_domain: str = "amocrm.ru"
    api_base_url: str = ""
    db_path: Path = Path("data/amocrm_service.sqlite3")
    data_root: Path = Path("data/users")
    request_timeout: float = 30.0
    form_pipeline_id: int | None = None
    form_status_id: int | None = None
    form_responsible_user_id: int | None = None
    form_tags: tuple[str, ...] = ("Сайт", "Заявка с сайта")
    form_secret: str = ""

    @property
    def account_base_url(self) -> str:
        if not self.subdomain:
            return self.api_base_url.rstrip("/")
        return f"https://{self.subdomain}.{self.base_domain}"

    @property
    def api_v4_url(self) -> str:
        if self.subdomain:
            return f"{self.account_base_url}/api/v4"
        return f"{self.api_base_url.rstrip('/')}/api/v4"

    @property
    def account_key(self) -> str:
        if self.account_key_override:
            return self.account_key_override
        if self.subdomain:
            return self.subdomain
        host = urlparse(self.api_base_url).netloc
        return host or "default"

    @property
    def workspace_dir(self) -> Path:
        return self.db_path.parent


def _load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().lstrip("\ufeff")] = value.strip().strip('"').strip("'")
    return values


def _derive_account_key(subdomain: str, api_base_url: str, override: str = "") -> str:
    if override:
        return override
    if subdomain:
        return subdomain
    host = urlparse(api_base_url).netloc
    return host or "default"


def load_settings(
    account_key: str | None = None,
    user_key: str | None = None,
    data_root: Path | None = None,
) -> Settings:
    _load_env_file(Path(os.getenv("AMO_ENV_FILE", ".env")))
    env = dict(os.environ)
    requested_user = (user_key or env.get("AMO_USER_KEY") or "default").strip() or "default"
    requested_account = (account_key or env.get("AMO_ACCOUNT_KEY") or "").strip()
    data_root = data_root or Path(env.get("AMO_DATA_ROOT", "data/users"))

    account_env_values: dict[str, str] = {}
    if requested_account:
        account_env = data_root / requested_user / "accounts" / requested_account / "account.env"
        account_env_values = _read_env_file(account_env)
        env.update(account_env_values)

    subdomain = env.get("AMO_SUBDOMAIN", "").strip()
    api_base_url = env.get("AMO_API_BASE_URL", "").strip()
    account_key_value = _derive_account_key(subdomain, api_base_url, requested_account)
    explicit_db_path = env.get("AMO_SERVICE_DB", "").strip()
    use_explicit_db_path = bool(explicit_db_path) and (
        not (account_key or user_key) or "AMO_SERVICE_DB" in account_env_values
    )
    db_path = (
        Path(explicit_db_path)
        if use_explicit_db_path
        else data_root / requested_user / "accounts" / account_key_value / "hub.sqlite3"
    )

    def optional_int(name: str) -> int | None:
        value = env.get(name, "").strip()
        return int(value) if value else None

    form_tags = tuple(
        tag.strip()
        for tag in env.get("AMO_FORM_TAGS", "Сайт,Заявка с сайта").split(",")
        if tag.strip()
    )
    return Settings(
        subdomain=subdomain,
        access_token=env.get("AMO_ACCESS_TOKEN", "").strip(),
        user_key=requested_user,
        account_key_override=account_key_value,
        base_domain=env.get("AMO_BASE_DOMAIN", "amocrm.ru").strip(),
        api_base_url=api_base_url,
        db_path=db_path,
        data_root=data_root,
        request_timeout=float(env.get("AMO_REQUEST_TIMEOUT", "30")),
        form_pipeline_id=optional_int("AMO_FORM_PIPELINE_ID"),
        form_status_id=optional_int("AMO_FORM_STATUS_ID"),
        form_responsible_user_id=optional_int("AMO_FORM_RESPONSIBLE_USER_ID"),
        form_tags=form_tags,
        form_secret=env.get("AMO_FORM_SECRET", "").strip(),
    )
