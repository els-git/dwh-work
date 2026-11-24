"""I11: HTTP request of an API object, cURL import/export, pages, records, HTTP gateway (no network)."""
from __future__ import annotations

import base64
import io
import json
import sys
import unittest
import urllib.error
import urllib.parse
from email.message import Message
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "etl" / "src"))

from dwh_api import api_request as ar  # noqa: E402
from dwh_api import http_gateway as hg  # noqa: E402
from dwh_api.api_request import ApiRequestError  # noqa: E402

BASE = {"url": "https://api.example/v1/services", "format": "JSON", "records_path": "data.items"}


def doc(**extra) -> dict:
    return {**BASE, **extra}


class NormalizeTest(unittest.TestCase):
    def test_query_of_address_moves_to_params(self):
        d = ar.normalize(doc(url="https://api.example/v1/s?a=1&b=2", params=[{"name": "c", "value": "3"}]))
        self.assertEqual(d["url"], "https://api.example/v1/s")
        self.assertEqual([(p["name"], p["value"]) for p in d["params"]], [("a", "1"), ("b", "2"), ("c", "3")])
        self.assertEqual(d["pagination"], {"kind": "NONE", "max_pages": 1000})

    def test_credentials_outside_auth_refused(self):
        for bad in (doc(headers=[{"name": "Authorization", "value": "Bearer x"}]),
                    doc(headers=[{"name": "X-Api-Key", "value": "x"}]),
                    doc(params=[{"name": "access_token", "value": "x"}])):
            with self.assertRaises(ApiRequestError) as ctx:
                ar.normalize(bad)
            self.assertEqual(ctx.exception.code, "API_CONFIG_SECRET")

    def test_invalid_documents(self):
        for bad in (doc(url="ftp://x/y"), doc(url="https://u:p@x/y"), doc(format="CSV"),
                    {**BASE, "format": "XML", "records_path": ""}, doc(auth={"kind": "BASIC"}),
                    doc(pagination={"kind": "PAGE", "size": 0}), doc(pagination={"kind": "CURSOR"}),
                    doc(records_path="a b")):
            with self.assertRaises(ApiRequestError):
                ar.normalize(bad)

    def test_query_key_auth_param_removed_from_params(self):
        d = ar.normalize(doc(auth={"kind": "QUERY", "name": "api_key"}, params=[{"name": "api_key", "value": "x"}]))
        self.assertEqual(d["params"], [])


class CurlTest(unittest.TestCase):
    def test_bearer_and_params(self):
        c = ar.import_curl("curl -s 'https://h.example/pv-api/v1/services?page=1&size=5' "
                           "-H 'Authorization: Bearer SECRET1' -H 'Accept: application/json'")
        self.assertEqual(c.secret, "SECRET1")
        self.assertEqual(c.request["auth"], {"kind": "BEARER"})
        self.assertEqual(c.request["url"], "https://h.example/pv-api/v1/services")
        self.assertEqual([(p["name"], p["value"]) for p in c.request["params"]], [("page", "1"), ("size", "5")])
        self.assertNotIn("SECRET1", json.dumps(c.request))
        ar.normalize(c.request)

    def test_basic_header_key_and_query_key(self):
        raw = base64.b64encode(b"svc:pw1").decode()
        c = ar.import_curl(f'curl https://h/x -H "Authorization: Basic {raw}"')
        self.assertEqual((c.request["auth"], c.secret), ({"kind": "BASIC", "user": "svc"}, "pw1"))
        c = ar.import_curl("curl -u svc:pw2 https://h/x")
        self.assertEqual((c.request["auth"]["user"], c.secret), ("svc", "pw2"))
        c = ar.import_curl("curl https://h/x -H 'X-Api-Key: K3'")
        self.assertEqual((c.request["auth"], c.secret), ({"kind": "HEADER", "name": "X-Api-Key"}, "K3"))
        c = ar.import_curl("curl 'https://h/x?api_key=K4&format=xml'")
        self.assertEqual((c.request["auth"], c.secret, c.request["format"]), ({"kind": "QUERY", "name": "api_key"}, "K4", "XML"))

    def test_get_data_and_windows_continuation(self):
        c = ar.import_curl('curl -G https://h/x ^\n --data-urlencode "updated_since=2026-01-01" -d size=10')
        self.assertEqual([(p["name"], p["value"]) for p in c.request["params"]],
                         [("updated_since", "2026-01-01"), ("size", "10")])

    def test_refusals_and_no_shell(self):
        for bad, code in (("curl -X POST https://h/x", "CURL_METHOD"), ("curl https://h/x -d a=1", "CURL_METHOD"),
                          ("curl --proxy p https://h/x", "CURL_UNSUPPORTED"), ("wget https://h/x", "CURL_INVALID"),
                          ("curl 'https://h/x", "CURL_INVALID")):
            with self.assertRaises(ApiRequestError) as ctx:
                ar.import_curl(bad)
            self.assertEqual(ctx.exception.code, code, bad)
        c = ar.import_curl("curl 'https://h/x?q=$(rm -rf /)'")
        self.assertEqual(c.request["params"][0]["value"], "$(rm -rf /)")

    def test_export_masks_credential(self):
        text = ar.export_curl(doc(auth={"kind": "BEARER"}, headers=[{"name": "Accept", "value": "application/json"}]))
        self.assertIn("Authorization: Bearer ***", text)
        text = ar.export_curl(doc(auth={"kind": "QUERY", "name": "api_key"}))
        self.assertIn("api_key=%2A%2A%2A", text)
        back = ar.import_curl(ar.export_curl(doc(auth={"kind": "BASIC", "user": "svc"})))
        self.assertEqual(back.request["auth"], {"kind": "BASIC", "user": "svc"})


