"""I04 API flow: credential READ, connection test, objects, snapshot (fake DB and adapter)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("DWH_APP_PASSWORD", "unit-test-not-used")

from fastapi import HTTPException

from dwh_api import source_connection_admin as sca
from dwh_api.connector_contract import ConnectionTestResult, DiscoveredObject
from dwh_api.postgres_connector import PostgresConnectorError
from dwh_api.secret_file_store import EncryptedFileSecretStore
from dwh_api.secret_store import SecretStore

SYNTHETIC = "synthetic-i04-Ж-7"
REF = "33333333-3333-4333-8333-333333333333"
OP = "44444444-4444-4444-8444-444444444444"


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.conn.calls.append((sql, params))
        if "set_config" in sql:
            self.rows = []
            return
        key = next(k for k in self.conn.handlers if k in sql)
        self.rows = self.conn.handlers[key](params)

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self, **overrides):
        self.calls = []
        self.commits = 0
        self.handlers = {
            "integration_source_connection_get": lambda p: [{
                "source_kind": "DATABASE", "status": "DRAFT", "row_version": 3,
                "connection_config": {"host": "db", "port": 5432, "database": "crm", "username": "r"}}],
            "FROM meta.source_system": lambda p: [{"source_type": "POSTGRESQL"}],
            "secret_read_begin": lambda p: [{"operation_id": OP, "secret_ref": REF, "key_id": "K_T", "row_version": 3}],
            "secret_read_finish": lambda p: [{"ok": True}],
            "record_test": lambda p: [{"status": "READY" if p[3] else "DRAFT", "row_version": 4}],
            "FROM meta.source_object": lambda p: [
                {"source_object_id": 16, "source_schema": "public", "source_object": "asan_pension_requests"}],
            "metadata_capture": lambda p: [{"metadata_capture_id": 9, "objects_captured": 1, "columns_captured": 2}],
            "integration_object_config_get": lambda p: [{"config": {"object": {}, "current": None, "draft": None}}],
        }
        self.handlers.update(overrides)

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def sql_with(self, text):
        return [p for s, p in self.calls if text in s]


class FakeAdapter:
    def __init__(self, ok=True, missing=False):
        self.ok = ok
        self.missing = missing
        self.seen = []

    def test_connection(self, config, credential):
        self.seen.append(credential.reveal())
        return ConnectionTestResult(True, "Connected", "OK", "PostgreSQL 16") if self.ok else \
            ConnectionTestResult(False, "Authentication failed", "AUTH_FAILED")

    def discover(self, config, credential, limit):
        return [DiscoveredObject("public", f"t{i}", "TABLE") for i in range(3)]

    def describe(self, config, credential, objects):
        if self.missing:
            return "PostgreSQL 16", {}
        cols = [{"ordinal_position": 1, "column_name": "id", "formatted_type": "bigint", "udt_name": "int8",
                 "is_nullable": False, "is_primary_key": True}]
        return "PostgreSQL 16", {o: {"relkind": "r", "object_type": "TABLE", "columns": cols} for o in objects}


class I04FlowTests(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("DWH_I02_TEST_TEMP") or None
        self.dir = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.dir.cleanup)
        self.crypto = SecretStore(os.urandom(32))
        EncryptedFileSecretStore(crypto=self.crypto, directory=Path(self.dir.name)).create(secret_ref=REF, value=SYNTHETIC)
        patcher = mock.patch.dict(os.environ, {sca.SECRET_DIRECTORY_ENV: self.dir.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.loader = lambda key_id: self.crypto

    def run_test(self, conn, adapter):
        return sca.test_source_connection(conn, "a" * 64, "req", 5, adapter=adapter, key_loader=self.loader)

    def assert_no_secret(self, conn):
        for sql, params in conn.calls:
            self.assertNotIn(SYNTHETIC, sql)
            self.assertNotIn(SYNTHETIC, repr(params))

    def test_success_reads_journals_and_records(self):
        conn, adapter = Conn(), FakeAdapter()
        result = self.run_test(conn, adapter)
        self.assertEqual((result.ok, result.code, result.status, result.row_version), (True, "OK", "READY", 4))
        self.assertEqual(adapter.seen, [SYNTHETIC])
        self.assertEqual(conn.sql_with("secret_read_finish"), [("a" * 64, 5, OP)])
        self.assertIn("true", [s for s, _ in conn.calls if "secret_read_finish" in s][0])
        self.assertEqual(conn.sql_with("record_test"), [("a" * 64, 5, 3, True, "OK", "PostgreSQL 16")])
        order = [k for s, _ in conn.calls for k in ("connection_get", "read_begin", "read_finish", "record_test") if k in s]
        self.assertEqual(order, ["connection_get", "read_begin", "read_finish", "record_test"])
        self.assert_no_secret(conn)

    def test_failed_test_is_recorded(self):
        conn = Conn()
        result = self.run_test(conn, FakeAdapter(ok=False))
        self.assertEqual((result.ok, result.code, result.status), (False, "AUTH_FAILED", "DRAFT"))
        self.assertEqual(conn.sql_with("record_test")[0][3:5], (False, "AUTH_FAILED"))

    def test_decrypt_failure_finishes_read_as_failed(self):
        conn = Conn()
        with self.assertRaises(HTTPException) as ctx:
            sca.test_source_connection(conn, "a" * 64, "req", 5, adapter=FakeAdapter(),
                                       key_loader=lambda k: SecretStore(os.urandom(32)))
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("false", [s for s, _ in conn.calls if "secret_read_finish" in s][0])
        self.assertEqual(conn.sql_with("record_test"), [])

    def test_non_postgres_source_rejected_before_read(self):
        conn = Conn(**{"FROM meta.source_system": lambda p: [{"source_type": "ORACLE"}]})
        with self.assertRaises(HTTPException) as ctx:
            self.run_test(conn, FakeAdapter())
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(conn.sql_with("read_begin"), [])

    def test_no_connection(self):
        conn = Conn(integration_source_connection_get=lambda p: [])
        with self.assertRaises(HTTPException) as ctx:
            self.run_test(conn, FakeAdapter())
        self.assertEqual(ctx.exception.status_code, 422)

    def test_objects(self):
        conn = Conn()
        result = sca.list_source_objects(conn, "a" * 64, "req", 5, adapter=FakeAdapter(), key_loader=self.loader)
        self.assertEqual([o.name for o in result.objects], ["t0", "t1", "t2"])
        self.assertFalse(result.truncated)
        self.assertEqual(len(conn.sql_with("secret_read_finish")), 1)

    def test_objects_truncated(self):
        with mock.patch.object(sca, "DISCOVER_LIMIT", 2):
            result = sca.list_source_objects(Conn(), "a" * 64, "req", 5, adapter=FakeAdapter(), key_loader=self.loader)
        self.assertEqual(len(result.objects), 2)
        self.assertTrue(result.truncated)

    def test_snapshot_payload(self):
        conn = Conn()
        result = sca.capture_source_snapshot(conn, "a" * 64, "req", 5, adapter=FakeAdapter(), key_loader=self.loader)
        self.assertEqual((result.metadata_capture_id, result.objects_captured, result.columns_captured), (9, 1, 2))
        (params,) = conn.sql_with("metadata_capture")
        payload = params[4].obj
        self.assertEqual(payload[0]["source_object_id"], 16)
        self.assertEqual(payload[0]["columns"][0]["column_name"], "id")
        self.assertEqual(params[2:4], ("crm", "PostgreSQL 16"))
        self.assertEqual(result.objects_with_drift, 0)
        self.assert_no_secret(conn)

    def test_snapshot_missing_object(self):
        conn = Conn()
        with self.assertRaises(HTTPException) as ctx:
            sca.capture_source_snapshot(conn, "a" * 64, "req", 5, adapter=FakeAdapter(missing=True), key_loader=self.loader)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("public.asan_pension_requests", ctx.exception.detail)
        self.assertEqual(conn.sql_with("metadata_capture"), [])

    def test_snapshot_needs_registered_objects(self):
        conn = Conn(**{"FROM meta.source_object": lambda p: []})
        with self.assertRaises(HTTPException) as ctx:
            sca.capture_source_snapshot(conn, "a" * 64, "req", 5, adapter=FakeAdapter(), key_loader=self.loader)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_connector_error_is_422_without_secret(self):
        class Broken(FakeAdapter):
            def discover(self, config, credential, limit):
                raise PostgresConnectorError("CONNECT_TIMEOUT")
        with self.assertRaises(HTTPException) as ctx:
            sca.list_source_objects(Conn(), "a" * 64, "req", 5, adapter=Broken(), key_loader=self.loader)
        self.assertEqual((ctx.exception.status_code, ctx.exception.detail), (422, "Connection timed out"))


if __name__ == "__main__":
    unittest.main()
