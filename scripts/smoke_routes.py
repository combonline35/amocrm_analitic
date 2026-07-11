"""Смоук-проверка ключевых GET-роутов на уже запущенном сервере.

Скрипт НЕ запускает сервер и НЕ пишет в БД — только GET-запросы.

Использование:
  1. В одном окне поднять сервер вручную:
       python -m amocrm_service.server --port 8010
  2. В другом окне запустить смоук:
       python scripts\\smoke_routes.py
     или с другим базовым URL:
       python scripts\\smoke_routes.py --base http://127.0.0.1:8010
"""

from __future__ import annotations

import argparse
import urllib.error
import urllib.request

API_PATHS = [
    "/api/summary",
    "/api/sync-options",
    "/api/hub/overview",
    "/api/connection/status",
    "/api/activity/summary",
    "/api/freshness",
    "/api/quality/summary",
    "/api/kpi/daily",
    "/api/sync-queue",
    "/api/sync-sources",
    "/api/call-checklist-steps",
    "/api/conversations",
    "/api/analytics/fields",
    "/api/formula/dictionary",
    "/api/dashboard-widgets",
    "/api/dashboard-pages",
    "/api/work-sources",
]

PAGE_PATHS = [
    "/",
    "/dashboard",
    "/conversations",
    "/quality",
    "/activity",
    "/freshness",
    "/queue",
]

TIMEOUT_SECONDS = 10


def check_path(base: str, path: str) -> bool:
    url = base.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    except Exception as exc:
        print(f"ERR {exc}  {path}")
        return False
    print(f"OK {status} {len(body)}  {path}")
    return status < 500


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-check GET routes on a running server")
    parser.add_argument("--base", default="http://127.0.0.1:8010", help="base URL of the running server")
    args = parser.parse_args()

    paths = API_PATHS + PAGE_PATHS
    ok_count = sum(1 for path in paths if check_path(args.base, path))
    print(f"\n{ok_count}/{len(paths)} ответили статусом <500")


if __name__ == "__main__":
    main()
