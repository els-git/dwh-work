"""I11-05: test API service over PROCESS_VIEWER public.services (read only, GET only).

Runs on the DWH server as dwh-test-api.service on 127.0.0.1:8090; nginx publishes it as
https://<dns name>/pv-api/ (tools/server/dwh_test_api_setup.py). Only for testing the API connector of DWH.

Settings (environment, /etc/dwh/test-api.env, root 0600, read by systemd):
  PV_DB_HOST, PV_DB_PORT, PV_DB_NAME, PV_DB_USER, PV_DB_PASSWORD   read-only account of process_viewer
  PV_API_TOKEN                                                      token of the service
  PV_API_PORT                                                       default 8090

Endpoints (all but /health need "Authorization: Bearer <token>" or "X-Api-Key: <token>"):
  GET /health                                     {"status": "ok"} (no database access)
  GET /v1/services?page=1&size=100                page pagination: {"data": {"items": [...]}, "meta": {...}}
  GET /v1/services/cursor?limit=100&cursor=...    cursor pagination: {"items": [...], "next_cursor", "next"}
                                                  and a Link: <...>; rel="next" header
  common parameters: format=json|xml, updated_since=<ISO time> (updated_at >= value),
                     simulate=429|500|slow|flaky (error modes for retry tests; flaky = every other call 429)
The database is read through source_gateway (read-only session, SELECT only). Errors never show settings.
"""
from __future__ import annotations

import base64
import datetime as dt
import decimal
import hmac
import json
import os
import sys
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC / "backend" / "src"))

COLUMNS = ("id", "process_id", "code", "description", "local_service", "method", "url", "environment",
           "variables", "active", "curl_text", "created_at", "updated_at")
MAX_SIZE = 500
DEFAULT_SIZE = 100
SLOW_SECONDS = 5.0


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def _int(params: dict[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    if not raw.isdigit() or not low <= int(raw) <= high:
        raise ApiError(400, "BAD_PARAMETER", f"{name}: whole number from {low} to {high}")
    return int(raw)


def _since(params: dict[str, str]) -> dt.datetime | None:
    raw = params.get("updated_since")
    if not raw:
        return None
    try:
        value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(400, "BAD_PARAMETER", "updated_since: ISO date and time") from None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def encode_cursor(last_id: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"after": last_id}).encode()).decode().rstrip("=")


def decode_cursor(raw: str) -> int:
    try:
        value = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))["after"]
    except Exception:
        raise ApiError(400, "BAD_CURSOR", "cursor: not issued by this service") from None
    if not isinstance(value, int) or value < 0:
        raise ApiError(400, "BAD_CURSOR", "cursor: not issued by this service")
    return value


class ServicesStore:
    """Reads public.services through source_gateway; one short read-only session per request."""

    def __init__(self, settings: dict[str, str], connect: Callable[..., Any] | None = None):
        self.settings = settings
        self._connect = connect

    def _open(self):
        if self._connect is not None:
            return self._connect()
        from dwh_api import source_gateway
        s = self.settings
        return source_gateway.connect(host=s["PV_DB_HOST"], port=int(s.get("PV_DB_PORT") or 5432),
                                      dbname=s["PV_DB_NAME"], user=s["PV_DB_USER"],
                                      password=s.get("PV_DB_PASSWORD"), application_name="dwh_test_api",
                                      statement_timeout_ms=15000, connect_timeout=10)

    def query(self, sql: str, params: tuple) -> list[tuple]:
        conn = self._open()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            conn.rollback()
            return rows
        finally:
            conn.close()

    def page(self, since: dt.datetime | None, page: int, size: int) -> tuple[list[dict], int]:
        where, args = ("WHERE updated_at >= %s", (since,)) if since else ("", ())
        total = self.query(f"SELECT count(*) FROM public.services {where}", args)[0][0]
        rows = self.query(f"SELECT {', '.join(COLUMNS)} FROM public.services {where} ORDER BY id LIMIT %s OFFSET %s",
                          args + (size, (page - 1) * size))
        return [dict(zip(COLUMNS, r)) for r in rows], int(total)

    def after(self, since: dt.datetime | None, after_id: int, limit: int) -> list[dict]:
        cond, args = ("AND updated_at >= %s", (since,)) if since else ("", ())
        rows = self.query(f"SELECT {', '.join(COLUMNS)} FROM public.services WHERE id > %s {cond} ORDER BY id LIMIT %s",
                          (after_id,) + args + (limit + 1,))
        return [dict(zip(COLUMNS, r)) for r in rows]


def plain(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    return value


def to_json_item(row: dict) -> dict:
    item = {k: plain(v) for k, v in row.items()}
    if isinstance(item.get("variables"), str):
        try:
            item["variables"] = json.loads(item["variables"])
        except ValueError:
            pass
    return item


def to_xml(root_name: str, items: list[dict], meta: dict) -> bytes:
    root = ET.Element(root_name)
    holder = ET.SubElement(root, "items")
    for row in items:
        node = ET.SubElement(holder, "service")
        for key, value in row.items():
            child = ET.SubElement(node, key)
            if value is None:
                child.set("null", "true")
            elif isinstance(value, (dict, list)):
                child.text = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, bool):
                child.text = "true" if value else "false"
            else:
                child.text = str(value)
    meta_node = ET.SubElement(root, "meta")
    for key, value in meta.items():
        if value is not None:
            ET.SubElement(meta_node, key).text = str(value)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


