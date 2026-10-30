"""Isolated tests; no actual PostgreSQL, credentials or SecretStore writes."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from dwh_api.secret_source_scope import (
    authorize_source_secret_create,
    source_resource_code,
)


class FakeCursor:
    def __init__(self, mode="on", active=True, allowed=True):
        self.mode, self.active, self.allowed = mode, active, allowed
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        sql = self.calls[-1][0]
        if sql == "SHOW transaction_read_only":
            return {"transaction_read_only": self.mode}
        if "FROM meta.source_system" in sql:
            return None if self.active is None else {"is_active": self.active}
        return None if self.allowed is None else {"allowed": self.allowed}


class FakeConn:
    def __init__(self, **kwargs):
        self.cur = FakeCursor(**kwargs)
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cur

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.state = SimpleNamespace(
            authenticated=True,
            user=SimpleNamespace(user_id=42, is_system=False, must_change_password=False),
            permissions=[],  # Admins may have implicit privileges in PostgreSQL.
        )

    def call(self, conn=None, source_id=1, state=None):
        conn = conn or FakeConn()
        result = authorize_source_secret_create(
            self.state if state is None else state,
            source_id,
            readonly_connection=lambda: conn,
        )
        return result, conn

    def test_canonical_codes(self):
        self.assertEqual(source_resource_code(1), "SOURCE_SYSTEM_1")
        self.assertEqual(source_resource_code(5), "SOURCE_SYSTEM_5")

    def test_reject_bad_ids(self):
        for value in (True, 0, -1, "1", 2**63):
            with self.subTest(value=value), self.assertRaises(ValueError):
                source_resource_code(value)

    def test_scoped_query_uses_server_id_and_static_identifiers(self):
        passed, conn = self.call(source_id=5)
        self.assertTrue(passed)
        sql, params = conn.cur.calls[-1]
        self.assertIn("app.has_scoped_permission", sql)
        self.assertEqual(params, (42, "ADMIN_INTEGRATION_MANAGE", "INTEGRATION_SOURCE_SYSTEM", "SOURCE_SYSTEM_5"))
        self.assertTrue(conn.rolled_back)
        self.assertTrue(conn.closed)

    def test_empty_authstate_permission_list_does_not_override_db(self):
        self.assertTrue(self.call()[0])

    def test_inactive_or_missing_source_denied(self):
        for active in (False, None):
            self.assertFalse(self.call(FakeConn(active=active))[0])

    def test_false_null_permission_denied(self):
        for allowed in (False, None, 1):
            self.assertFalse(self.call(FakeConn(allowed=allowed))[0])

    def test_non_readonly_denied_before_query(self):
        granted, conn = self.call(FakeConn(mode="off"))
        self.assertFalse(granted)
        self.assertEqual(len(conn.cur.calls), 1)

    def test_wrong_identity_denied(self):
        cases = (
            SimpleNamespace(authenticated=False, user=self.state.user),
            SimpleNamespace(authenticated=True, user=None),
            SimpleNamespace(authenticated=True, user=SimpleNamespace(user_id=True, is_system=False, must_change_password=False)),
            SimpleNamespace(authenticated=True, user=SimpleNamespace(user_id=42, is_system=True, must_change_password=False)),
            SimpleNamespace(authenticated=True, user=SimpleNamespace(user_id=42, is_system=False, must_change_password=True)),
        )
        for state in cases:
            with self.subTest(state=state):
                self.assertFalse(self.call(state=state)[0])

    def test_connection_failure_denied(self):
        self.assertFalse(authorize_source_secret_create(self.state, 1, readonly_connection=lambda: (_ for _ in ()).throw(RuntimeError("secret"))))

    def test_query_failure_denied_and_closed(self):
        conn = FakeConn()
        def failed(_sql, _params=None):
            raise RuntimeError("synthetic error")
        conn.cur.execute = failed
        self.assertFalse(self.call(conn)[0])
        self.assertTrue(conn.rolled_back)
        self.assertTrue(conn.closed)


if __name__ == "__main__":
    unittest.main()
