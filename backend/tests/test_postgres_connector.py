"""PostgreSQL adapter (I04-01) with a fake psycopg connection; no real server."""

import unittest

import psycopg

from dwh_api import connector_contract as cc
from dwh_api import postgres_connector as pc

SYNTHETIC = "synthetic-pg-Ж-1"
CONFIG = {"host": "db.local", "port": 5432, "database": "crm", "username": "reader"}


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.conn.sql.append((sql, params))
        self.rows = self.conn.answer(sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, readonly="on", objects=None, tables=None):
        self.readonly = readonly
        self.objects = objects or []
        self.tables = tables or {}
        self.sql = []
        self.closed = False
        self.rolled_back = False

    def answer(self, sql, params):
        if "SHOW transaction_read_only" in sql:
            return [{"transaction_read_only": self.readonly}]
        if "server_version" in sql:
            return [{"v": "16.4"}]
        if "LIMIT" in sql:
            return self.objects[: params[0]]
        if "c.relname = %s" in sql:
            t = self.tables.get(params)
            return [{"oid": t["oid"], "relkind": "r", "object_type": "TABLE"}] if t else []
        if "a.attrelid = %s" in sql:
            return next(t["columns"] for t in self.tables.values() if t["oid"] == params[0])
        raise AssertionError(sql)

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class ConnectError(psycopg.OperationalError):
    __slots__ = ("_sqlstate",)

    def __init__(self, message, sqlstate=None):
        self._sqlstate = sqlstate
        super().__init__(message)

    @property
    def sqlstate(self):
        return self._sqlstate


def adapter_with(conn=None, error=None):
    calls = []

    def connect(**kwargs):
        calls.append(kwargs)
        if error:
            raise error
        return conn

    return pc.PostgresAdapter(connect=connect), calls


class SettingsTests(unittest.TestCase):
    def test_read_only_session_and_timeouts(self):
        kwargs = pc.connection_kwargs(CONFIG, cc.SecretValue(SYNTHETIC))
        self.assertIn("default_transaction_read_only=on", kwargs["options"])
        self.assertIn("statement_timeout=", kwargs["options"])
        self.assertEqual(kwargs["connect_timeout"], pc.CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(kwargs["sslmode"], "prefer")

    def test_invalid_settings(self):
        for change in ({"host": ""}, {"database": None}, {"username": " "}, {"port": "x"}, {"port": 70000},
                       {"sslmode": "always"}):
            with self.subTest(change), self.assertRaises(pc.PostgresConnectorError) as ctx:
                pc.connection_kwargs({**CONFIG, **change}, cc.SecretValue(SYNTHETIC))
            self.assertEqual(ctx.exception.code, "CONFIG_INVALID")
        with self.assertRaises(pc.PostgresConnectorError) as ctx:
            pc.connection_kwargs(CONFIG, None)
        self.assertEqual(ctx.exception.code, "NO_CREDENTIAL")


class ClassifyTests(unittest.TestCase):
    def test_sqlstate_wins_over_localised_text(self):
        self.assertEqual(pc.classify_error(ConnectError("ВАЖНО: пользователь не прошёл проверку", "28P01")), "AUTH_FAILED")
        self.assertEqual(pc.classify_error(ConnectError("база данных не существует", "3D000")), "DATABASE_NOT_FOUND")
        self.assertEqual(pc.classify_error(ConnectError("x", "28000")), "ACCESS_DENIED")

    def test_client_side_texts(self):
        cases = {
            'could not translate host name "x" to address': "HOST_NOT_FOUND",
            "connection refused": "CONNECTION_REFUSED",
            "timeout expired": "CONNECT_TIMEOUT",
            "SSL error: certificate verify failed": "SSL_ERROR",
            'FATAL:  database "nope" does not exist': "DATABASE_NOT_FOUND",
            'ВАЖНО:  база данных "nope" не существует': "DATABASE_NOT_FOUND",
            'ВАЖНО:  пользователь "r" не прошёл проверку подлинности (по паролю)': "AUTH_FAILED",
            'FATAL:  role "r" does not exist': "ACCESS_DENIED",
            "No connection could be made because the target machine actively refused it": "CONNECTION_REFUSED",
            "something else": "CONNECT_FAILED",
        }
        for text, code in cases.items():
            with self.subTest(text):
                self.assertEqual(pc.classify_error(ConnectError(text)), code)


class AdapterTests(unittest.TestCase):
    def test_registered(self):
        self.assertIs(cc.get_adapter("POSTGRESQL"), pc.ADAPTER)

    def test_success(self):
        conn = FakeConn()
        adapter, calls = adapter_with(conn)
        result = adapter.test_connection(CONFIG, cc.SecretValue(SYNTHETIC))
        self.assertEqual((result.ok, result.code, result.server_version), (True, "OK", "PostgreSQL 16.4"))
        self.assertEqual(calls[0]["password"], SYNTHETIC)
        self.assertTrue(conn.closed and conn.rolled_back)

    def test_auth_failure_has_fixed_message(self):
        adapter, _ = adapter_with(error=ConnectError(f"password authentication failed for user reader {SYNTHETIC}", "28P01"))
        result = adapter.test_connection(CONFIG, cc.SecretValue(SYNTHETIC))
        self.assertEqual((result.ok, result.code), (False, "AUTH_FAILED"))
        self.assertNotIn(SYNTHETIC, result.message)
        self.assertNotIn("reader", result.message)

    def test_session_must_be_read_only(self):
        conn = FakeConn(readonly="off")
        adapter, _ = adapter_with(conn)
        result = adapter.test_connection(CONFIG, cc.SecretValue(SYNTHETIC))
        self.assertEqual((result.ok, result.code), (False, "NOT_READ_ONLY"))
        self.assertTrue(conn.closed)

    def test_discover_limit_and_catalog_only(self):
        objects = [{"schema": "public", "name": f"t{i}", "object_type": "TABLE"} for i in range(10)]
        conn = FakeConn(objects=objects)
        adapter, _ = adapter_with(conn)
        found = adapter.discover(CONFIG, cc.SecretValue(SYNTHETIC), limit=3)
        self.assertEqual([o.name for o in found], ["t0", "t1", "t2"])
        for sql, _ in conn.sql:
            self.assertRegex(sql, r"pg_catalog|transaction_read_only|server_version")

    def test_describe_reports_found_objects(self):
        cols = [{"ordinal_position": 1, "column_name": "id", "formatted_type": "bigint", "udt_name": "int8",
                 "is_nullable": False, "is_primary_key": True, "is_unique_key": True, "is_identity": False,
                 "is_generated": False, "default_expression": None, "column_comment": None}]
        conn = FakeConn(tables={("public", "items"): {"oid": 7, "columns": cols}})
        adapter, _ = adapter_with(conn)
        version, found = adapter.describe(CONFIG, cc.SecretValue(SYNTHETIC), [("public", "items"), ("public", "gone")])
        self.assertEqual(version, "PostgreSQL 16.4")
        self.assertEqual(list(found), [("public", "items")])
        self.assertEqual(found[("public", "items")]["columns"][0]["column_name"], "id")

    def test_read_not_available(self):
        with self.assertRaises(cc.ConnectorError):
            pc.ADAPTER.read(CONFIG, None, cc.ReadRequest("public", "t", ("id",)))


if __name__ == "__main__":
    unittest.main()