class PageTest(unittest.TestCase):
    def test_json_records_and_cursor(self):
        d = ar.normalize(doc(records_path="items", pagination={"kind": "CURSOR", "cursor_path": "next_cursor"}))
        page = ar.parse_page(json.dumps({"items": [{"id": 1}, {"id": 2}], "next_cursor": "abc"}).encode(), d)
        self.assertEqual((len(page.records), page.next_cursor), (2, "abc"))
        with self.assertRaises(ApiRequestError):
            ar.parse_page(b'{"items": 5}', d)
        with self.assertRaises(ApiRequestError):
            ar.parse_page(b"<html>", d)

    def test_xml_records_null_and_nested(self):
        d = ar.normalize({**BASE, "format": "XML", "records_path": "response.items.service",
                          "pagination": {"kind": "CURSOR", "cursor_path": "meta.next_cursor"}})
        body = (b'<?xml version="1.0"?><response><items><service><id>1</id><v null="true"/><a><b>x</b></a></service>'
                b'<service><id>2</id><v>t</v></service></items><meta><next_cursor>c2</next_cursor></meta></response>')
        page = ar.parse_page(body, d)
        self.assertEqual(page.records[0], {"id": "1", "v": None, "a": {"b": "x"}})
        self.assertEqual(page.next_cursor, "c2")
        self.assertEqual(ar.flatten(page.records[0]), {"id": "1", "v": None, "a.b": "x"})

    def test_link_header(self):
        d = ar.normalize(doc(records_path="items", pagination={"kind": "NEXT_LINK"}))
        page = ar.parse_page(b'{"items": [{"id": 1}]}', d, {"link": '</v1/s?cursor=x>; rel="next", </a>; rel="prev"'})
        self.assertEqual(page.next_link, "/v1/s?cursor=x")

    def test_infer_columns(self):
        cols = ar.infer_columns([
            {"id": 1, "code": "A", "active": True, "variables": [{"k": 1}], "updated_at": "2026-11-24T05:42:44+00:00",
             "amount": 1, "day": "2026-11-24", "meta": {"a": 1}},
            {"id": 2, "code": None, "active": False, "variables": [], "updated_at": "2026-11-24T05:42:44.5+00:00",
             "amount": ar.Decimal("1.5"), "day": "2026-11-25", "meta": {"a": 2}, "late": "x"}])
        types = {c["name"]: (c["type"], c["nullable"]) for c in cols}
        self.assertEqual(types["id"], ("bigint", False))
        self.assertEqual(types["code"], ("text", True))
        self.assertEqual(types["active"][0], "boolean")
        self.assertEqual(types["variables"][0], "jsonb")
        self.assertEqual(types["updated_at"][0], "timestamp with time zone")
        self.assertEqual(types["amount"][0], "numeric")
        self.assertEqual(types["day"][0], "date")
        self.assertEqual(types["meta.a"][0], "bigint")
        self.assertEqual(types["late"], ("text", True))
        self.assertEqual(ar.to_text([{"k": 1}]), '[{"k": 1}]')
        self.assertEqual(ar.to_text(False), "false")


