from __future__ import annotations

from pathlib import Path


source = Path("/tmp/amocrm-service.env.upload")
target = Path("/etc/amocrm-service.env")

values: dict[str, str] = {}
for raw_line in source.read_text(encoding="utf-8-sig").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip().lstrip("\ufeff")] = value.strip().strip('"').strip("'")

values["AMO_SERVICE_DB"] = "/var/lib/amocrm-service/amocrm_service.sqlite3"


def format_value(value: str) -> str:
    if not value:
        return ""
    if any(char.isspace() for char in value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


content = "".join(f"{key}={format_value(value)}\n" for key, value in values.items())
target.write_text(content, encoding="utf-8")
target.chmod(0o600)
