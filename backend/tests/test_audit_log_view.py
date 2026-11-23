"""I13-05: routes of the journal view (package 029 functions are called, the level is checked, errors mapped)."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DWH_APP_PASSWORD", "unused")
os.environ["DWH_SERVER_LOAD_ENABLED"] = "0"

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from dwh_api import audit_log_view as av  # noqa: E402
from dwh_api import auth  # noqa: E402


class _DbError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.diag = SimpleNamespace(message_primary=message)


def status_of(message: str) -> int:
    try:
        av._raise(_DbError(message))
    except HTTPException as exc:
        return exc.status_code
    raise AssertionError("no HTTP error")


class ErrorMappingTest(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(status_of("Authentication required"), 401)
        self.assertEqual(status_of("Invalid authentication session"), 401)
        self.assertEqual(status_of("Audit log access denied"), 403)
        self.assertEqual(status_of("Password change required"), 403)
        self.assertEqual(status_of("some other failure"), 500)


class RouteTest(unittest.TestCase):
    def setUp(self):
        from dwh_api.main import app
        self.client = TestClient(app)
        self.client.cookies.set(auth.SESSION_COOKIE_NAME, "t" * 40)
        self.calls = []

        def fake_call(request, statement, params):
            self.calls.append((statement, params))
            return {"rows": [], "has_more": False, "next_before_id": None}

        self.patches = [mock.patch.object(av, "_call", side_effect=fake_call)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def as_level(self, level):
        state = SimpleNamespace(user=SimpleNamespace(account_level=level, must_change_password=False),
                                permissions=(), resources=())
        return mock.patch.object(auth, "_load_auth_state_by_hash", return_value=state)

    def test_admin_calls_package_functions_with_filters(self):
        with self.as_level("ADMIN"):
            r = self.client.get("/api/admin/audit/events",
                                params={"event_type": " LOGIN_FAILED ", "actor": "", "before_id": 50, "limit": 20})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(self.client.get("/api/admin/audit/filters").status_code, 200)
            r = self.client.get("/api/admin/audit/secret-operations", params={"source_system_id": 7, "operation_type": "READ"})
            self.assertEqual(r.status_code, 200)
        self.assertIn("app.audit_log_page", self.calls[0][0])
        self.assertEqual(self.calls[0][1], (None, None, "LOGIN_FAILED", None, None, None, 50, 20))
        self.assertIn("app.audit_log_filters", self.calls[1][0])
        self.assertIn("app.secret_journal_page", self.calls[2][0])
        self.assertEqual(self.calls[2][1], (7, None, None, "READ", None, 100))

    def test_user_level_and_bad_input_refused_before_the_database(self):
        with self.as_level("USER"):
            self.assertEqual(self.client.get("/api/admin/audit/events").status_code, 403)
        with self.as_level("SUPERADMIN"):
            self.assertEqual(self.client.get("/api/admin/audit/events", params={"limit": 201}).status_code, 422)
            self.assertEqual(self.client.get("/api/admin/audit/secret-operations",
                                             params={"operation_type": "DELETE"}).status_code, 422)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
