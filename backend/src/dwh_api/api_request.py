"""HTTP request of an API object (I11): the document, cURL import/export, reading pages, records and structure.

The document (meta.object_api_request.request, no credentials in it):
  url          https://host/path (query parameters go to params)
  params       [{"name", "value"}]; "{{watermark}}" in a value is replaced by the increment threshold
  headers      [{"name", "value"}] (Authorization, cookies and key-like headers are refused: use auth)
  auth         {"kind": NONE | BEARER | BASIC | HEADER | QUERY, "user": <BASIC user>, "name": <HEADER/QUERY name>}
               the credential of the object (SecretStore) is the bearer value, the BASIC password or the key
  format       JSON | XML
  records_path JSON: dot path to the array of records ("" = the whole answer); XML: dot path of element names
               from the root to the record element ("items.service")
  pagination   {"kind": NONE | PAGE | OFFSET | CURSOR | NEXT_LINK, "size": 100, "max_pages": 1000,
                PAGE: "page_param", "size_param", "start_page"; OFFSET: "offset_param", "limit_param";
                CURSOR: "cursor_param", "cursor_path"; NEXT_LINK: "next_path" (dot path) or "LINK_HEADER"}
  increment    {"param": "updated_since"} - optional: the parameter that carries the increment threshold
  timeout_seconds 30, retries 3, pause_ms 0
Only GET (B3). Pages are read through http_gateway (B10).
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import shlex
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Iterator

from . import http_gateway
from .http_gateway import HttpGatewayError

AUTH_KINDS = ("NONE", "BEARER", "BASIC", "HEADER", "QUERY")
PAGE_KINDS = ("NONE", "PAGE", "OFFSET", "CURSOR", "NEXT_LINK")
FORMATS = ("JSON", "XML")
SECRET_NAME = re.compile(r"(pass|pwd|secret|token|credential|private[-_]?key|api[-_]?key|access[-_]?key|"
                         r"authorization|cookie|session)", re.I)
NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}$")
WATERMARK = "{{watermark}}"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_WAIT = 60.0


class ApiRequestError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ------------------------------------------------------------------ document


def _pairs(value: Any, what: str) -> list[dict]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ApiRequestError("API_CONFIG_INVALID", f"{what}: list of name/value")
    out = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not NAME.match(item["name"]):
            raise ApiRequestError("API_CONFIG_INVALID", f"{what}: every item needs a name (latin, digits, -_.)")
        text = item.get("value", "")
        if not isinstance(text, (str, int, float)) or len(str(text)) > 2000:
            raise ApiRequestError("API_CONFIG_INVALID", f"{what} {item['name']}: value up to 2000 characters")
        out.append({"name": item["name"], "value": str(text)})
    return out


def normalize(doc: dict, draft: bool = False) -> dict:
    """Checked document with defaults; raises ApiRequestError with the first problem.

    draft=True (saving the draft, cURL import): an XML request may still lack the path to the record element;
    checking the request, publishing and loading need it (draft=False)."""
    if not isinstance(doc, dict):
        raise ApiRequestError("API_CONFIG_INVALID", "HTTP request must be an object")
    url = str(doc.get("url") or "").strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ApiRequestError("API_CONFIG_INVALID", "Address must start with https:// and name a host")
    if parts.username or parts.password or parts.fragment:
        raise ApiRequestError("API_CONFIG_INVALID", "Address must not contain a user, password or #fragment")
    params = _pairs(doc.get("params"), "Parameter")
    if parts.query:  # query of the address moves to params
        params = [{"name": k, "value": v} for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)] + params
        url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    headers = _pairs(doc.get("headers"), "Header")
    for h in headers:
        if SECRET_NAME.search(h["name"]):
            raise ApiRequestError("API_CONFIG_SECRET", f"Header {h['name']} carries a credential: use the authorization "
                                                       "settings and the credential of the object")
    auth = dict(doc.get("auth") or {"kind": "NONE"})
    kind = str(auth.get("kind") or "NONE").upper()
    if kind not in AUTH_KINDS:
        raise ApiRequestError("API_CONFIG_INVALID", f"Authorization: one of {', '.join(AUTH_KINDS)}")
    auth = {"kind": kind}
    if kind == "BASIC":
        user = str((doc.get("auth") or {}).get("user") or "").strip()
        if not user or len(user) > 200 or ":" in user:
            raise ApiRequestError("API_CONFIG_INVALID", "Basic authorization needs a user name (without ':')")
        auth["user"] = user
    if kind in ("HEADER", "QUERY"):
        name = str((doc.get("auth") or {}).get("name") or "").strip()
        if not NAME.match(name):
            raise ApiRequestError("API_CONFIG_INVALID", "Key authorization needs the header or parameter name")
        auth["name"] = name
    for p in params:
        if SECRET_NAME.search(p["name"]) and not (kind == "QUERY" and p["name"] == auth.get("name")):
            raise ApiRequestError("API_CONFIG_SECRET", f"Parameter {p['name']} carries a credential: use the "
                                                       "authorization settings and the credential of the object")
    params = [p for p in params if not (kind == "QUERY" and p["name"] == auth.get("name"))]
    fmt = str(doc.get("format") or "JSON").upper()
    if fmt not in FORMATS:
        raise ApiRequestError("API_CONFIG_INVALID", "Format: JSON or XML")
    records_path = str(doc.get("records_path") or "").strip()
    if records_path and not re.fullmatch(r"[A-Za-z0-9_:\-]+(\.[A-Za-z0-9_:\-]+)*", records_path):
        raise ApiRequestError("API_CONFIG_INVALID", "Path to records: names separated by dots")
    if fmt == "XML" and not records_path and not draft:
        raise ApiRequestError("API_CONFIG_INVALID", "XML needs the path to the record element")
    pg = dict(doc.get("pagination") or {"kind": "NONE"})
    pkind = str(pg.get("kind") or "NONE").upper()
    if pkind not in PAGE_KINDS:
        raise ApiRequestError("API_CONFIG_INVALID", f"Pagination: one of {', '.join(PAGE_KINDS)}")

    def num(key: str, default: int, low: int, high: int) -> int:
        value = pg.get(key, default)
        if not isinstance(value, int) and not (isinstance(value, str) and value.isdigit()):
            raise ApiRequestError("API_CONFIG_INVALID", f"Pagination {key}: whole number {low}-{high}")
        value = int(value)
        if not low <= value <= high:
            raise ApiRequestError("API_CONFIG_INVALID", f"Pagination {key}: whole number {low}-{high}")
        return value

    def pname(key: str, default: str) -> str:
        value = str(pg.get(key) or default).strip()
        if not NAME.match(value):
            raise ApiRequestError("API_CONFIG_INVALID", f"Pagination {key}: parameter name")
        return value

    pagination: dict[str, Any] = {"kind": pkind, "max_pages": num("max_pages", 1000, 1, 100000)}
    if pkind == "PAGE":
        pagination.update(page_param=pname("page_param", "page"), size_param=pname("size_param", "size"),
                          size=num("size", 100, 1, 10000), start_page=num("start_page", 1, 0, 1))
    elif pkind == "OFFSET":
        pagination.update(offset_param=pname("offset_param", "offset"), limit_param=pname("limit_param", "limit"),
                          size=num("size", 100, 1, 10000))
    elif pkind == "CURSOR":
        path = str(pg.get("cursor_path") or "").strip()
        if not path:
            raise ApiRequestError("API_CONFIG_INVALID", "Cursor pagination needs the path to the next cursor")
        pagination.update(cursor_param=pname("cursor_param", "cursor"), cursor_path=path)
        if pg.get("size_param"):
            pagination.update(size_param=pname("size_param", "limit"), size=num("size", 100, 1, 10000))
    elif pkind == "NEXT_LINK":
        path = str(pg.get("next_path") or "LINK_HEADER").strip()
        pagination.update(next_path=path)
        if pg.get("size_param"):
            pagination.update(size_param=pname("size_param", "limit"), size=num("size", 100, 1, 10000))
    increment = None
    if doc.get("increment") and (doc["increment"] or {}).get("param"):
        param = str(doc["increment"]["param"]).strip()
        if not NAME.match(param):
            raise ApiRequestError("API_CONFIG_INVALID", "Increment parameter: name")
        increment = {"param": param}

    def bounded(key: str, default: int, low: int, high: int) -> int:
        value = doc.get(key, default)
        if not isinstance(value, int) or not low <= value <= high:
            raise ApiRequestError("API_CONFIG_INVALID", f"{key}: whole number {low}-{high}")
        return value

    return {"url": url, "params": params, "headers": headers, "auth": auth, "format": fmt,
            "records_path": records_path, "pagination": pagination, "increment": increment,
            "timeout_seconds": bounded("timeout_seconds", 30, 1, 300), "retries": bounded("retries", 3, 0, 5),
            "pause_ms": bounded("pause_ms", 0, 0, 60000)}


# ------------------------------------------------------------------ cURL


@dataclass
class CurlImport:
    request: dict
    secret: str | None
    notes: list[str]


_IGNORED = {"-s", "--silent", "-S", "--show-error", "--compressed", "-L", "--location", "-i", "--include", "-v",
            "--verbose", "-g", "--globoff"}
_VALUED_IGNORED = {"--connect-timeout", "-m", "--max-time", "--retry"}


def import_curl(text: str) -> CurlImport:
    """cURL command -> request document + credential (never executed: only split into words)."""
    text = re.sub(r"\\\r?\n", " ", text.strip())
    text = re.sub(r"\^\r?\n", " ", text)  # Windows cmd continuation
    try:
        words = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ApiRequestError("CURL_INVALID", f"cURL command cannot be split: {exc}") from None
    if not words or words[0].lower() not in ("curl", "curl.exe"):
        raise ApiRequestError("CURL_INVALID", "Command must start with curl")
    url = None
    headers: list[tuple[str, str]] = []
    data: list[str] = []
    user = None
    get_flag = False
    notes: list[str] = []
    i = 1
    while i < len(words):
        w = words[i]

        def value() -> str:
            nonlocal i
            i += 1
            if i >= len(words):
                raise ApiRequestError("CURL_INVALID", f"{w} needs a value")
            return words[i]

        if w in ("-X", "--request"):
            method = value().upper()
            if method != "GET":
                raise ApiRequestError("CURL_METHOD", f"Only GET requests are allowed (cURL has {method})")
        elif w in ("-H", "--header"):
            name, _, val = value().partition(":")
            headers.append((name.strip(), val.strip()))
        elif w in ("-u", "--user"):
            user = value()
        elif w in ("-G", "--get"):
            get_flag = True
        elif w in ("-d", "--data", "--data-urlencode", "--data-raw", "--data-ascii"):
            data.append(value())
        elif w == "--url":
            url = value()
        elif w in ("-k", "--insecure"):
            notes.append("--insecure ignored: certificates are always checked")
        elif w in _IGNORED:
            pass
        elif w in _VALUED_IGNORED:
            value()
        elif w.startswith("-"):
            raise ApiRequestError("CURL_UNSUPPORTED", f"cURL option {w} is not supported")
        elif url is None:
            url = w
        else:
            raise ApiRequestError("CURL_INVALID", f"Unexpected word: {w[:40]}")
        i += 1
    if not url:
        raise ApiRequestError("CURL_INVALID", "cURL command has no address")
    if data and not get_flag:
        raise ApiRequestError("CURL_METHOD", "cURL with data sends POST; only GET is allowed (use -G for parameters)")
    parts = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    for d in data:
        params += urllib.parse.parse_qsl(d, keep_blank_values=True) if "=" in d else [(d, "")]
    base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    auth: dict[str, str] = {"kind": "NONE"}
    secret = None
    plain_headers = []
    for name, val in headers:
        low = name.lower()
        if low == "authorization":
            scheme, _, rest = val.partition(" ")
            if scheme.lower() == "bearer" and rest.strip():
                auth, secret = {"kind": "BEARER"}, rest.strip()
            elif scheme.lower() == "basic" and rest.strip():
                try:
                    u, _, p = base64.b64decode(rest.strip()).decode("utf-8").partition(":")
                except (ValueError, UnicodeDecodeError):
                    raise ApiRequestError("CURL_INVALID", "Basic authorization value is not base64") from None
                auth, secret = {"kind": "BASIC", "user": u}, p
            else:
                raise ApiRequestError("CURL_UNSUPPORTED", "Authorization header: only Bearer and Basic are supported")
        elif SECRET_NAME.search(low) and low not in ("accept", "content-type"):
            if auth["kind"] != "NONE":
                raise ApiRequestError("CURL_UNSUPPORTED", "cURL has more than one credential")
            auth, secret = {"kind": "HEADER", "name": name}, val
        elif low in ("accept-encoding", "user-agent", "connection", "content-length", "host"):
            notes.append(f"header {name} ignored")
        else:
            plain_headers.append({"name": name, "value": val})
    if user is not None:
        if auth["kind"] != "NONE":
            raise ApiRequestError("CURL_UNSUPPORTED", "cURL has more than one credential")
        u, _, p = user.partition(":")
        auth, secret = {"kind": "BASIC", "user": u}, p
    plain_params = []
    for name, val in params:
        if SECRET_NAME.search(name):
            if auth["kind"] != "NONE":
                raise ApiRequestError("CURL_UNSUPPORTED", "cURL has more than one credential")
            auth, secret = {"kind": "QUERY", "name": name}, val
        else:
            plain_params.append({"name": name, "value": val})
    accept = next((h["value"].lower() for h in plain_headers if h["name"].lower() == "accept"), "")
    query = dict(params)
    fmt = "XML" if "xml" in accept or str(query.get("format", "")).lower() == "xml" else "JSON"
    request = {"url": base, "params": plain_params, "headers": plain_headers, "auth": auth, "format": fmt,
               "records_path": "", "pagination": {"kind": "NONE"}}
    if secret is not None and not secret:
        secret = None
        notes.append("credential in cURL is empty")
    if fmt == "XML":
        notes.append("XML: set the path to the record element")
    return CurlImport(request=request, secret=secret, notes=notes)


def export_curl(doc: dict) -> str:
    """cURL text of the request; the credential is shown as *** (B5)."""
    doc = normalize(doc, draft=True)
    params = [(p["name"], p["value"]) for p in doc["params"]]
    auth = doc["auth"]
    if auth["kind"] == "QUERY":
        params.append((auth["name"], "***"))
    url = http_gateway.build_url(doc["url"], params)
    words = ["curl", shlex.quote(url)]
    for h in doc["headers"]:
        words += ["-H", shlex.quote(f"{h['name']}: {h['value']}")]
    if auth["kind"] == "BEARER":
        words += ["-H", shlex.quote("Authorization: Bearer ***")]
    elif auth["kind"] == "BASIC":
        words += ["-u", shlex.quote(f"{auth['user']}:***")]
    elif auth["kind"] == "HEADER":
        words += ["-H", shlex.quote(f"{auth['name']}: ***")]
    return " ".join(words)


# ------------------------------------------------------------------ answers


def _path(doc: Any, path: str) -> Any:
    if not path:
        return doc
    node = doc
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def _xml_value(el: ET.Element) -> Any:
    if el.get("null") == "true":
        return None
    children = list(el)
    if not children:
        return el.text if el.text is not None else ""
    out: dict[str, Any] = {}
    for c in children:
        value = _xml_value(c)
        if c.tag in out:
            out[c.tag] = (out[c.tag] if isinstance(out[c.tag], list) else [out[c.tag]]) + [value]
        else:
            out[c.tag] = value
    return out


@dataclass
class Page:
    records: list[dict]
    next_cursor: str | None = None
    next_link: str | None = None


def parse_page(body: bytes, doc: dict, headers: dict[str, str] | None = None) -> Page:
    headers = headers or {}
    pg = doc["pagination"]
    if doc["format"] == "JSON":
        try:
            data = json.loads(body.decode("utf-8-sig"), parse_float=Decimal)
        except (ValueError, UnicodeDecodeError):
            raise ApiRequestError("API_BAD_JSON", "Answer is not JSON") from None
        found = _path(data, doc["records_path"])
        if found is None:
            raise ApiRequestError("API_BAD_RECORDS", f"No records at path '{doc['records_path'] or '(root)'}'")
        if isinstance(found, dict):
            found = [found]
        if not isinstance(found, list) or any(not isinstance(r, dict) for r in found):
            raise ApiRequestError("API_BAD_RECORDS", f"Path '{doc['records_path'] or '(root)'}' is not a list of objects")
        cursor = _path(data, pg["cursor_path"]) if pg["kind"] == "CURSOR" else None
        link = (_path(data, pg["next_path"]) if pg["kind"] == "NEXT_LINK" and pg["next_path"] != "LINK_HEADER"
                else None)
        records = found
    else:
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            raise ApiRequestError("API_BAD_XML", "Answer is not XML") from None
        parts = doc["records_path"].split(".")
        if parts and parts[0] == root.tag:
            parts = parts[1:]
        records = []
        for el in root.findall("/".join(parts)) if parts else [root]:
            value = _xml_value(el)
            records.append(value if isinstance(value, dict) else {el.tag: value})

        def xml_path(path: str) -> str | None:
            items = path.split(".")
            if items and items[0] == root.tag:
                items = items[1:]
            node = root.find("/".join(items)) if items else root
            return None if node is None or node.get("null") == "true" else (node.text or None)

        cursor = xml_path(pg["cursor_path"]) if pg["kind"] == "CURSOR" else None
        link = xml_path(pg["next_path"]) if pg["kind"] == "NEXT_LINK" and pg["next_path"] != "LINK_HEADER" else None
    if pg["kind"] == "NEXT_LINK" and pg["next_path"] == "LINK_HEADER":
        link = _link_next(headers.get("link", ""))
    return Page(records=records, next_cursor=str(cursor) if cursor not in (None, "") else None,
                next_link=str(link) if link not in (None, "") else None)


def _link_next(value: str) -> str | None:
    for part in value.split(","):
        m = re.match(r'\s*<([^>]+)>\s*;(.*)', part)
        if m and re.search(r'rel\s*=\s*"?next"?', m.group(2)):
            return m.group(1)
    return None


def flatten(record: dict, prefix: str = "") -> dict[str, Any]:
    """Nested objects become a.b columns; lists stay one value (JSON text when staged)."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            out.update(flatten(value, name + "."))
        else:
            out[name] = value
    return out