class Api:
    """Request handling without the HTTP server (tested directly)."""

    def __init__(self, store: ServicesStore, token: str, sleep: Callable[[float], None] = time.sleep):
        if len(token) < 16:
            raise ValueError("PV_API_TOKEN: at least 16 characters")
        self.store, self.token, self.sleep = store, token, sleep
        self._flaky = 0
        self._lock = threading.Lock()

    def authorized(self, headers: dict[str, str]) -> bool:
        auth = headers.get("authorization", "")
        given = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("x-api-key", "")
        return bool(given) and hmac.compare_digest(given.encode(), self.token.encode())

    def handle(self, method: str, target: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        try:
            return self._handle(method, target, {k.lower(): v for k, v in headers.items()})
        except ApiError as exc:
            body = json.dumps({"error": {"code": exc.code, "message": exc.message}}).encode()
            return exc.status, {"Content-Type": "application/json", **exc.headers}, body
        except Exception:
            body = json.dumps({"error": {"code": "INTERNAL", "message": "internal error"}}).encode()
            return 500, {"Content-Type": "application/json"}, body

    def _handle(self, method: str, target: str, headers: dict[str, str]):
        parts = urlsplit(target)
        path = parts.path.rstrip("/") or "/"
        if method != "GET":
            raise ApiError(405, "METHOD_NOT_ALLOWED", "only GET", {"Allow": "GET"})
        if path == "/health":
            return 200, {"Content-Type": "application/json"}, b'{"status": "ok"}'
        if path not in ("/v1/services", "/v1/services/cursor"):
            raise ApiError(404, "NOT_FOUND", "unknown path")
        if not self.authorized(headers):
            raise ApiError(401, "UNAUTHORIZED", "token missing or wrong", {"WWW-Authenticate": "Bearer"})
        params = {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        self._simulate(params.get("simulate", ""))
        fmt = params.get("format", "json")
        if fmt not in ("json", "xml"):
            raise ApiError(400, "BAD_PARAMETER", "format: json or xml")
        since = _since(params)
        if path == "/v1/services":
            page = _int(params, "page", 1, 1, 1_000_000)
            size = _int(params, "size", DEFAULT_SIZE, 1, MAX_SIZE)
            rows, total = self.store.page(since, page, size)
            items = [to_json_item(r) for r in rows]
            meta = {"page": page, "size": size, "total": total, "pages": (total + size - 1) // size}
            if fmt == "xml":
                return 200, {"Content-Type": "application/xml; charset=utf-8"}, to_xml("response", items, meta)
            body = {"data": {"items": items}, "meta": meta}
            return 200, {"Content-Type": "application/json; charset=utf-8"}, json.dumps(body, ensure_ascii=False).encode()
        limit = _int(params, "limit", DEFAULT_SIZE, 1, MAX_SIZE)
        after_id = decode_cursor(params["cursor"]) if params.get("cursor") else 0
        rows = self.store.after(since, after_id, limit)
        more = len(rows) > limit
        rows = rows[:limit]
        items = [to_json_item(r) for r in rows]
        next_cursor = encode_cursor(int(rows[-1]["id"])) if more and rows else None
        out_headers = {}
        next_url = None
        if next_cursor:
            query = {k: v for k, v in params.items() if k not in ("cursor", "simulate")}
            query["cursor"] = next_cursor
            next_url = f"{headers.get('x-forwarded-prefix', '')}{path}?{urlencode(query)}"
            out_headers["Link"] = f'<{next_url}>; rel="next"'
        meta = {"limit": limit, "count": len(items), "next_cursor": next_cursor}
        if fmt == "xml":
            return 200, {"Content-Type": "application/xml; charset=utf-8", **out_headers}, to_xml("response", items, meta)
        body = {"items": items, "next_cursor": next_cursor, "next": next_url}
        return 200, {"Content-Type": "application/json; charset=utf-8", **out_headers}, json.dumps(body, ensure_ascii=False).encode()

    def _simulate(self, mode: str) -> None:
        if not mode:
            return
        if mode == "429":
            raise ApiError(429, "RATE_LIMITED", "too many requests", {"Retry-After": "2"})
        if mode == "500":
            raise ApiError(500, "SIMULATED", "simulated server error")
        if mode == "slow":
            self.sleep(SLOW_SECONDS)
            return
        if mode == "flaky":
            with self._lock:
                self._flaky += 1
                odd = self._flaky % 2 == 1
            if odd:
                raise ApiError(429, "RATE_LIMITED", "too many requests", {"Retry-After": "1"})
            return
        raise ApiError(400, "BAD_PARAMETER", "simulate: 429, 500, slow or flaky")


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        server_version = "pv-test-api"
        sys_version = ""

        def _reply(self, method: str, with_body: bool = True) -> None:
            status, headers, body = api.handle(method, self.path, dict(self.headers.items()))
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if with_body:
                self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            self._reply("GET")

        def do_POST(self):  # noqa: N802
            self._reply("POST")

        do_PUT = do_DELETE = do_PATCH = do_POST

        def log_message(self, fmt, *args):  # request line only: never headers (token)
            sys.stderr.write("%s %s\n" % (self.command, urlsplit(self.path).path))

    return Handler


def main() -> int:
    env = dict(os.environ)
    missing = [k for k in ("PV_DB_HOST", "PV_DB_NAME", "PV_DB_USER", "PV_API_TOKEN") if not env.get(k)]
    if missing:
        sys.stderr.write("Missing settings: " + ", ".join(missing) + "\n")
        return 2
    api = Api(ServicesStore(env), env["PV_API_TOKEN"])
    port = int(env.get("PV_API_PORT") or 8090)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api))
    sys.stderr.write(f"pv-test-api on 127.0.0.1:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
