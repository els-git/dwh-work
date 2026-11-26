"""I14-07: tools/server/dwh_negative_check.py against a fake API (no network)."""
from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "server"))
import dwh_negative_check as nc  # noqa: E402

SPEC = {"paths": {
    "/api/health": {"get": {}}, "/api/auth/login": {"post": {}}, "/api/auth/me": {"get": {}},
    "/api/admin/users": {"get": {}, "post": {}}, "/api/admin/users/{user_id}": {"post": {}},
    "/api/etl/runs": {"get": {}}, "/api/reports/saved/reports/{report_id}/run": {"post": {}},
}}
GOOD_COOKIE = "dwh_session=tok123; HttpOnly; Path=/; SameSite=strict; Secure"


class FakeApi:
    def __init__(self, **over):
        self.level = over.get("level", "USER")
        self.cookie = over.get("cookie", GOOD_COOKIE)
        self.leak_admin = over.get("leak_admin", False)
        self.keep_session = over.get("keep_session", False)
        self.cors = over.get("cors", False)
        self.lock_after = over.get("lock_after")
        self.ai = over.get("ai", {"status": "REFUSED", "code": "AI_SQL_FORBIDDEN"})
        self.alive = False
        self.failures = 0
        self.calls = []

    def __call__(self, method, path, body=None, cookie=None, headers=None, timeout=30):
        self.calls.append((method, path, cookie))
        extra = {"access-control-allow-origin": "*"} if self.cors and headers and "Origin" in headers else {}
        if path == "/openapi.json":
            return 200, {}, json.dumps(SPEC).encode()
        if path == "/api/auth/login":
            if body["password"] != "right":
                self.failures += 1
                return 401, {}, b"{}"
            if self.lock_after is not None and self.failures >= self.lock_after:
                return 401, {}, b"{}"
            self.alive = True
            return 200, {"set-cookie": self.cookie}, b"{}"
        if path == "/api/auth/logout":
            self.alive = self.keep_session
            return 200, {}, b"{}"
        if path == "/api/auth/me":
            on = bool(cookie) and self.alive
            return 200, extra, json.dumps({"authenticated": on, "user": {"account_level": self.level}}).encode()
        if path == "/api/ai/report":
            return 200, {}, json.dumps(self.ai).encode()
        if method == "OPTIONS":
            return 405, extra, b""
        if not cookie:
            return 401, {}, b"{}"
        if path.startswith("/api/admin/"):
            return (200 if self.leak_admin else 403), {}, b"{}"
        return 200, {}, b"[]"


def run(api, **kw) -> tuple[int, str]:
    args = argparse.Namespace(user="tester", base="http://x", bruteforce=kw.get("bruteforce", 0),
                              ai=kw.get("ai", False), report=None)
    out = io.StringIO()
    with redirect_stdout(out):
        code = nc.run(args, "right", http=api)
    return code, out.getvalue()


class NegativeCheckTest(unittest.TestCase):
    def test_clean_api_passes_and_password_never_printed(self):
        code, out = run(FakeApi())
        self.assertEqual(code, 0, out)
        self.assertIn("NEGATIVE_CHECK_RESULT: PASS", out)
        self.assertIn("test session: 3 /api/admin routes, answered without 401/403: 0", out)
        self.assertNotIn("right", out.replace("rights", ""))
        self.assertNotIn("tok123", out)

    def test_admin_account_stops(self):
        code, out = run(FakeApi(level="ADMIN"))
        self.assertEqual(code, 2)
        self.assertIn("use a test account without administrator level", out)

    def test_findings(self):
        code, out = run(FakeApi(cookie="dwh_session=t; Path=/", leak_admin=True, keep_session=True, cors=True))
        self.assertEqual(code, 1)
        self.assertIn("missing HttpOnly, Secure, SameSite=Strict", out)
        self.assertIn("POST /api/admin/users/{user_id} -> 200", out)
        self.assertIn("after logout the old cookie gives no session: HTTP 200, authenticated", out)
        self.assertIn("B8 CORS: no Access-Control-Allow-Origin for another origin (got *)", out)

    def test_bruteforce(self):
        _, out = run(FakeApi(), bruteforce=3)
        self.assertIn("wrong passwords: 401, 401, 401; then the right one: 200", out)
        self.assertIn("WARN  B1 guessing", out)
        _, out = run(FakeApi(lock_after=3), bruteforce=3)
        self.assertIn("PASS  B1 guessing", out)

    def test_ai(self):
        _, out = run(FakeApi(), ai=True)
        self.assertIn("PASS  B5 assistant report with schema_to_xml: HTTP 200, REFUSED, code AI_SQL_FORBIDDEN", out)
        _, out = run(FakeApi(ai={"status": "OK", "sql": "SELECT schema_to_xml('dm', true, false, '')"}), ai=True)
        self.assertIn("FAIL  B5", out)

    def test_ids_are_zero_and_admin_changes_get_empty_body(self):
        api = FakeApi()
        run(api)
        self.assertIn(("POST", "/api/admin/users/0", "dwh_session=tok123"), api.calls)
        self.assertNotIn(("POST", "/api/reports/saved/reports/0/run", "dwh_session=tok123"), api.calls)


if __name__ == "__main__":
    unittest.main()
