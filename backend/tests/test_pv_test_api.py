"""I11-05: test API service over PROCESS_VIEWER services and its setup tool (no database, no network)."""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "test_api"))
sys.path.insert(0, str(ROOT / "tools" / "server"))
import pv_test_api as api_mod  # noqa: E402
import dwh_test_api_setup as setup  # noqa: E402

TOKEN = "t" * 32
T0 = dt.datetime(2026, 11, 20, 10, 0, tzinfo=dt.timezone.utc)


def make_rows(n: int) -> list[dict]:
    return [{"id": i, "process_id": 100 + i, "code": f"S{i}", "description": "d", "local_service": "",
             "method": "GET", "url": f"https://x/{i}", "environment": "test", "variables": [{"k": i}],
             "active": i % 2 == 0, "curl_text": "", "created_at": T0, "updated_at": T0 + dt.timedelta(hours=i)}
            for i in range(1, n + 1)]


class FakeStore:
    def __init__(self, rows):
        self.rows = rows

    def _filter(self, since):
        return [r for r in self.rows if since is None or r["updated_at"] >= since]

    def page(self, since, page, size):
        rows = self._filter(since)
        return rows[(page - 1) * size: page * size], len(rows)

    def after(self, since, after_id, limit):
        return [r for r in self._filter(since) if r["id"] > after_id][: limit + 1]


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.slept = []
        self.api = api_mod.Api(FakeStore(make_rows(7)), TOKEN, sleep=self.slept.append)
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def get(self, target, headers=None):
        status, out, body = self.api.handle("GET", target, self.auth if headers is None else headers)
        return status, out, body

    def test_health_without_token(self):
        self.assertEqual(self.get("/health", {})[0], 200)

    def test_token_required_and_checked(self):
        self.assertEqual(self.get("/v1/services", {})[0], 401)
        self.assertEqual(self.get("/v1/services", {"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.get("/v1/services", {"X-Api-Key": TOKEN})[0], 200)

    def test_only_get(self):
        status, headers, _ = self.api.handle("POST", "/v1/services", self.auth)
        self.assertEqual((status, headers.get("Allow")), (405, "GET"))

    def test_page_pagination_json(self):
        status, _, body = self.get("/v1/services?page=2&size=3")
        doc = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual([i["id"] for i in doc["data"]["items"]], [4, 5, 6])
        self.assertEqual(doc["meta"], {"page": 2, "size": 3, "total": 7, "pages": 3})
        self.assertEqual(doc["data"]["items"][0]["variables"], [{"k": 4}])
        self.assertEqual(doc["data"]["items"][0]["updated_at"], "2026-11-20T14:00:00+00:00")

    def test_cursor_walks_all_rows_and_link(self):
        ids, target, pages = [], "/v1/services/cursor?limit=3", 0
        while target:
            status, headers, body = self.get(target)
            self.assertEqual(status, 200)
            doc = json.loads(body)
            ids += [i["id"] for i in doc["items"]]
            pages += 1
            target = doc["next"]
            if target:
                self.assertEqual(headers["Link"], f'<{target}>; rel="next"')
        self.assertEqual((ids, pages), (list(range(1, 8)), 3))

    def test_cursor_next_keeps_prefix_and_filters(self):
        _, _, body = self.get("/v1/services/cursor?limit=2&updated_since=2026-11-20T13:00:00Z",
                              {**self.auth, "X-Forwarded-Prefix": "/pv-api"})
        doc = json.loads(body)
        self.assertEqual([i["id"] for i in doc["items"]], [3, 4])
        nxt = urlsplit(doc["next"])
        self.assertEqual(nxt.path, "/pv-api/v1/services/cursor")
        self.assertEqual(parse_qs(nxt.query)["updated_since"], ["2026-11-20T13:00:00Z"])

    def test_bad_cursor_and_parameters(self):
        self.assertEqual(self.get("/v1/services/cursor?cursor=abc")[0], 400)
        self.assertEqual(self.get("/v1/services?size=501")[0], 400)
        self.assertEqual(self.get("/v1/services?updated_since=yesterday")[0], 400)
        self.assertEqual(self.get("/v1/services?format=csv")[0], 400)
        self.assertEqual(self.get("/v1/other")[0], 404)

    def test_xml(self):
        status, headers, body = self.get("/v1/services?size=2&format=xml")
        self.assertEqual(status, 200)
        self.assertIn("application/xml", headers["Content-Type"])
        root = ET.fromstring(body)
        services = root.findall("./items/service")
        self.assertEqual(len(services), 2)
        self.assertEqual(services[0].findtext("code"), "S1")
        self.assertEqual(services[0].findtext("active"), "false")
        self.assertEqual(json.loads(services[0].findtext("variables")), [{"k": 1}])
        self.assertEqual(root.findtext("./meta/total"), "7")

    def test_simulations(self):
        status, headers, _ = self.get("/v1/services?simulate=429")
        self.assertEqual((status, headers["Retry-After"]), (429, "2"))
        self.assertEqual(self.get("/v1/services?simulate=500")[0], 500)
        self.assertEqual(self.get("/v1/services?simulate=slow")[0], 200)
        self.assertEqual(self.slept, [api_mod.SLOW_SECONDS])
        flaky = [self.get("/v1/services?simulate=flaky")[0] for _ in range(4)]
        self.assertEqual(flaky, [429, 200, 429, 200])
        self.assertEqual(self.get("/v1/services?simulate=boom")[0], 400)

    def test_errors_hide_details(self):
        class Broken(FakeStore):
            def page(self, *a):
                raise RuntimeError("password=secret host=10.1.10.40")
        api = api_mod.Api(Broken([]), TOKEN)
        status, _, body = api.handle("GET", "/v1/services", self.auth)
        self.assertEqual(status, 500)
        self.assertNotIn(b"secret", body)
        self.assertNotIn(b"10.1.10.40", body)

    def test_short_token_refused(self):
        with self.assertRaises(ValueError):
            api_mod.Api(FakeStore([]), "short")

    def test_store_sql_is_select_only(self):
        seen = []

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params):
                seen.append(sql)

            def fetchall(self):
                return [(0,)] if "count" in seen[-1] else []

        class Conn:
            def cursor(self):
                return Cur()

            def rollback(self):
                pass

            def close(self):
                pass

        store = api_mod.ServicesStore({}, connect=Conn)
        store.page(T0, 1, 10)
        store.after(None, 5, 10)
        self.assertTrue(all(s.lstrip().upper().startswith("SELECT") for s in seen))
        self.assertTrue(all("public.services" in s for s in seen))


class SetupTest(unittest.TestCase):
    def test_include_inserted_once(self):
        conf = (ROOT / "deploy" / "nginx" / "dwh.conf").read_text().replace(setup.INCLUDE_LINE + "\n\n", "")
        self.assertNotIn("nginx-dwh.d", conf)
        once = setup.with_include(conf)
        self.assertEqual(once.count("include /etc/dwh/nginx-dwh.d/*.conf;"), 1)
        self.assertEqual(setup.with_include(once), once)
        self.assertLess(once.index("nginx-dwh.d"), once.index(setup.ANCHOR))

    def test_template_has_include(self):
        self.assertIn(setup.INCLUDE_LINE, (ROOT / "deploy" / "nginx" / "dwh.conf").read_text())

    def test_include_refuses_unknown_file(self):
        with self.assertRaises(setup.SetupError):
            setup.with_include("server { }")

    def test_env_roundtrip_and_no_line_breaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "e.env"
            values = {"PV_DB_HOST": "h", "PV_DB_PASSWORD": "p=w", "PV_API_TOKEN": TOKEN}
            path.write_text(setup.env_text(values))
            self.assertEqual(setup.read_env(path)["PV_DB_PASSWORD"], "p=w")
        with self.assertRaises(setup.SetupError):
            setup.env_text({"PV_DB_PASSWORD": "a\nb"})

    def test_check_uses_all_scenarios(self):
        api = api_mod.Api(FakeStore(make_rows(120)), TOKEN)

        def fetch(url, token, context, timeout=30):
            parts = urlsplit(url)
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            target = parts.path.replace("/pv-api", "", 1) + ("?" + parts.query if parts.query else "")
            return api.handle("GET", target, headers)

        results = setup.check("https://x/pv-api", TOKEN, None, wait=1, fetch_fn=fetch)
        self.assertTrue(all(ok for ok, _ in results), results)
        self.assertIn("cursor: 120 rows in 3 pages (total 120)", [t for _, t in results])

    def test_check_rejects_application_page(self):
        # nginx without /pv-api/ answers every path with the application page (200, HTML)
        def fetch(url, token, context, timeout=30):
            return 200, {"Content-Type": "text/html"}, b"<!doctype html><html></html>"

        results = setup.check("https://x/pv-api", TOKEN, None, wait=1, fetch_fn=fetch)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0][0])
        self.assertIn("not the test service", results[0][1])

    def test_check_waits_for_new_nginx_workers(self):
        api = api_mod.Api(FakeStore(make_rows(3)), TOKEN)
        calls = {"n": 0}

        def fetch(url, token, context, timeout=30):
            calls["n"] += 1
            if calls["n"] <= 2:  # old workers
                return 200, {"Content-Type": "text/html"}, b"<html></html>"
            parts = urlsplit(url)
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            target = parts.path.replace("/pv-api", "", 1) + ("?" + parts.query if parts.query else "")
            return api.handle("GET", target, headers)

        with mock.patch.object(setup.time, "sleep", lambda s: None):
            results = setup.check("https://x/pv-api", TOKEN, None, wait=5, fetch_fn=fetch)
        self.assertTrue(all(ok for ok, _ in results), results)


if __name__ == "__main__":
    unittest.main()