_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?(Z|[+-]\d{2}:?\d{2})?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _kind(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "bigint"
    if isinstance(value, (float, Decimal)):
        return "numeric"
    if isinstance(value, (list, dict)):
        return "jsonb"
    text = str(value)
    if _TS.match(text):
        return "timestamp with time zone" if re.search(r"(Z|[+-]\d{2}:?\d{2})$", text) else "timestamp without time zone"
    if _DATE.match(text):
        return "date"
    return "text"


def infer_columns(records: list[dict]) -> list[dict]:
    """Columns in first-seen order with a suggested PostgreSQL type (the user can change it, B8)."""
    order: list[str] = []
    kinds: dict[str, set[str]] = {}
    nulls: dict[str, bool] = {}
    for index, record in enumerate(records):
        flat = flatten(record)
        for name in order:
            if name not in flat:
                nulls[name] = True
        for name, value in flat.items():
            if name not in kinds:
                order.append(name)
                kinds[name] = set()
                nulls[name] = index > 0  # missing in the earlier records
            kind = _kind(value)
            if kind is None:
                nulls[name] = True
            else:
                kinds[name].add(kind)
    columns = []
    for name in order:
        found = kinds[name]
        if not found:
            kind = "text"
        elif len(found) == 1:
            kind = next(iter(found))
        elif found <= {"bigint", "numeric"}:
            kind = "numeric"
        elif found <= {"timestamp with time zone", "timestamp without time zone", "date"}:
            kind = "timestamp with time zone" if "timestamp with time zone" in found else "timestamp without time zone"
        else:
            kind = "text"
        columns.append({"name": name, "type": kind, "nullable": nulls.get(name, False)})
    return columns


def to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


# ------------------------------------------------------------------ reading pages


def watermark_threshold(watermark: str | None, overlap_minutes: int) -> str | None:
    """Increment threshold for the request parameter: the watermark minus the overlap (ISO 8601)."""
    if not watermark:
        return None
    text = watermark.strip()
    if re.fullmatch(r"-?\d+", text):
        return text
    try:
        moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return text
    return (moment - dt.timedelta(minutes=overlap_minutes)).isoformat()


def credential_parts(doc: dict, secret: str | None) -> tuple[list[tuple[str, str]], dict[str, str]]:
    auth = doc["auth"]
    if auth["kind"] == "NONE":
        return [], {}
    if not secret:
        raise ApiRequestError("API_CREDENTIAL_MISSING", "The object has no credential (set it in the HTTP request)")
    if auth["kind"] == "BEARER":
        return [], {"Authorization": f"Bearer {secret}"}
    if auth["kind"] == "BASIC":
        raw = base64.b64encode(f"{auth['user']}:{secret}".encode()).decode()
        return [], {"Authorization": f"Basic {raw}"}
    if auth["kind"] == "HEADER":
        return [], {auth["name"]: secret}
    return [(auth["name"], secret)], {}


def read_pages(doc: dict, secret: str | None, *, watermark: str | None = None, overlap_minutes: int = 0,
               get: Callable[..., http_gateway.HttpResponse] = http_gateway.get,
               settings: http_gateway.GatewaySettings | None = None, sleep: Callable[[float], None] = time.sleep,
               log: Callable[[str], None] = lambda m: None, max_pages: int | None = None) -> Iterator[list[dict]]:
    """Records of every page, page by page (retries on 429/5xx/timeout/no connection)."""
    doc = normalize(doc)
    pg = doc["pagination"]
    limit = max_pages or pg["max_pages"]
    secret_params, secret_headers = credential_parts(doc, secret)
    headers = {h["name"]: h["value"] for h in doc["headers"]}
    headers.update(secret_headers)
    headers.setdefault("Accept", "application/xml" if doc["format"] == "XML" else "application/json")
    threshold = watermark_threshold(watermark, overlap_minutes)
    base_params = []
    for p in doc["params"]:
        value = p["value"]
        if WATERMARK in value:
            if threshold is None:
                continue  # first run of an increment: the parameter is left out
            value = value.replace(WATERMARK, threshold)
        base_params.append((p["name"], value))
    if doc["increment"] and threshold is not None:
        base_params = [(n, v) for n, v in base_params if n != doc["increment"]["param"]]
        base_params.append((doc["increment"]["param"], threshold))
    base_host = urllib.parse.urlsplit(doc["url"]).netloc
    page_no = pg.get("start_page", 1)
    offset = 0
    cursor: str | None = None
    next_url: str | None = None
    for number in range(1, limit + 2):
        if number > limit:
            raise ApiRequestError("API_TOO_MANY_PAGES", f"More than {limit} pages; raise the limit or narrow the request")
        params = list(base_params)
        url = doc["url"]
        if pg["kind"] == "PAGE":
            params += [(pg["page_param"], str(page_no)), (pg["size_param"], str(pg["size"]))]
        elif pg["kind"] == "OFFSET":
            params += [(pg["offset_param"], str(offset)), (pg["limit_param"], str(pg["size"]))]
        elif pg["kind"] == "CURSOR":
            if "size_param" in pg:
                params.append((pg["size_param"], str(pg["size"])))
            if cursor:
                params.append((pg["cursor_param"], cursor))
        elif pg["kind"] == "NEXT_LINK" and next_url:
            url, params = next_url, []
            if "size_param" in pg and pg["size_param"] not in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query):
                params.append((pg["size_param"], str(pg["size"])))
        elif pg["kind"] == "NEXT_LINK" and "size_param" in pg:
            params.append((pg["size_param"], str(pg["size"])))
        response = _get_with_retries(get, url, params + secret_params, headers, doc, settings, sleep, log)
        page = parse_page(response.body, doc, response.headers)
        yield page.records
        if pg["kind"] == "NONE":
            return
        if pg["kind"] == "PAGE":
            if len(page.records) < pg["size"]:
                return
            page_no += 1
        elif pg["kind"] == "OFFSET":
            if len(page.records) < pg["size"]:
                return
            offset += len(page.records)
        elif pg["kind"] == "CURSOR":
            if not page.next_cursor or not page.records:
                return
            cursor = page.next_cursor
        else:
            if not page.next_link or not page.records:
                return
            nxt = urllib.parse.urljoin(response.url, page.next_link)
            if urllib.parse.urlsplit(nxt).netloc != base_host:
                raise ApiRequestError("API_NEXT_LINK_HOST", "Next page link points to another host")
            if doc["auth"]["kind"] == "QUERY":  # the key is never taken from the answer
                q = [(k, v) for k, v in urllib.parse.parse_qsl(urllib.parse.urlsplit(nxt).query, keep_blank_values=True)
                     if k != doc["auth"]["name"]]
                p = urllib.parse.urlsplit(nxt)
                nxt = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q), ""))
            next_url = nxt
        if doc["pause_ms"]:
            sleep(doc["pause_ms"] / 1000)


def _get_with_retries(get, url, params, headers, doc, settings, sleep, log) -> http_gateway.HttpResponse:
    attempt = 0
    while True:
        try:
            return get(url, params, headers, settings=settings, timeout=float(doc["timeout_seconds"]))
        except HttpGatewayError as exc:
            retryable = exc.status in RETRY_STATUSES or exc.code in ("API_TIMEOUT", "API_UNREACHABLE")
            if not retryable or attempt >= doc["retries"]:
                raise ApiRequestError(exc.code, exc.message) from None
            attempt += 1
            wait = min(exc.retry_after if exc.retry_after is not None else float(2 ** attempt), MAX_RETRY_WAIT)
            log(f"STAGE: {exc.code}, repeat {attempt} of {doc['retries']} in {wait:.0f} s")
            sleep(wait)