class FakeGet:
    """Answers by a function of (url, params); records calls."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, url, params, headers, settings=None, timeout=30.0):
        self.calls.append((url, list(params), dict(headers)))
        result = self.answer(url, dict(params), len(self.calls))
        if isinstance(result, hg.HttpGatewayError):
            raise result
        body, hdrs = result if isinstance(result, tuple) else (result, {})
        return hg.HttpResponse(200, hdrs, json.dumps(body).encode(), hg.build_url(url, params))


ROWS = [{"id": i, "updated_at": f"2026-11-{i:02d}T00:00:00+00:00"} for i in range(1, 8)]


class ReadPagesTest(unittest.TestCase):
    def read(self, d, answer, secret=None, **kw):
        get = FakeGet(answer)
        pages = list(ar.read_pages(d, secret, get=get, sleep=lambda s: None, **kw))
        return pages, get

    def test_page_pagination_and_bearer(self):
        d = doc(records_path="data.items", auth={"kind": "BEARER"},
                pagination={"kind": "PAGE", "page_param": "page", "size_param": "size", "size": 3})

        def answer(url, p, n):
            page, size = int(p["page"]), int(p["size"])
            return {"data": {"items": ROWS[(page - 1) * size: page * size]}}
        pages, get = self.read(d, answer, secret="S")
        self.assertEqual([len(p) for p in pages], [3, 3, 1])
        self.assertEqual(get.calls[0][2]["Authorization"], "Bearer S")

    def test_cursor_offset_and_next_link(self):
        d = doc(records_path="items", pagination={"kind": "CURSOR", "cursor_path": "next", "cursor_param": "c"})

        def by_cursor(url, p, n):
            start = int(p.get("c", 0))
            return {"items": ROWS[start:start + 3], "next": str(start + 3) if start + 3 < len(ROWS) else None}
        pages, _ = self.read(d, by_cursor)
        self.assertEqual(sum(len(p) for p in pages), 7)
        d = doc(records_path="items", pagination={"kind": "OFFSET", "size": 4})
        pages, _ = self.read(d, lambda u, p, n: {"items": ROWS[int(p["offset"]):int(p["offset"]) + 4]})
        self.assertEqual([len(p) for p in pages], [4, 3])
        d = doc(records_path="items", pagination={"kind": "NEXT_LINK", "next_path": "next"})
        pages, get = self.read(d, lambda u, p, n: {"items": ROWS[:2], "next": "/v1/services?part=2" if n == 1 else None})
        self.assertEqual(get.calls[1][0], "https://api.example/v1/services?part=2")
        with self.assertRaises(ApiRequestError) as ctx:
            self.read(d, lambda u, p, n: {"items": ROWS[:1], "next": "https://evil.example/x"})
        self.assertEqual(ctx.exception.code, "API_NEXT_LINK_HOST")

    def test_retries_on_429_then_success_and_401_stops(self):
        d = doc(records_path="items")
        waits = []

        def answer(url, p, n):
            if n < 3:
                return hg.HttpGatewayError("HTTP_429", "busy", 429, retry_after=2)
            return {"items": ROWS}
        get = FakeGet(answer)
        pages = list(ar.read_pages(d, None, get=get, sleep=waits.append))
        self.assertEqual((len(pages[0]), waits), (7, [2, 2]))
        with self.assertRaises(ApiRequestError) as ctx:
            self.read(d, lambda u, p, n: hg.HttpGatewayError("HTTP_401", "no", 401))
        self.assertEqual(ctx.exception.code, "HTTP_401")
        with self.assertRaises(ApiRequestError) as ctx:
            self.read(doc(records_path="items", retries=1), lambda u, p, n: hg.HttpGatewayError("HTTP_500", "x", 500))
        self.assertEqual(ctx.exception.code, "HTTP_500")

    def test_increment_threshold_and_page_limit(self):
        d = doc(records_path="items", increment={"param": "updated_since"},
                params=[{"name": "updated_since", "value": "{{watermark}}"}])
        _, get = self.read(d, lambda u, p, n: {"items": []})
        self.assertNotIn("updated_since", dict(get.calls[0][1]))
        _, get = self.read(d, lambda u, p, n: {"items": []}, watermark="2026-11-24T10:00:00+00:00", overlap_minutes=10)
        self.assertEqual(dict(get.calls[0][1])["updated_since"], "2026-11-24T09:50:00+00:00")
        d = doc(records_path="items", pagination={"kind": "PAGE", "size": 1, "max_pages": 2})
        with self.assertRaises(ApiRequestError) as ctx:
            self.read(d, lambda u, p, n: {"items": ROWS[:1]})
        self.assertEqual(ctx.exception.code, "API_TOO_MANY_PAGES")

    def test_credential_required(self):
        with self.assertRaises(ApiRequestError) as ctx:
            self.read(doc(records_path="items", auth={"kind": "BEARER"}), lambda u, p, n: {"items": []})
        self.assertEqual(ctx.exception.code, "API_CREDENTIAL_MISSING")


class FakeOpener:
    def __init__(self, result):
        self.result, self.requests = result, []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeResponse(io.BytesIO):
    def __init__(self, body, url, status=200, headers=None):
        super().__init__(body)
        self.status, self._url = status, url
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def geturl(self):
        return self._url


class GatewayTest(unittest.TestCase):
    S = hg.GatewaySettings(allowed_hosts=frozenset({"api.example", "local.test:8090"}),
                           plain_hosts=frozenset({"local.test:8090"}), max_bytes=100)

    def test_url_rules(self):
        for url, code in (("https://other.example/x", "API_HOST_NOT_ALLOWED"), ("http://api.example/x", "API_PLAIN_HTTP"),
                          ("https://u:p@api.example/x", "API_URL_INVALID"), ("file:///etc/passwd", "API_URL_INVALID")):
            with self.assertRaises(hg.HttpGatewayError) as ctx:
                hg.get(url, settings=self.S, opener=FakeOpener(None))
            self.assertEqual(ctx.exception.code, code, url)
        hg.check_url("http://local.test:8090/x", self.S)

    def test_get_ok_redirect_large_and_shown_url(self):
        url = "https://api.example/v1?q=1"
        r = hg.get("https://api.example/v1", [("q", "1")], {"Accept": "application/json"}, settings=self.S,
                   opener=FakeOpener(FakeResponse(b'{"a": 1}', url)))
        self.assertEqual((r.status, r.body), (200, b'{"a": 1}'))
        redirect = urllib.error.HTTPError("https://api.example/v1", 302, "Found", Message(), io.BytesIO(b""))
        with self.assertRaises(hg.HttpGatewayError) as ctx:
            hg.get("https://api.example/v1", [("api_key", "SECRET")], settings=self.S, opener=FakeOpener(redirect))
        self.assertEqual(ctx.exception.code, "HTTP_302")
        self.assertNotIn("SECRET", ctx.exception.message)
        with self.assertRaises(hg.HttpGatewayError) as ctx:
            hg.get("https://api.example/v1", settings=self.S, opener=FakeOpener(FakeResponse(b"x" * 101, url)))
        self.assertEqual(ctx.exception.code, "API_RESPONSE_TOO_LARGE")
        headers = Message()
        headers["Retry-After"] = "7"
        busy = urllib.error.HTTPError("https://api.example/v1", 429, "Busy", headers, io.BytesIO(b""))
        with self.assertRaises(hg.HttpGatewayError) as ctx:
            hg.get("https://api.example/v1", settings=self.S, opener=FakeOpener(busy))
        self.assertEqual((ctx.exception.status, ctx.exception.retry_after), (429, 7.0))

    def test_settings_from_env(self):
        s = hg.GatewaySettings.from_env({"DWH_HTTP_ALLOWED_HOSTS": " A.example , b:1 ", "DWH_HTTP_MAX_BYTES": "10",
                                         "DWH_HTTP_CA_FILE": "/x.crt"})
        self.assertEqual((s.allowed_hosts, s.max_bytes, s.ca_file), (frozenset({"a.example", "b:1"}), 10, "/x.crt"))


class EngineChunksTest(unittest.TestCase):
    def test_api_chunks_text_values_and_drift(self):
        import run_object

        plan = run_object.Plan("stg", "t", [
            {"target_column": "id", "source_column": "id", "source_type": "BIGINT", "target_type": "BIGINT",
             "write_policy": "AUTHORITATIVE"},
            {"target_column": "meta_a", "source_column": "meta.a", "source_type": "TEXT", "target_type": "TEXT",
             "write_policy": "AUTHORITATIVE"},
            {"target_column": "flag", "source_column": "flag", "source_type": "BOOLEAN", "target_type": "BOOLEAN",
             "write_policy": "AUTHORITATIVE"}], ["id"])

        def pages(records):
            def reader(doc, secret, watermark=None, overlap_minutes=0, log=None):
                yield from records
            return reader

        chunks = list(run_object.api_chunks({}, None, plan, None, lambda m: None, size=2, read_pages=pages(
            [[{"id": 1, "meta": {"a": "x"}, "flag": True}, {"id": 2, "flag": None}], [{"id": 3, "meta": {"a": [1]}}]])))
        self.assertEqual(chunks, [[["1", "x", "true"], ["2", None, None]], [["3", "[1]", None]]])
        with self.assertRaises(run_object.RunError) as ctx:
            list(run_object.api_chunks({}, None, plan, None, lambda m: None, read_pages=pages([[{"id": 1}]])))
        self.assertEqual(ctx.exception.code, "SOURCE_DRIFT")


if __name__ == "__main__":
    unittest.main()
