from __future__ import annotations

import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


def _save_callback(query: dict[str, list[str]], output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: values[-1] if values else "" for key, values in query.items()}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    server_version = "amocrm-oauth-callback/0.1"
    output_path = Path("data/oauth_callback_public.json")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {"/", "/oauth/callback"}:
            self.send_error(404, "Not found")
            return

        payload = _save_callback(parse_qs(parsed.query), self.output_path)
        code = payload.get("code") or "code не пришел в query-параметрах"
        self._send_html(f"""
        <!doctype html>
        <html lang="ru">
        <head>
          <meta charset="utf-8">
          <title>amoCRM OAuth</title>
          <style>
            body {{
              margin: 0;
              min-height: 100vh;
              display: grid;
              place-items: center;
              background: #edf2f7;
              color: #07101f;
              font: 16px/1.45 "Segoe UI", Arial, sans-serif;
            }}
            main {{
              width: min(680px, calc(100% - 32px));
              padding: 32px;
              border-radius: 24px;
              background: #fff;
              box-shadow: 0 24px 70px rgba(15, 23, 42, .12);
            }}
            h1 {{ margin: 0 0 10px; font-size: 30px; }}
            p {{ color: #66758a; }}
            code {{
              display: block;
              padding: 14px;
              border-radius: 12px;
              background: #f8fafc;
              overflow-wrap: anywhere;
            }}
          </style>
        </head>
        <body>
          <main>
            <h1>Код amoCRM получен</h1>
            <p>Callback сохранен локально. Эту вкладку можно закрыть.</p>
            <code>{html.escape(code)}</code>
          </main>
        </body>
        </html>
        """)

    def do_POST(self) -> None:
        self.send_error(405, "Method not allowed")

    def log_message(self, format: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), format % args))

    def _send_html(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(host: str = "127.0.0.1", port: int = 8020, output: Path | None = None) -> None:
    if output is not None:
        OAuthCallbackHandler.output_path = output
    httpd = ThreadingHTTPServer((host, port), OAuthCallbackHandler)
    print(f"amoCRM OAuth callback: http://{host}:{port}/oauth/callback")
    httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(prog="amocrm-oauth-callback")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--output", default="data/oauth_callback_public.json")
    args = parser.parse_args()
    serve(args.host, args.port, Path(args.output))


if __name__ == "__main__":
    main()
